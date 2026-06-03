from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import Assistant, Message
import uuid, os, litellm

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
QDRANT_URL   = os.getenv("QDRANT_URL", "http://qdrant:6333")
os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY", "")

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@router.post("/assistants")
async def create_assistant(data: dict):
    async with AsyncSessionLocal() as session:
        assistant = Assistant(
            id=uuid.uuid4(),
            project_id=data.get("project_id"),
            kb_id=data.get("kb_id"),
            name=data.get("name", "Asistente"),
            system_prompt=data.get("system_prompt", "Eres un asistente util. Responde basandote en el contexto proporcionado."),
            model=data.get("model", "groq/llama-3.1-8b-instant"),
            confidence_threshold=data.get("confidence_threshold", 0.65),
        )
        session.add(assistant)
        await session.commit()
        await session.refresh(assistant)
        return {"id": str(assistant.id), "name": assistant.name}

@router.post("/assistants/{assistant_id}/chat")
async def chat(assistant_id: str, data: dict):
    question   = data.get("message", "")
    session_id = data.get("session_id", str(uuid.uuid4()))

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Assistant).where(Assistant.id == assistant_id))
        assistant = result.scalar_one_or_none()
        if not assistant:
            raise HTTPException(404, "Assistant not found")

        from qdrant_client import QdrantClient
        from fastembed import TextEmbedding

        embed_model  = TextEmbedding("BAAI/bge-small-en-v1.5")
        query_vector = list(embed_model.embed([question]))[0].tolist()

        client     = QdrantClient(url=QDRANT_URL)
        collection = f"kb_{str(assistant.kb_id)}"

        try:
            result_q  = client.query_points(
                collection_name=collection,
                query=query_vector,
                limit=5,
                score_threshold=0.3
            )
            hits      = result_q.points
            context   = "\n\n".join([h.payload["text"] for h in hits])
            avg_score = sum(h.score for h in hits) / len(hits) if hits else 0
        except Exception as e:
            print(f"Qdrant error: {e}")
            context   = ""
            avg_score = 0

        if not context:
            return JSONResponse(content={
                "answer": "No encontre informacion sobre eso. Quieres que te conecte con un agente?",
                "confidence": 0,
                "escalate": True
            })

        prompt = f"Contexto:\n{context}\n\nPregunta: {question}\n\nResponde basandote unicamente en el contexto."

        response = await litellm.acompletion(
            model=assistant.model,
            messages=[
                {"role": "system", "content": assistant.system_prompt},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )

        answer   = response.choices[0].message.content
        escalate = avg_score < float(assistant.confidence_threshold)

        msg = Message(
            id=uuid.uuid4(),
            role="assistant",
            content=answer,
            confidence_score=avg_score,
        )
        db.add(msg)
        await db.commit()

        return JSONResponse(content={
            "answer": answer,
            "confidence": round(avg_score, 3),
            "escalate": escalate,
            "chunks_used": len(hits)
        })
