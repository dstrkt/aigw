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
    clerk_user_id:        Optional[str] = None

class OrgUpdate(BaseModel):
    name:                 Optional[str] = None
    plan_type:            Optional[str] = None
    billing_email:        Optional[str] = None
    quota_tokens_monthly: Optional[int] = None
    is_active:            Optional[bool] = None

@router.post("/organizations")
async def create_organization(data: OrgCreate):
    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(Organization).where(Organization.slug == data.slug)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(400, f"Slug ya existe: {data.slug}")
        org = Organization(
            id=uuid.uuid4(),
            name=data.name,
            slug=data.slug,
            plan_type=data.plan_type,
            billing_email=data.billing_email or None,
            quota_tokens_monthly=data.quota_tokens_monthly,
            stripe_customer_id=None,
            is_active=True,
            clerk_user_id=data.clerk_user_id or None
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
            "clerk_user_id":        org.clerk_user_id,
            "created_at":           str(org.created_at)
        }

@router.get("/organizations")
async def list_organizations(clerk_user_id: Optional[str] = None):
    async with AsyncSessionLocal() as session:
        query = select(Organization).order_by(Organization.created_at.desc())
        if clerk_user_id:
            query = query.where(Organization.clerk_user_id == clerk_user_id)
        result = await session.execute(query)
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
                "clerk_user_id":        o.clerk_user_id,
                "created_at":           str(o.created_at)
            }
            for o in orgs
        ]

@router.get("/organizations/{org_id}")
async def get_organization(org_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            raise HTTPException(404, "Organizacion no encontrada")
        return {
            "id":                   str(org.id),
            "name":                 org.name,
            "slug":                 org.slug,
            "plan_type":            org.plan_type,
            "billing_email":        org.billing_email,
            "quota_tokens_monthly": org.quota_tokens_monthly,
            "is_active":            org.is_active,
            "clerk_user_id":        org.clerk_user_id,
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

@router.delete("/organizations/{org_id}/cascade")
async def delete_organization_cascade(org_id: str, exclude_kb: bool = False, exclude_assistants: bool = False):
    async with AsyncSessionLocal() as session:
        from sqlalchemy import text
        p = {"org_id": org_id}
        await session.execute(text("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE assistant_id IN (SELECT id FROM assistants WHERE project_id IN (SELECT id FROM projects WHERE org_id = CAST(:org_id AS UUID))))").bindparams(**p))
        await session.execute(text("DELETE FROM conversations WHERE assistant_id IN (SELECT id FROM assistants WHERE project_id IN (SELECT id FROM projects WHERE org_id = CAST(:org_id AS UUID)))").bindparams(**p))
        if not exclude_assistants:
            await session.execute(text("DELETE FROM assistants WHERE project_id IN (SELECT id FROM projects WHERE org_id = CAST(:org_id AS UUID))").bindparams(**p))
        if not exclude_kb:
            await session.execute(text("DELETE FROM knowledge_bases WHERE project_id IN (SELECT id FROM projects WHERE org_id = CAST(:org_id AS UUID))").bindparams(**p))
        await session.execute(text("DELETE FROM quota_events WHERE org_id = CAST(:org_id AS UUID)").bindparams(**p))
        await session.execute(text("DELETE FROM usage_logs WHERE org_id = CAST(:org_id AS UUID)").bindparams(**p))
        await session.execute(text("DELETE FROM api_keys WHERE project_id IN (SELECT id FROM projects WHERE org_id = CAST(:org_id AS UUID))").bindparams(**p))
        await session.execute(text("DELETE FROM projects WHERE org_id = CAST(:org_id AS UUID)").bindparams(**p))
        await session.execute(text("DELETE FROM organizations WHERE id = CAST(:org_id AS UUID)").bindparams(**p))
        await session.commit()
        return {"deleted": True, "org_id": org_id}
