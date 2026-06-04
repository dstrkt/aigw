from fastapi import APIRouter, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import KnowledgeBase
from pydantic import BaseModel
from typing import Optional
import uuid, os

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")

engine            = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class KBCreate(BaseModel):
    name:       str
    project_id: Optional[str] = None

@router.post("/knowledge-bases")
async def create_knowledge_base(data: KBCreate):
    async with AsyncSessionLocal() as session:
        kb = KnowledgeBase(
            id=uuid.uuid4(),
            project_id=data.project_id or None,
            name=data.name,
            total_chunks=0
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        return {
            "id":           str(kb.id),
            "name":         kb.name,
            "project_id":   str(kb.project_id) if kb.project_id else None,
            "total_chunks": kb.total_chunks,
            "created_at":   str(kb.created_at)
        }

@router.get("/knowledge-bases")
async def list_knowledge_bases(project_id: Optional[str] = None):
    async with AsyncSessionLocal() as session:
        query = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        if project_id:
            query = query.where(KnowledgeBase.project_id == project_id)
        result = await session.execute(query)
        kbs    = result.scalars().all()
        return [
            {
                "id":              str(kb.id),
                "name":            kb.name,
                "project_id":      str(kb.project_id) if kb.project_id else None,
                "total_chunks":    kb.total_chunks,
                "last_indexed_at": str(kb.last_indexed_at) if kb.last_indexed_at else None,
                "created_at":      str(kb.created_at)
            }
            for kb in kbs
        ]

@router.get("/knowledge-bases/{kb_id}")
async def get_knowledge_base(kb_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb     = result.scalar_one_or_none()
        if not kb:
            raise HTTPException(404, "KB no encontrada")
        return {
            "id":              str(kb.id),
            "name":            kb.name,
            "project_id":      str(kb.project_id) if kb.project_id else None,
            "total_chunks":    kb.total_chunks,
            "last_indexed_at": str(kb.last_indexed_at) if kb.last_indexed_at else None,
            "created_at":      str(kb.created_at)
        }

@router.post("/knowledge-bases/{kb_id}/documents")
async def upload_document(kb_id: str, file: UploadFile = File(...)):
    from app.worker import ingest_document
    import aiofiles, tempfile

    suffix = os.path.splitext(file.filename or "file")[1]
    upload_dir = "/tmp/aigw-uploads"
    os.makedirs(upload_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=upload_dir) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    ingest_document.delay(kb_id, tmp_path, file.filename or "document")

    return {
        "status":    "processing",
        "kb_id":     kb_id,
        "file_name": file.filename,
        "message":   "Documento en cola para indexacion"
    }

@router.get("/knowledge-bases/{kb_id}/status")
async def kb_status(kb_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb     = result.scalar_one_or_none()
        if not kb:
            raise HTTPException(404, "KB no encontrada")
        return {
            "id":              str(kb.id),
            "name":            kb.name,
            "total_chunks":    kb.total_chunks,
            "last_indexed_at": str(kb.last_indexed_at) if kb.last_indexed_at else None,
        }
