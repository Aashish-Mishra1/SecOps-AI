from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.genai import types

from app.ai.providers import GeminiProvider
from app.config import Settings
from app.errors import AppError


def provider():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/test",
        jwt_secret="a" * 64,
        ai_provider="gemini",
        allow_external_ai=True,
        gemini_api_key="synthetic-test-key",
        gemini_model="configured-generation-model",
        gemini_embedding_model="configured-embedding-model",
    )
    return GeminiProvider(settings)


def test_sdk_contract_embedding_dimensions_and_task(monkeypatch):
    adapter = provider()
    client = MagicMock()
    client.__enter__.return_value = client
    client.models.embed_content.return_value = types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=[1.0] * 768)]
    )
    monkeypatch.setattr(adapter, "client", lambda: client)
    result = adapter.embed(["synthetic MFA policy"], query=True)
    config = client.models.embed_content.call_args.kwargs["config"]
    assert config.output_dimensionality == 768 and config.task_type == "RETRIEVAL_QUERY"
    assert sum(v * v for v in result.vectors[0]) == pytest.approx(1)
    assert result.input_tokens is None
    client.models.embed_content.return_value.embeddings[0].statistics = types.ContentEmbeddingStatistics(
        token_count=12, truncated=False
    )
    assert adapter.embed(["synthetic MFA policy"]).input_tokens == 12
    client.models.embed_content.return_value.embeddings[0].statistics.truncated = True
    with pytest.raises(AppError, match="no fallback"):
        adapter.embed(["synthetic MFA policy"])


def test_sdk_generation_usage_and_invalid_output(monkeypatch):
    adapter = provider()
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(adapter, "client", lambda: client)
    client.models.generate_content.return_value = SimpleNamespace(
        text='{"claims":[]}',
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=42, candidates_token_count=3
        ),
    )
    result = adapter.answer("synthetic question", [])
    assert result.input_tokens == 42 and result.output_tokens == 3
    client.models.generate_content.return_value.text = "invalid JSON"
    with pytest.raises(AppError, match="no fallback"):
        adapter.answer("synthetic question", [])


def test_sdk_invalid_embedding_no_fallback(monkeypatch):
    adapter = provider()
    client = MagicMock()
    client.__enter__.return_value = client
    client.models.embed_content.return_value = types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=[1.0] * 4)]
    )
    monkeypatch.setattr(adapter, "client", lambda: client)
    with pytest.raises(AppError, match="no fallback"):
        adapter.embed(["synthetic"])
