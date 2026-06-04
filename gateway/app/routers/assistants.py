from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import Assistant, KnowledgeBase, Conversation, Message
from pydantic import BaseModel
from typing import Optional
import uuid, os
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from fastembed import TextEmbedding

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
QDRANT_URL   = os.getenv("QDRANT_URL", "http://qdrant:6333")

engine            = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

embedding_model = TextEmbedding("BAAI/bge-small-en-v1.5")
qdrant          = QdrantClient(url=QDRANT_URL)

class AssistantCreate(BaseModel):
    name:                 str
    kb_id:                str
    project_id:           Optional[str] = None
    model:                Optional[str] = "groq/llama-3.1-8b-instant"
    system_prompt:        Optional[str] = "Eres un asistente de soporte util."
    confidence_threshold: Optional[float] = 0.65
    escalation_email:     Optional[str] = None

class ChatRequest(BaseModel):
    message:    str
    session_id: Optional[str] = "default"

@router.post("/assistants")
async def create_assistant(data: AssistantCreate):
    async with AsyncSessionLocal() as session:
        assistant = Assistant(
            id=uuid.uuid4(),
            project_id=data.project_id or None,
            kb_id=data.kb_id,
            name=data.name,
            model=data.model,
            system_prompt=data.system_prompt,
            confidence_threshold=data.confidence_threshold,
            escalation_email=data.escalation_email,
            is_active=True
        )
        session.add(assistant)
        await session.commit()
        await session.refresh(assistant)
        return {
            "id":                   str(assistant.id),
            "name":                 assistant.name,
            "kb_id":                str(assistant.kb_id),
            "project_id":           str(assistant.project_id) if assistant.project_id else None,
            "model":                assistant.model,
            "confidence_threshold": assistant.confidence_threshold,
            "is_active":            assistant.is_active,
            "created_at":           str(assistant.created_at)
        }

@router.get("/assistants")
async def list_assistants(project_id: Optional[str] = None):
    async with AsyncSessionLocal() as session:
        query = select(Assistant).order_by(Assistant.created_at.desc())
        if project_id:
            query = query.where(Assistant.project_id == project_id)
        result     = await session.execute(query)
        assistants = result.scalars().all()
        return [
            {
                "id":                   str(a.id),
                "name":                 a.name,
                "kb_id":                str(a.kb_id) if a.kb_id else None,
                "project_id":           str(a.project_id) if a.project_id else None,
                "model":                a.model,
                "confidence_threshold": a.confidence_threshold,
                "is_active":            a.is_active,
                "created_at":           str(a.created_at)
            }
            for a in assistants
        ]

@router.get("/assistants/{assistant_id}")
async def get_assistant(assistant_id: str):
    async with AsyncSessionLocal() as session:
        result    = await session.execute(select(Assistant).where(Assistant.id == assistant_id))
        assistant = result.scalar_one_or_none()
        if not assistant:
            raise HTTPException(404, "Asistente no encontrado")
        return {
            "id":                   str(assistant.id),
            "name":                 assistant.name,
            "kb_id":                str(assistant.kb_id) if assistant.kb_id else None,
            "project_id":           str(assistant.project_id) if assistant.project_id else None,
            "model":                assistant.model,
            "system_prompt":        assistant.system_prompt,
            "confidence_threshold": assistant.confidence_threshold,
            "escalation_email":     assistant.escalation_email,
            "is_active":            assistant.is_active,
            "created_at":           str(assistant.created_at)
        }

@router.post("/assistants/{assistant_id}/chat")
async def chat_with_assistant(assistant_id: str, req: ChatRequest):
    import litellm

    async with AsyncSessionLocal() as session:
        result    = await session.execute(select(Assistant).where(Assistant.id == assistant_id))
        assistant = result.scalar_one_or_none()
        if not assistant:
            raise HTTPException(404, "Asistente no encontrado")

        # Cargar historial
        conv_result = await session.execute(
            select(Conversation).where(
                Conversation.assistant_id == assistant.id,
                Conversation.session_id   == req.session_id
            )
        )
        conversation = conv_result.scalar_one_or_none()

        if not conversation:
            conversation = Conversation(
                id=uuid.uuid4(),
                assistant_id=assistant.id,
                session_id=req.session_id,
                channel="web"
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)

        # Cargar mensajes anteriores
        msgs_result = await session.execute(
            select(Message).where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc()).limit(10)
        )
        prev_messages = list(reversed(msgs_result.scalars().all()))

        # Generar embedding de la pregunta
        embeddings   = list(embedding_model.embed([req.message]))
        query_vector = embeddings[0].tolist()

        # Buscar en Qdrant
        collection_name = f"kb_{str(assistant.kb_id).replace('-', '_')}"
        context_text    = ""
        confidence      = 0.0

        try:
            search_results = qdrant.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=5,
                score_threshold=0.5
            )
            if search_results:
                context_text = "\n\n".join([r.payload.get("text", "") for r in search_results])
                confidence   = float(search_results[0].score)
        except Exception:
            pass

        # Construir mensajes
        history = [
            {"role": m.role, "content": m.content}
            for m in prev_messages
        ]

        system = assistant.system_prompt or "Eres un asistente de soporte util."
        if context_text:
            system += f"\n\nCONTEXTO RELEVANTE:\n{context_text[:2000]}"

        messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": req.message}]

        # Llamar al modelo
        try:
            response = await litellm.acompletion(
                model=assistant.model or "groq/llama-3.1-8b-instant",
                messages=messages,
                temperature=0.3,
                max_tokens=500,
                stream=False
            )
            answer = response.choices[0].message.content
        except Exception as e:
            answer = f"Lo siento, ocurrio un error al procesar tu consulta: {str(e)}"

        # Guardar mensajes
        user_msg = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role="user",
            content=req.message
        )
        assistant_msg = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            confidence_score=confidence
        )
        session.add(user_msg)
        session.add(assistant_msg)
        await session.commit()

        # Evaluar si escalar
        should_escalate = confidence < (assistant.confidence_threshold or 0.65) and confidence > 0

        return {
            "answer":           answer,
            "confidence":       confidence,
            "should_escalate":  should_escalate,
            "conversation_id":  str(conversation.id),
            "session_id":       req.session_id
        }

