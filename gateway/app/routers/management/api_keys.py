from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from app.models import ApiKey
from pydantic import BaseModel
from typing import Optional
from passlib.hash import bcrypt
import uuid, os, secrets, redis.asyncio as aioredis

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")
engine       = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class ApiKeyCreate(BaseModel):
    label:                str
    project_id:           Optional[str] = None
    quota_tokens_monthly: Optional[int] = 100_000
    rate_limit_rpm:       int = 60
    allowed_models:       Optional[list] = None

@router.post("/api-keys")
async def create_api_key(data: ApiKeyCreate):
    raw_key = f"sk-gw-{secrets.token_hex(24)}"
    prefix  = raw_key[:12]
    hashed  = bcrypt.hash(raw_key)

    async with AsyncSessionLocal() as session:
        api_key = ApiKey(
            id=uuid.uuid4(),
            project_id=data.project_id,
            key_hash=hashed,
            key_prefix=prefix,
            label=data.label,
            quota_tokens_monthly=data.quota_tokens_monthly,
            rate_limit_rpm=data.rate_limit_rpm,
            is_active=True
        )
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)

    # Guardar límite en Redis para acceso rápido
    r = aioredis.from_url(REDIS_URL)
    await r.set(f"quota:limit:{str(api_key.id)}", data.quota_tokens_monthly or 100_000)
    await r.close()

    return {
        "id":                   str(api_key.id),
        "full_key":             raw_key,  # Solo se muestra una vez
        "key_prefix":           prefix,
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
        result  = await session.execute(query)
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
        result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
        key = result.scalar_one_or_none()
        if not key:
            raise HTTPException(404, "API Key no encontrada")
        await session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(is_active=False)
        )
        await session.commit()

    # Invalidar cache en Redis
    r = aioredis.from_url(REDIS_URL)
    await r.delete(f"auth:key:{key_id}")
    await r.close()

    return {"revoked": True, "id": key_id}

@router.get("/api-keys/{key_id}/usage")
async def get_key_usage(key_id: str):
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    r     = aioredis.from_url(REDIS_URL)
    used  = int(await r.get(f"quota:used:{key_id}:{month}") or 0)
    limit = int(await r.get(f"quota:limit:{key_id}") or 100_000)
    await r.close()
    return {
        "key_id":    key_id,
        "month":     month,
        "used":      used,
        "limit":     limit,
        "remaining": max(0, limit - used),
        "pct":       round((used / limit) * 100, 2) if limit else 0
    }
