from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import JSONResponse
import litellm, redis.asyncio as aioredis, os, time, hashlib, asyncio
import uuid as uuid_lib
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update
from app.models import ApiKey, UsageLog

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")

engine            = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY", "")

async def _log_usage(key_id, model_requested, model_used, provider,
                     input_tokens, output_tokens, total_tokens):
    try:
        async with AsyncSessionLocal() as session:
            log = UsageLog(
                id=uuid_lib.uuid4(),
                api_key_id=key_id,
                model_requested=model_requested,
                model_used=model_used,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                status="success"
            )
            session.add(log)
            await session.commit()
    except Exception as e:
        print(f"Error logging usage: {e}")

async def validate_api_key(raw_key: str):
    key_hash  = hashlib.sha256(raw_key.encode()).hexdigest()
    cache_key = f"auth:key:{key_hash}"

    r = aioredis.from_url(REDIS_URL)
    cached = await r.get(cache_key)
    await r.close()

    if cached:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.id == cached.decode())
            )
            return result.scalar_one_or_none()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.is_active == True
            )
        )
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise HTTPException(status_code=401, detail="Invalid or revoked API Key")

        await session.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key.id)
            .values(last_used_at=datetime.utcnow())
        )
        await session.commit()

    r = aioredis.from_url(REDIS_URL)
    await r.setex(cache_key, 300, str(api_key.id))
    if api_key.quota_tokens_monthly:
        await r.set(f"quota:limit:{str(api_key.id)}", api_key.quota_tokens_monthly)
    await r.close()

    return api_key

@router.post("/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(...)):
    raw_key = authorization.replace("Bearer ", "").strip()
    api_key = await validate_api_key(raw_key)
    key_id  = str(api_key.id)

    r = aioredis.from_url(REDIS_URL)

    # Rate limit
    minute   = int(time.time() // 60)
    rate_key = f"rate:{key_id}:{minute}"
    count    = await r.incr(rate_key)
    await r.expire(rate_key, 60)
    if count > (api_key.rate_limit_rpm or 60):
        await r.close()
        raise HTTPException(status_code=429, detail="Rate limit exceeded",
            headers={"Retry-After": "60"})

    # Quota check
    month = datetime.now().strftime("%Y-%m")
    used  = int(await r.get(f"quota:used:{key_id}:{month}") or 0)
    limit = int(await r.get(f"quota:limit:{key_id}") or 500_000)

    if used >= limit:
        await r.close()
        raise HTTPException(status_code=429, detail="Quota exceeded",
            headers={"X-Quota-Used": str(used), "X-Quota-Limit": str(limit),
                     "X-Quota-Remaining": "0"})

    if used >= limit * 0.95:
        await r.publish("quota:events", f"critical:{key_id}:{used}:{limit}")
    elif used >= limit * 0.80:
        await r.publish("quota:events", f"warning:{key_id}:{used}:{limit}")

    await r.close()

    body  = await request.json()
    model = body.get("model", "groq/llama-3.1-8b-instant")

    # Fallback chain
    fallback_models = [model, "groq/llama-3.1-8b-instant", "groq/llama3-8b-8192"]
    seen            = set()
    fallback_models = [m for m in fallback_models if not (m in seen or seen.add(m))]

    response   = None
    last_error = None
    model_used = model

    for attempt_model in fallback_models:
        provider = attempt_model.split("/")[0]
        r_cb     = aioredis.from_url(REDIS_URL)
        cb_open  = await r_cb.get(f"circuit:{provider}")
        await r_cb.close()

        if cb_open:
            continue

        try:
            response   = await litellm.acompletion(
                model=attempt_model,
                messages=body["messages"],
                stream=False,
                timeout=30,
            )
            model_used = attempt_model
            break
        except Exception as e:
            last_error = e
            if any(x in str(e).lower() for x in ["503", "502", "unavailable", "overloaded"]):
                r_cb = aioredis.from_url(REDIS_URL)
                await r_cb.setex(f"circuit:{provider}", 60, "1")
                await r_cb.close()
            continue

    if response is None:
        raise HTTPException(status_code=503,
            detail=f"All model providers failed. Last error: {str(last_error)}")

    total_tokens   = 0
    input_tokens   = 0
    output_tokens  = 0
    if hasattr(response, "usage") and response.usage:
        total_tokens  = response.usage.total_tokens or 0
        input_tokens  = response.usage.prompt_tokens or 0
        output_tokens = response.usage.completion_tokens or 0

    r = aioredis.from_url(REDIS_URL)
    await r.incrby(f"quota:used:{key_id}:{month}", total_tokens)
    await r.close()

    # Log en PostgreSQL (no bloquea)
    asyncio.create_task(_log_usage(
        key_id=key_id,
        model_requested=model,
        model_used=model_used,
        provider=model_used.split("/")[0],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    ))

    remaining = max(0, limit - used - total_tokens)

    return JSONResponse(
        content=response.model_dump(),
        headers={
            "X-Gateway-Model-Used": model_used,
            "X-Gateway-Provider":   model_used.split("/")[0],
            "X-Quota-Used":         str(used + total_tokens),
            "X-Quota-Limit":        str(limit),
            "X-Quota-Remaining":    str(remaining),
        }
    )
