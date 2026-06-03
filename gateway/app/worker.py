from celery import Celery
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import os, uuid, asyncio

REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")
DATABASE_URL = os.getenv("DATABASE_URL")
QDRANT_URL   = os.getenv("QDRANT_URL", "http://qdrant:6333")

app = Celery("worker", broker=REDIS_URL, backend=REDIS_URL)

@app.task
def ingest_document(kb_id: str, file_path: str, filename: str):
    asyncio.run(_ingest(kb_id, file_path, filename))

async def _ingest(kb_id: str, file_path: str, filename: str):
    text   = extract_text(file_path, filename)
    chunks = chunk_text(text, chunk_size=512, overlap=50)

    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct, VectorParams, Distance
    from fastembed import TextEmbedding

    client     = QdrantClient(url=QDRANT_URL)
    collection = f"kb_{kb_id}"

    # Crear colección si no existe
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE)
        )

    # Generar embeddings
    model      = TextEmbedding("BAAI/bge-small-en-v1.5")
    embeddings = list(model.embed(chunks))

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=embeddings[i].tolist(),
            payload={
                "text":        chunks[i],
                "kb_id":       kb_id,
                "filename":    filename,
                "chunk_index": i
            }
        )
        for i in range(len(chunks))
    ]

    # Upsert usando batch
    client.upsert(
        collection_name=collection,
        points=points,
        wait=True
    )

    # Actualizar BD
    engine            = create_async_engine(DATABASE_URL)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        from sqlalchemy import update
        from app.models import KnowledgeBase
        from datetime import datetime, timezone
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(
                total_chunks=len(chunks),
                last_indexed_at=datetime.now(timezone.utc)
            )
        )
        await session.commit()

    print(f"✅ Indexados {len(chunks)} chunks de {filename}")

def extract_text(file_path: str, filename: str) -> str:
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(file_path)
        return result.text_content
    except Exception as e:
        print(f"MarkItDown falló ({e}), usando extractor básico")
        if filename.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            return " ".join(page.extract_text() or "" for page in reader.pages)
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list:
    words  = text.split()
    chunks = []
    i      = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks
@app.task
def process_quota_event(event_type: str, key_id: str, used: int, limit: int):
    asyncio.run(_process_quota_event(event_type, key_id, used, limit))

async def _process_quota_event(event_type: str, key_id: str, used: int, limit: int):
    from app.models import QuotaEvent
    import uuid
    from datetime import datetime, timezone

    engine_local = create_async_engine(DATABASE_URL)
    AsyncSession2 = sessionmaker(engine_local, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSession2() as session:
        event = QuotaEvent(
            id=uuid.uuid4(),
            api_key_id=key_id,
            event_type=event_type,
            tokens_consumed=used,
            tokens_limit=limit,
            notified_at=datetime.now(timezone.utc)
        )
        session.add(event)
        await session.commit()

    print(f"Quota event: {event_type} key={key_id} {used}/{limit} ({round(used/limit*100)}%)")
