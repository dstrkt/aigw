from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from app.models import ApiKey
from pydantic import BaseModel
from typing import Optional
import uuid, os, hashlib, secrets

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class ApiKeyCreate(BaseModel):
    label:                str
    project_id:           Optional[str] = None
    quota_tokens_monthly: Optional[int] = 100_000
    rate_limit_rpm:       Optional[int] = 60

@router.post("/api-keys")
async def create_api_key(data: ApiKeyCreate):
    raw_key  = "sk-gw-" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    prefix   = raw_key[:10]

    async with AsyncSessionLocal() as session:
        api_key = ApiKey(
            id=uuid.uuid4(),
            project_id=data.project_id or None,
            key_hash=key_hash,
            key_prefix=prefix,
            label=data.label,
            quota_tokens_monthly=data.quota_tokens_monthly,
            rate_limit_rpm=data.rate_limit_rpm,
            is_active=True
        )
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)
        return {
            "id":                   str(api_key.id),
            "full_key":             raw_key,
            "key_prefix":           api_key.key_prefix,
            "label":                api_key.label,
            "project_id":           str(api_key.project_id) if api_key.project_id else None,
            "quota_tokens_monthly": api_key.quota_tokens_monthly,
            "rate_limit_rpm":       api_key.rate_limit_rpm,
            "is_active":            api_key.is_active,
            "created_at":           str(api_key.created_at)
        }

@router.get("/api-keys")
async def list_api_keys(project_id: Optional[str] = None):
    async with AsyncSessionLocal() as session:
        query = select(ApiKey).order_by(ApiKey.created_at.desc())
        if project_id:
            query = query.where(ApiKey.project_id == project_id)
        result   = await session.execute(query)
        api_keys = result.scalars().all()
        return [
            {
                "id":                   str(k.id),
                "key_prefix":           k.key_prefix,
                "label":                k.label,
                "project_id":           str(k.project_id) if k.project_id else None,
                "quota_tokens_monthly": k.quota_tokens_monthly,
                "rate_limit_rpm":       k.rate_limit_rpm,
                "is_active":            k.is_active,
                "last_used_at":         str(k.last_used_at) if k.last_used_at else None,
                "created_at":           str(k.created_at)
            }
            for k in api_keys
        ]

@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(is_active=False)
        )
        await session.commit()
        return {"revoked": True, "id": key_id}
