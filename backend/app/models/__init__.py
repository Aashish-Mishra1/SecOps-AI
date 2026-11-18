from datetime import datetime, timezone
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Scoped(Identity):
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)


class Organization(Identity, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))


class User(Scoped, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('analyst','reviewer')"),
        UniqueConstraint("organization_id", "id"),
    )
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))


class Alert(Scoped, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alert_org_time", "organization_id", "event_time"),
        UniqueConstraint("organization_id", "id"),
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_ip: Mapped[str | None] = mapped_column(String(45))
    username: Mapped[str | None] = mapped_column(String(200))
    severity_hint: Mapped[str] = mapped_column(String(20), default="info")
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecurityEvent(Scoped, Base):
    __tablename__ = "security_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "source", "source_event_id"),
        Index("ix_event_org_user_time", "organization_id", "username", "timestamp"),
        Index("ix_event_org_ip_time", "organization_id", "source_ip", "timestamp"),
    )
    source: Mapped[str] = mapped_column(String(100))
    source_event_id: Mapped[str] = mapped_column(String(200))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str] = mapped_column(String(100))
    source_ip: Mapped[str | None] = mapped_column(String(45))
    username: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(30))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Document(Scoped, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "sha256"),
        UniqueConstraint("organization_id", "id"),
        CheckConstraint("ingestion_status IN ('processing','ready','failed')"),
    )
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(100))
    ingestion_status: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(100))
    embedding_model: Mapped[str] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Scoped, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        ForeignKeyConstraint(
            ["organization_id", "document_id"], ["documents.organization_id", "documents.id"]
        ),
        Index("ix_chunk_search", "search_vector", postgresql_using="gin"),
        Index("ix_chunk_org_model", "organization_id", "embedding_model"),
    )
    document_id: Mapped[UUID]
    chunk_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
    embedding_model: Mapped[str] = mapped_column(String(200))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    document: Mapped[Document] = relationship(back_populates="chunks")


class ThreatIndicator(Scoped, Base):
    __tablename__ = "threat_indicators"
    __table_args__ = (UniqueConstraint("organization_id", "indicator_type", "value", "source"),)
    indicator_type: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(String(255))
    classification: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Scoped, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Investigation(Scoped, Base):
    __tablename__ = "investigations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(["organization_id", "alert_id"], ["alerts.organization_id", "alerts.id"]),
        ForeignKeyConstraint(["organization_id", "initiated_by"], ["users.organization_id", "users.id"]),
        Index("ix_investigation_org_status", "organization_id", "status"),
        UniqueConstraint("organization_id", "request_id"),
        CheckConstraint("attempt >= 0 AND attempt <= 3", name="investigations_attempt_check"),
        CheckConstraint(
            "status IN ('queued','running','interrupted','awaiting_review','approved','rejected','failed')",
            name="investigations_status_check",
        ),
    )
    alert_id: Mapped[UUID]
    initiated_by: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(30), default="queued")
    graph_thread_id: Mapped[str] = mapped_column(String(100), unique=True)
    request_id: Mapped[UUID] = mapped_column(default=uuid4)
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    model: Mapped[str] = mapped_column(String(200), default="mock:agents-v1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_ms: Mapped[float] = mapped_column(default=0.0)
    tool_count: Mapped[int] = mapped_column(default=0)
    llm_count: Mapped[int] = mapped_column(default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Evidence(Scoped, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"], ["investigations.organization_id", "investigations.id"]
        ),
    )
    investigation_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str] = mapped_column(String(255))
    excerpt: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Identity, Base):
    __tablename__ = "agent_runs"
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("investigations.id"), index=True)
    agent: Mapped[str] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None]
    model: Mapped[str] = mapped_column(String(200))
    trace_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[float | None]
    prompt_version: Mapped[str] = mapped_column(String(30), default="agents-v1")
    result: Mapped[dict | None] = mapped_column(JSONB)


class ToolRun(Identity, Base):
    __tablename__ = "tool_runs"
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("investigations.id"), index=True)
    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    latency_ms: Mapped[float | None]
    error_code: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCall(Identity, Base):
    __tablename__ = "llm_calls"
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("investigations.id"), index=True)
    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    total_tokens: Mapped[int | None]
    latency_ms: Mapped[float | None]
    time_to_first_token_ms: Mapped[float | None]
    cost_usd: Mapped[float | None]
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Review(Identity, Base):
    __tablename__ = "reviews"
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("investigations.id"), unique=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
