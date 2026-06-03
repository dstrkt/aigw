from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import chat, knowledge_base, assistants
from app.routers.management import organizations, projects, api_keys, usage

app = FastAPI(title="AI Gateway", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router,           prefix="/v1")
app.include_router(knowledge_base.router, prefix="/v1")
app.include_router(assistants.router,     prefix="/v1")

app.include_router(organizations.router,  prefix="/mgmt")
app.include_router(projects.router,       prefix="/mgmt")
app.include_router(api_keys.router,       prefix="/mgmt")
app.include_router(usage.router,          prefix="/mgmt")

@app.get("/health")
async def health():
    return {"status": "ok"}
