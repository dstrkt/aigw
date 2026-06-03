from fastapi import APIRouter, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import KnowledgeBase
import uuid, os

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@router.post("/knowledge-bases")
async def create_knowledge_base(data: dict):
    async with AsyncSessionLocal() as session:
        kb = KnowledgeBase(
            id=uuid.uuid4(),
            project_id=data.get("project_id"),
            name=data.get("name", "Mi base de conocimiento"),
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        return {"id": str(kb.id), "name": kb.name, "status": "created"}

@router.post("/knowledge-bases/{kb_id}/documents")
async def upload_document(kb_id: str, file: UploadFile = File(...)):
    content = await file.read()

    # Guardar en volumen compartido con worker
    os.makedirs("/tmp/uploads", exist_ok=True)
    path = f"/tmp/uploads/{file.filename}"
    with open(path, "wb") as f:
        f.write(content)

    # Encolar tarea Celery
    from app.worker import ingest_document
    task = ingest_document.delay(kb_id, path, file.filename)
    return {"task_id": task.id, "filename": file.filename, "status": "processing"}

@router.get("/knowledge-bases/{kb_id}/status")
async def kb_status(kb_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        kb = result.scalar_one_or_none()
        if not kb:
            raise HTTPException(404, "Knowledge base not found")
        return {
            "id":             str(kb.id),
            "name":           kb.name,
            "total_chunks":   kb.total_chunks,
            "last_indexed_at": str(kb.last_indexed_at)
        }
