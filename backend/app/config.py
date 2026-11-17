from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    jwt_secret: SecretStr
    jwt_expire_minutes: int = 30
    cors_origins: str = "http://localhost:5173"
    db_pool_size: int = Field(5, ge=1, le=20)
    db_max_overflow: int = Field(5, ge=0, le=20)
    registration_enabled: bool = False
    demo_organization_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    demo_password: SecretStr | None = None
    ai_provider: Literal["mock", "gemini"] = "mock"
    allow_external_ai: bool = False
    gemini_api_key: SecretStr | None = None
    gemini_model: str = ""
    gemini_embedding_model: str = ""
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    google_cloud_location: str = ""
    embedding_dim: int = 768
    chunk_words: int = 400
    chunk_overlap_words: int = 60
    retrieval_top_k: int = 5
    provider_timeout_seconds: int = Field(30, ge=1, le=60)
    max_document_chars: int = Field(1_000_000, ge=1, le=1_000_000)
    max_pdf_pages: int = Field(200, ge=1, le=200)
    requests_per_minute: int = Field(60, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_configuration(self) -> "Settings":
        secret = self.jwt_secret.get_secret_value()
        if len(secret) < 32 or "replace" in secret.lower():
            raise ValueError("JWT_SECRET must be a randomly generated secret of at least 32 characters")
        if self.embedding_dim != 768:
            raise ValueError("EMBEDDING_DIM requires a schema migration; current schema uses 768")
        if not 0 <= self.chunk_overlap_words < self.chunk_words <= 600:
            raise ValueError("Require 0 <= chunk overlap < chunk words <= 600")
        if not 1 <= self.retrieval_top_k <= 20 or not 1 <= self.jwt_expire_minutes <= 120:
            raise ValueError("Invalid retrieval or token limit")
        if self.app_env == "production" and self.registration_enabled:
            raise ValueError("Demo registration must be disabled in production")
        if self.ai_provider == "gemini":
            if not self.allow_external_ai or not self.gemini_model or not self.gemini_embedding_model:
                raise ValueError("Gemini requires explicit external AI opt-in and both model IDs")
            if self.google_genai_use_vertexai:
                if not self.google_cloud_project or not self.google_cloud_location:
                    raise ValueError("Vertex requires project and location plus local ADC credentials")
            elif not self.gemini_api_key:
                raise ValueError("Gemini requires local API credentials")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
