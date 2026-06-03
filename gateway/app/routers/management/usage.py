from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func, text
from app.models import UsageLog
import redis.asyncio as aioredis
import os, json, asyncio
from datetime import datetime

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")

engine            = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@router.get("/usage/realtime")
async def usage_realtime(request: Request):
    async def event_stream():
        while True:
            if await request.is_disconnected():
                break
            try:
                r     = aioredis.from_url(REDIS_URL)
                month = datetime.now().strftime("%Y-%m")

                # Obtener todas las keys de quota del mes
                keys  = await r.keys(f"quota:used:*:{month}")
                total_used = 0
                for key in keys:
                    val = await r.get(key)
                    if val:
                        total_used += int(val)

                # RPM actual (último minuto)
                minute   = int(asyncio.get_event_loop().time() // 60)
                rpm_keys = await r.keys(f"rate:*:{minute}")
                total_rpm = 0
                for key in rpm_keys:
                    val = await r.get(key)
                    if val:
                        total_rpm += int(val)

                await r.close()

                data = {
                    "tokens_used": total_used,
                    "rpm":         total_rpm,
                    "timestamp":   datetime.now().isoformat()
                }
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        }
    )

@router.get("/usage/summary")
async def usage_summary():
    r     = aioredis.from_url(REDIS_URL)
    month = datetime.now().strftime("%Y-%m")
    keys  = await r.keys(f"quota:used:*:{month}")

    total_used = 0
    per_key    = {}
    for key in keys:
        val = await r.get(key)
        if val:
            tokens       = int(val)
            total_used  += tokens
            key_id       = key.decode().split(":")[2]
            per_key[key_id] = tokens

    await r.close()

    return {
        "month":      month,
        "total_used": total_used,
        "per_key":    per_key,
        "timestamp":  datetime.now().isoformat()
    }

@router.get("/usage/export")
async def usage_export():
    import csv, io
    from fastapi.responses import StreamingResponse as SR

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UsageLog).order_by(UsageLog.created_at.desc()).limit(10000)
        )
        logs = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "request_id", "model_requested", "model_used",
        "provider", "input_tokens", "output_tokens", "total_tokens",
        "latency_ms", "status", "created_at"
    ])
    for log in logs:
        writer.writerow([
            str(log.id), log.request_id, log.model_requested, log.model_used,
            log.provider, log.input_tokens, log.output_tokens, log.total_tokens,
            log.latency_ms, log.status, str(log.created_at)
        ])

    output.seek(0)
    month = datetime.now().strftime("%Y-%m")

    return SR(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=usage-{month}.csv"}
    )