@router.post("/conversations/{conversation_id}/escalate")
async def escalate_conversation(conversation_id: str):
    async with AsyncSessionLocal() as session:
        from sqlalchemy import update
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(escalated=True)
        )
        await session.commit()
        return {"escalated": True, "conversation_id": conversation_id}

from pydantic import BaseModel as PydanticBase

class AssistantUpdate(PydanticBase):
    name:                 str | None = None
    model:                str | None = None
    system_prompt:        str | None = None
    confidence_threshold: float | None = None
    escalation_email:     str | None = None

@router.patch("/assistants/{assistant_id}")
async def update_assistant(assistant_id: str, data: AssistantUpdate):
    from sqlalchemy import update as sql_update
    values = {k: v for k, v in data.model_dump().items() if v is not None}
    if not values:
        raise HTTPException(400, "No hay campos para actualizar")
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(Assistant).where(Assistant.id == assistant_id).values(**values)
        )
        await session.commit()
    return {"updated": True, "id": assistant_id}

class AssistantUpdate(BaseModel):
    name:                 str | None = None
    model:                str | None = None
    system_prompt:        str | None = None
    confidence_threshold: float | None = None
    escalation_email:     str | None = None

@router.patch("/assistants/{assistant_id}")
async def update_assistant(assistant_id: str, data: AssistantUpdate):
    from sqlalchemy import update as sql_update
    values = {k: v for k, v in data.model_dump().items() if v is not None}
    if not values:
        raise HTTPException(400, "No hay campos para actualizar")
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(Assistant).where(Assistant.id == assistant_id).values(**values)
        )
        await session.commit()
    return {"updated": True, "id": assistant_id}

