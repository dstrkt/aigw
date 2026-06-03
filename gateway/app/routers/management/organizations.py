from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from app.models import Organization
from pydantic import BaseModel
from typing import Optional
import uuid, os

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class OrgCreate(BaseModel):
    name:                 str
    slug:                 str
    plan_type:            str = "starter"
    billing_email:        Optional[str] = None
    quota_tokens_monthly: Optional[int] = 100_000

class OrgUpdate(BaseModel):
    name:                 Optional[str] = None
    plan_type:            Optional[str] = None
    billing_email:        Optional[str] = None
    quota_tokens_monthly: Optional[int] = None
    is_active:            Optional[bool] = None

@router.post("/organizations")
async def create_organization(data: OrgCreate):
    async with AsyncSessionLocal() as session:
        # Verificar slug único
        existing = await session.execute(select(Organization).where(Organization.slug == data.slug))
        if existing.scalar_one_or_none():
            raise HTTPException(400, f"Slug '{data.slug}' ya existe")
        org = Organization(
            id=uuid.uuid4(),
            name=data.name,
            slug=data.slug,
            plan_type=data.plan_type,
            billing_email=data.billing_email,
            quota_tokens_monthly=data.quota_tokens_monthly,
            is_active=True
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return {
            "id":                   str(org.id),
            "name":                 org.name,
            "slug":                 org.slug,
            "plan_type":            org.plan_type,
            "billing_email":        org.billing_email,
            "quota_tokens_monthly": org.quota_tokens_monthly,
            "is_active":            org.is_active,
            "created_at":           str(org.created_at)
        }

@router.get("/organizations")
async def list_organizations():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Organization).order_by(Organization.created_at.desc()))
        orgs = result.scalars().all()
        return [
            {
                "id":                   str(o.id),
                "name":                 o.name,
                "slug":                 o.slug,
                "plan_type":            o.plan_type,
                "billing_email":        o.billing_email,
                "quota_tokens_monthly": o.quota_tokens_monthly,
                "is_active":            o.is_active,
                "created_at":           str(o.created_at)
            }
            for o in orgs
        ]

@router.get("/organizations/{org_id}")
async def get_organization(org_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Organization).where(Organization.id == org_id))
        org = result.scalar_one_or_none()
        if not org:
            raise HTTPException(404, "Organización no encontrada")
        return {
            "id":                   str(org.id),
            "name":                 org.name,
            "slug":                 org.slug,
            "plan_type":            org.plan_type,
            "billing_email":        org.billing_email,
            "quota_tokens_monthly": org.quota_tokens_monthly,
            "is_active":            org.is_active,
            "created_at":           str(org.created_at)
        }

@router.patch("/organizations/{org_id}")
async def update_organization(org_id: str, data: OrgUpdate):
    async with AsyncSessionLocal() as session:
        values = {k: v for k, v in data.model_dump().items() if v is not None}
        if not values:
            raise HTTPException(400, "No hay campos para actualizar")
        await session.execute(
            update(Organization).where(Organization.id == org_id).values(**values)
        )
        await session.commit()
        return {"updated": True, "id": org_id}

@router.delete("/organizations/{org_id}")
async def delete_organization(org_id: str):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Organization).where(Organization.id == org_id).values(is_active=False)
        )
        await session.commit()
        return {"deleted": True, "id": org_id}
