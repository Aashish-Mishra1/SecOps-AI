import pytest
from pydantic import ValidationError

from app.config import Settings


def settings(**kwargs):
    return Settings(
        _env_file=None, database_url="postgresql+psycopg://localhost/secops", jwt_secret="a" * 64, **kwargs
    )


def test_configuration_guards():
    assert settings().ai_provider == "mock"
    with pytest.raises(ValidationError):
        settings(embedding_dim=128)
    with pytest.raises(ValidationError):
        settings(ai_provider="gemini")
    with pytest.raises(ValidationError):
        settings(chunk_words=50, chunk_overlap_words=60)
    with pytest.raises(ValidationError):
        settings(app_env="production", registration_enabled=True)