@router.get("/assistants/{assistant_id}/widget.js")
async def get_widget_js(assistant_id: str):
    from fastapi.responses import Response
    js = f"""
(function() {{
  var ASSISTANT_ID = '{assistant_id}';
  var GATEWAY_URL  = window.location.protocol + '//' + window.location.hostname + (window.location.port ? ':' + window.location.port : '');

  // Estilos
  var style = document.createElement('style');
  style.textContent = `
    #aigw-btn {{
      position: fixed; bottom: 24px; right: 24px; z-index: 9999;
      width: 56px; height: 56px; border-radius: 50%;
      background: #2563eb; color: white; border: none;
      font-size: 24px; cursor: pointer;
      box-shadow: 0 4px 12px rgba(37,99,235,0.4);
      transition: transform 0.2s;
    }}
    #aigw-btn:hover {{ transform: scale(1.1); }}
    #aigw-container {{
      position: fixed; bottom: 92px; right: 24px; z-index: 9998;
      width: 380px; height: 520px; border-radius: 16px;
      background: #0f172a; box-shadow: 0 8px 32px rgba(0,0,0,0.4);
      display: none; flex-direction: column; overflow: hidden;
      font-family: system-ui, sans-serif;
      border: 1px solid #334155;
    }}
    #aigw-header {{
      padding: 1rem 1.25rem; background: #1e293b;
      display: flex; justify-content: space-between; align-items: center;
      border-bottom: 1px solid #334155;
    }}
    #aigw-title {{ color: #f1f5f9; font-size: 0.95rem; font-weight: 600; }}
    #aigw-close {{
      background: none; border: none; color: #64748b;
      cursor: pointer; font-size: 1.2rem; padding: 0;
    }}
    #aigw-messages {{
      flex: 1; overflow-y: auto; padding: 1rem;
      display: flex; flex-direction: column; gap: 0.75rem;
    }}
    .aigw-msg {{
      max-width: 85%; padding: 0.65rem 0.9rem;
      border-radius: 12px; font-size: 0.85rem; line-height: 1.5;
    }}
    .aigw-msg.user {{
      background: #2563eb; color: white;
      align-self: flex-end; border-bottom-right-radius: 4px;
    }}
    .aigw-msg.assistant {{
      background: #1e293b; color: #e2e8f0;
      align-self: flex-start; border-bottom-left-radius: 4px;
      border: 1px solid #334155;
    }}
    .aigw-msg.system {{
      background: transparent; color: #64748b;
      align-self: center; font-size: 0.75rem; text-align: center;
    }}
    #aigw-input-area {{
      padding: 0.75rem; background: #1e293b;
      border-top: 1px solid #334155;
      display: flex; gap: 0.5rem;
    }}
    #aigw-input {{
      flex: 1; padding: 0.6rem 0.9rem; border-radius: 8px;
      border: 1px solid #334155; background: #0f172a;
      color: #f1f5f9; font-size: 0.85rem; outline: none;
    }}
    #aigw-send {{
      padding: 0.6rem 1rem; background: #2563eb; color: white;
      border: none; border-radius: 8px; cursor: pointer;
      font-size: 0.85rem; font-weight: 600;
    }}
    #aigw-send:disabled {{ background: #334155; cursor: not-allowed; }}
    .aigw-escalate {{
      background: #059669; color: white; border: none;
      padding: 0.5rem 1rem; border-radius: 8px;
      cursor: pointer; font-size: 0.8rem; margin-top: 0.5rem;
    }}
  `;
  document.head.appendChild(style);

  // HTML
  var sessionId = 'sess_' + Math.random().toString(36).slice(2);
  var container = document.createElement('div');
  container.id  = 'aigw-container';
  container.innerHTML = `
    <div id="aigw-header">
      <span id="aigw-title">Asistente de Soporte</span>
      <button id="aigw-close">✕</button>
    </div>
    <div id="aigw-messages">
      <div class="aigw-msg system">Hola! Como puedo ayudarte hoy?</div>
    </div>
    <div id="aigw-input-area">
      <input id="aigw-input" type="text" placeholder="Escribe tu pregunta..." />
      <button id="aigw-send">Enviar</button>
    </div>
  `;

  var btn = document.createElement('button');
  btn.id          = 'aigw-btn';
  btn.textContent = '💬';

  document.body.appendChild(container);
  document.body.appendChild(btn);

  var open = false;
  btn.addEventListener('click', function() {{
    open = !open;
    container.style.display = open ? 'flex' : 'none';
    btn.textContent = open ? '✕' : '💬';
    if (open) document.getElementById('aigw-input').focus();
  }});

  document.getElementById('aigw-close').addEventListener('click', function() {{
    open = false;
    container.style.display = 'none';
    btn.textContent = '💬';
  }});

  function addMessage(role, text) {{
    var msgs = document.getElementById('aigw-messages');
    var div  = document.createElement('div');
    div.className = 'aigw-msg ' + role;
    div.textContent = text;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  }}

  function addEscalateButton(conversationId) {{
    var msgs = document.getElementById('aigw-messages');
    var btn  = document.createElement('button');
    btn.className   = 'aigw-escalate';
    btn.textContent = '👤 Hablar con un agente humano';
    btn.onclick = function() {{
      fetch(GATEWAY_URL + '/v1/conversations/' + conversationId + '/escalate', {{method: 'POST'}});
      btn.textContent = '✓ Agente notificado - en breve te contactaran';
      btn.disabled = true;
    }};
    msgs.appendChild(btn);
    msgs.scrollTop = msgs.scrollHeight;
  }}

  async function sendMessage() {{
    var input = document.getElementById('aigw-input');
    var send  = document.getElementById('aigw-send');
    var text  = input.value.trim();
    if (!text) return;

    input.value = '';
    send.disabled = true;
    addMessage('user', text);

    var thinking = addMessage('assistant', '...');

    try {{
      var res = await fetch(GATEWAY_URL + '/v1/assistants/' + ASSISTANT_ID + '/chat', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ message: text, session_id: sessionId }})
      }});
      var data = await res.json();
      thinking.textContent = data.answer || 'Lo siento, ocurrio un error.';
      if (data.should_escalate) {{
        addEscalateButton(data.conversation_id);
      }}
    }} catch(e) {{
      thinking.textContent = 'Error de conexion. Intenta de nuevo.';
    }}

    send.disabled = false;
    input.focus();
  }}

  document.getElementById('aigw-send').addEventListener('click', sendMessage);
  document.getElementById('aigw-input').addEventListener('keypress', function(e) {{
    if (e.key === 'Enter') sendMessage();
  }});
}})();
""".strip()
    return Response(content=js, media_type="application/javascript")
