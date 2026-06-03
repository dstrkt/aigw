from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from app.models import Project
from pydantic import BaseModel
from typing import Optional
import uuid, os

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class ProjectCreate(BaseModel):
    name:          str
    org_id:        Optional[str] = None
    default_model: str = "groq/llama-3.1-8b-instant"

@router.post("/projects")
async def create_project(data: ProjectCreate):
    async with AsyncSessionLocal() as session:
        project = Project(
            id=uuid.uuid4(),
            org_id=data.org_id if data.org_id else None,
            #org_id=data.org_id,
            name=data.name,
            default_model=data.default_model
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return {
            "id":            str(project.id),
            "org_id":        str(project.org_id) if project.org_id else None,
            "name":          project.name,
            "default_model": project.default_model,
            "created_at":    str(project.created_at)
        }

@router.get("/projects")
async def list_projects(org_id: Optional[str] = None):
    async with AsyncSessionLocal() as session:
        query = select(Project).order_by(Project.created_at.desc())
        if org_id:
            query = query.where(Project.org_id == org_id)
        result = await session.execute(query)
        projects = result.scalars().all()
        return [
            {
                "id":            str(p.id),
                "org_id":        str(p.org_id) if p.org_id else None,
                "name":          p.name,
                "default_model": p.default_model,
                "created_at":    str(p.created_at)
            }
            for p in projects
        ]

@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "Proyecto no encontrado")
        return {
            "id":            str(project.id),
            "org_id":        str(project.org_id) if project.org_id else None,
            "name":          project.name,
            "default_model": project.default_model,
            "created_at":    str(project.created_at)
        }

@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "Proyecto no encontrado")
        await session.delete(project)
        await session.commit()
        return {"deleted": True, "id": project_id}
