from celery import Celery
import os, httpx, redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import ApiKey, Organization, QuotaEvent
import uuid
from datetime import datetime

REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")
DATABASE_URL = os.getenv("DATABASE_URL")

engine            = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

celery_app = Celery("quota", broker=REDIS_URL, backend=REDIS_URL)

@celery_app.task
def send_quota_webhook(event_type: str, key_id: str, used: int, limit: int):
    import asyncio
    asyncio.run(_send_webhook(event_type, key_id, used, limit))

async def _send_webhook(event_type: str, key_id: str, used: int, limit: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
        api_key = result.scalar_one_or_none()
        if not api_key:
            return

        # Registrar evento en BD
        event = QuotaEvent(
            id=uuid.uuid4(),
            api_key_id=api_key.id,
            org_id=api_key.project_id,
            event_type=event_type,
            tokens_consumed=used,
            tokens_limit=limit,
            notified_at=datetime.utcnow()
        )
        session.add(event)
        await session.commit()

        # Enviar webhook si está configurado
        # (en producción vendría de la config del tenant)
        webhook_url = os.getenv("QUOTA_WEBHOOK_URL")
        if webhook_url:
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json={
                    "event":     event_type,
                    "key_id":    key_id,
                    "used":      used,
                    "limit":     limit,
                    "pct":       round((used / limit) * 100, 2),
                    "timestamp": datetime.utcnow().isoformat()
                }, timeout=5)
