from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Credentials(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: str) -> str:
        return value.lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    email: str
    role: Literal["analyst", "reviewer"]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class EventIn(StrictModel):
    source: str = Field(min_length=1, max_length=100)
    source_event_id: str = Field(min_length=1, max_length=200)
    timestamp: datetime
    event_type: str = Field(min_length=1, max_length=100)
    source_ip: str | None = Field(default=None, max_length=45)
    username: str | None = Field(default=None, max_length=200)
    outcome: Literal["success", "failure", "unknown"]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        return value.astimezone(timezone.utc)

    @field_validator("source_ip")
    @classmethod
    def normalize_ip(cls, value: str | None) -> str | None:
        return str(ip_address(value)) if value else None


class EventOut(BaseModel):
    id: UUID
    source: str
    source_event_id: str
    timestamp: datetime
    event_type: str
    source_ip: str | None
    username: str | None
    outcome: str
    metadata: dict[str, Any]


class ImportOut(BaseModel):
    accepted: int
    duplicates: int
    rejected: int = 0


class AlertIn(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    event_time: datetime
    source_ip: str | None = Field(default=None, max_length=45)
    username: str | None = Field(default=None, max_length=200)
    severity_hint: Literal["info", "low", "medium", "high", "critical"] = "info"

    _utc = field_validator("event_time")(EventIn.utc_timestamp.__func__)
    _ip = field_validator("source_ip")(EventIn.normalize_ip.__func__)


class AlertOut(AlertIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    filename: str
    ingestion_status: str
    error_code: str | None
    embedding_model: str
    input_tokens: int | None
    created_at: datetime


class RetrievalRequest(StrictModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    document_ids: list[UUID] | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=200)
    content_type: Literal["text/plain", "text/markdown", "application/pdf"] | None = None


class Citation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    title: str
    page: int | None
    char_start: int
    char_end: int
    excerpt: str
    score: float


class RetrievalOut(BaseModel):
    sources: list[Citation]
    provider: str
    embedding_model: str
    retrieval_latency_ms: float
    input_tokens: int | None


class RagOut(RetrievalOut):
    answer: str
    generation_model: str
    generation_latency_ms: float
    generation_input_tokens: int | None
    generation_output_tokens: int | None
