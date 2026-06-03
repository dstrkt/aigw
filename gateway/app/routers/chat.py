from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import JSONResponse
import litellm, redis.asyncio as aioredis, os, time
from datetime import datetime

router = APIRouter()
r = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))

# Configurar Groq
os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY", "")

@router.post("/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(...)):
    api_key = authorization.replace("Bearer ", "")

    # --- Rate limit ---
    minute = int(time.time() // 60)
    rate_key = f"rate:{api_key}:{minute}"
    count = await r.incr(rate_key)
    await r.expire(rate_key, 60)
    if count > 60:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # --- Quota check ---
    month = datetime.now().strftime("%Y-%m")
    used  = int(await r.get(f"quota:used:{api_key}:{month}") or 0)
    limit = int(await r.get(f"quota:limit:{api_key}") or 500_000)
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail="Quota exceeded",
            headers={
                "X-Quota-Used":      str(used),
                "X-Quota-Limit":     str(limit),
                "X-Quota-Remaining": "0"
            }
        )

    body = await request.json()
    model = body.get("model", "groq/llama-3.1-8b-instant")

    # --- Llamada al modelo ---
    try:
        response = await litellm.acompletion(
            model=model,
            messages=body["messages"],
            stream=False,
            timeout=30,
            fallbacks=[]
        )
    except litellm.exceptions.AuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid API key for model provider")
    except litellm.exceptions.RateLimitError:
        raise HTTPException(status_code=429, detail="Model provider rate limit reached")
    except litellm.exceptions.ServiceUnavailableError:
        raise HTTPException(status_code=503, detail="Model provider unavailable")
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    # --- Registrar tokens en Redis ---
    total_tokens = 0
    if hasattr(response, "usage") and response.usage:
        total_tokens = response.usage.total_tokens or 0

    await r.incrby(f"quota:used:{api_key}:{month}", total_tokens)

    remaining = max(0, limit - used - total_tokens)

    return JSONResponse(
        content=response.model_dump(),
        headers={
            "X-Gateway-Model-Used": model,
            "X-Gateway-Provider":   "groq",
            "X-Quota-Used":         str(used + total_tokens),
            "X-Quota-Limit":        str(limit),
            "X-Quota-Remaining":    str(remaining),
        }
    )
