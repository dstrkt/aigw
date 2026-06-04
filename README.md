# AI Gateway as a Service

Plataforma multi-modelo con control de tokens, multi-tenancy y Mesa de Ayuda RAG.

## Stack

- **API:** FastAPI + LiteLLM
- **Portal:** Next.js 16 + Clerk
- **BD:** PostgreSQL 16 + Redis 7 + Qdrant
- **Proxy:** Nginx

## Inicio rapido

```bash
git clone https://github.com/dstrkt/aigw.git
cd aigw
cp .env.example .env  # editar con tus keys
docker compose up --build
```

Inicializar tablas:
```bash
docker compose exec gateway python -c "
import asyncio
from app.database import engine
from app.models import Base
async def create():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
asyncio.run(create())
"
```

Abrir `http://localhost` y completar el onboarding.

## Variables de entorno

```env
GROQ_API_KEY=gsk_...
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
OPENAI_API_KEY=sk-...          # opcional
ANTHROPIC_API_KEY=sk-ant-...   # opcional
GEMINI_API_KEY=AIza...         # opcional
```

## Uso

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost/v1",
    api_key="sk-gw-tu_api_key"
)

response = client.chat.completions.create(
    model="groq/llama-3.1-8b-instant",
    messages=[{"role": "user", "content": "Hola"}]
)
```

## Endpoints

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| POST | /v1/chat/completions | Chat multi-modelo |
| POST | /v1/embeddings | Embeddings |
| GET | /v1/models | Modelos disponibles |
| POST | /v1/assistants/{id}/chat | Chat RAG |
| POST | /v1/knowledge-bases/{id}/documents | Subir documento |
| GET | /mgmt/usage/summary | Resumen de uso |
| GET | /mgmt/usage/export | Export CSV |
| GET | /health | Health check |

## Proveedores

| Proveedor | Variable |
|-----------|----------|
| Groq (default) | GROQ_API_KEY |
| OpenAI | OPENAI_API_KEY |
| Anthropic | ANTHROPIC_API_KEY |
| Google Gemini | GEMINI_API_KEY |

Fallback automatico entre proveedores si uno falla.

## Autor

Andres Bernal - [@dstrkt](https://github.com/dstrkt)
