from sqlalchemy import Column, String, Boolean, BigInteger, Integer, Float, Text, Enum, ForeignKey, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()

class Organization(Base):
    __tablename__ = "organizations"
    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name                 = Column(String(255), nullable=False)
    slug                 = Column(String(100), unique=True, nullable=False)
    plan_type            = Column(Enum("starter","growth","business","enterprise", name="plan_type"))
    billing_email        = Column(String(255))
    quota_tokens_monthly = Column(BigInteger)
    stripe_customer_id   = Column(String(100))
    created_at           = Column(TIMESTAMP(timezone=True), server_default=func.now())
    is_active            = Column(Boolean, default=True)

class Project(Base):
    __tablename__ = "projects"
    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id        = Column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    name          = Column(String(255))
    default_model = Column(String(100), default="groq/llama-3.1-8b-instant")
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now())

class ApiKey(Base):
    __tablename__ = "api_keys"
    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id           = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
    key_hash             = Column(String(64), unique=True, nullable=False)
    key_prefix           = Column(String(10))
    label                = Column(String(100))
    quota_tokens_monthly = Column(BigInteger)
    rate_limit_rpm       = Column(Integer, default=60)
    is_active            = Column(Boolean, default=True)
    last_used_at         = Column(TIMESTAMP(timezone=True))
    expires_at           = Column(TIMESTAMP(timezone=True))
    created_at           = Column(TIMESTAMP(timezone=True), server_default=func.now())

class UsageLog(Base):
    __tablename__ = "usage_logs"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id      = Column(String(100), unique=True)
    api_key_id      = Column(UUID(as_uuid=True), ForeignKey("api_keys.id"))
    project_id      = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
    org_id          = Column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    model_requested = Column(String(100))
    model_used      = Column(String(100))
    provider        = Column(String(50))
    input_tokens    = Column(Integer)
    output_tokens   = Column(Integer)
    total_tokens    = Column(Integer)
    latency_ms      = Column(Integer)
    status          = Column(Enum("success","error","quota_exceeded","rate_limited", name="request_status"))
    retry_count     = Column(Integer, default=0)
    created_at      = Column(TIMESTAMP(timezone=True), server_default=func.now())

class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id      = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
    name            = Column(String(255))
    embedding_model = Column(String(100), default="text-embedding-3-small")
    chunk_size      = Column(Integer, default=512)
    chunk_overlap   = Column(Integer, default=50)
    total_chunks    = Column(Integer, default=0)
    last_indexed_at = Column(TIMESTAMP(timezone=True))
    created_at      = Column(TIMESTAMP(timezone=True), server_default=func.now())

class Assistant(Base):
    __tablename__ = "assistants"
    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id           = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
    kb_id                = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id"))
    name                 = Column(String(255))
    system_prompt        = Column(Text)
    model                = Column(String(100), default="groq/llama-3.1-8b-instant")
    confidence_threshold = Column(Float, default=0.65)
    escalation_email     = Column(String(255))
    widget_config        = Column(JSONB)
    is_active            = Column(Boolean, default=True)
    created_at           = Column(TIMESTAMP(timezone=True), server_default=func.now())

class Conversation(Base):
    __tablename__ = "conversations"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assistant_id    = Column(UUID(as_uuid=True), ForeignKey("assistants.id"))
    session_id      = Column(String(100))
    channel         = Column(Enum("web","whatsapp","slack","email","api", name="channel_type"))
    user_identifier = Column(String(255))
    escalated       = Column(Boolean, default=False)
    escalation_reason = Column(Text)
    ticket_id       = Column(String(100))
    started_at      = Column(TIMESTAMP(timezone=True), server_default=func.now())
    ended_at        = Column(TIMESTAMP(timezone=True))

class Message(Base):
    __tablename__ = "messages"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"))
    role            = Column(Enum("user","assistant","system", name="message_role"))
    content         = Column(Text)
    input_tokens    = Column(Integer)
    output_tokens   = Column(Integer)
    confidence_score = Column(Float)
    created_at      = Column(TIMESTAMP(timezone=True), server_default=func.now())
