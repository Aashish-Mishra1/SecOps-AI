import io
import json
from uuid import uuid4

import jwt
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select

from app.ai.providers import Answer, Claim, MockProvider, get_provider
from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.models import DocumentChunk


def test_health_and_openapi(client):
    assert client.get("/api/v1/health/live").json()["provider"] == "mock"
    assert client.get("/api/v1/health/ready").status_code == 200
    assert "/api/v1/rag/query" in client.get("/openapi.json").json()["paths"]


def test_auth_roles_registration(client, identities):
    assert client.get("/api/v1/events").status_code == 401
    assert client.get("/api/v1/auth/me", headers=identities["analyst"]["headers"]).json()["role"] == "analyst"
    assert (
        client.post(
            "/api/v1/events/import",
            headers=identities["reviewer"]["headers"],
            files={"file": ("x.json", b"[]")},
        ).status_code
        == 403
    )
    payload = {"email": f"{uuid4().hex}@example.com", "password": "local-registration-password"}
    assert client.post("/api/v1/auth/register", json={**payload, "role": "reviewer"}).status_code == 422
    registered = client.post("/api/v1/auth/register", json=payload)
    assert registered.status_code == 201, registered.text
    assert registered.json()["role"] == "analyst"
    assert "password" not in registered.text
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409
    assert client.post("/api/v1/auth/login", json=payload).status_code == 200
    assert (
        client.post("/api/v1/auth/login", json={**payload, "password": "incorrect-password"}).status_code
        == 401
    )
    token = jwt.encode(
        {
            "sub": str(identities["analyst"]["id"]),
            "iat": 1,
            "exp": 2,
            "iss": "secops-ai",
            "aud": "secops-api",
        },
        get_settings().jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_event_import_filters_isolation_atomicity(client, identities):
    headers = identities["analyst"]["headers"]
    event = {
        "source": "test",
        "source_event_id": "1",
        "timestamp": "2026-01-15T09:00:00Z",
        "event_type": "authentication",
        "source_ip": "192.0.2.45",
        "username": "synthetic",
        "outcome": "failure",
    }

    def upload(records):
        return client.post(
            "/api/v1/events/import",
            headers=headers,
            files={"file": ("events.json", json.dumps(records).encode(), "application/json")},
        )

    assert upload([event, event]).json() == {"accepted": 1, "duplicates": 1, "rejected": 0}
    assert upload([event]).json()["duplicates"] == 1
    assert upload([{**event, "source_event_id": "2"}, {"bad": "record"}]).status_code == 422
    result = client.get("/api/v1/events?username=synthetic&limit=1", headers=headers).json()
    assert result["total"] == 1 and len(result["items"]) == 1
    assert client.get("/api/v1/events", headers=identities["outsider"]["headers"]).json()["total"] == 0
    assert client.get("/api/v1/events?limit=101", headers=headers).status_code == 422
    assert client.get("/api/v1/events?start=2026-01-01T00:00:00", headers=headers).status_code == 422
    csv = (
        b"source,source_event_id,timestamp,event_type,outcome\ntest,csv,2026-01-15T09:00:00Z,login,success\n"
    )
    assert (
        client.post("/api/v1/events/import", headers=headers, files={"file": ("events.csv", csv)}).json()[
            "accepted"
        ]
        == 1
    )


@pytest.mark.parametrize("mode", ["semantic", "keyword", "hybrid"])
def test_document_to_retrieval_workflow(client, identities, mode):
    headers = identities["analyst"]["headers"]
    content = b"MFA authentication policy. Review active sessions after repeated login failures. Human approval is required."
    result = client.post("/api/v1/documents", headers=headers, files={"file": ("policy.md", content)})
    assert result.status_code == 201, result.text
    document_id = result.json()["id"]
    duplicate = client.post("/api/v1/documents", headers=headers, files={"file": ("policy.md", content)})
    assert duplicate.json()["id"] == document_id
    body = {"query": "MFA", "mode": mode, "top_k": 1, "document_ids": [document_id]}
    response = client.post("/api/v1/retrieval/search", headers=headers, json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["sources"]) == 1
    assert data["sources"][0]["document_id"] == document_id
    assert data["retrieval_latency_ms"] > 0 and data["input_tokens"] is None
    rag = client.post("/api/v1/rag/query", headers=headers, json=body).json()
    assert "Deterministic mock" in rag["answer"]
    assert rag["sources"][0]["chunk_id"] in rag["answer"]
    assert rag["generation_output_tokens"] is None
    assert (
        client.get(f"/api/v1/documents/{document_id}", headers=identities["outsider"]["headers"]).status_code
        == 404
    )
    assert (
        client.post("/api/v1/retrieval/search", headers=identities["outsider"]["headers"], json=body).json()[
            "sources"
        ]
        == []
    )
    assert (
        client.post("/api/v1/retrieval/search", headers=headers, json={**body, "title": "absent"}).json()[
            "sources"
        ]
        == []
    )
    with session_factory()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
            )
            == 1
        )


def test_failure_status_retry_no_fallback(client, identities, app):
    class BrokenProvider(MockProvider):
        def embed(self, texts, *, query=False):
            raise AppError(502, "provider_error", "Synthetic provider failure")

    headers = identities["analyst"]["headers"]
    app.dependency_overrides[get_provider] = lambda: BrokenProvider()
    try:
        result = client.post(
            "/api/v1/documents", headers=headers, files={"file": ("retry.txt", b"Session review policy")}
        )
        assert result.status_code == 502
        failed = client.get("/api/v1/documents", headers=headers).json()["items"][0]
        assert failed["ingestion_status"] == "failed"
    finally:
        app.dependency_overrides.clear()
    retry = client.post(
        "/api/v1/documents", headers=headers, files={"file": ("retry.txt", b"Session review policy")}
    )
    assert retry.json()["id"] == failed["id"] and retry.json()["ingestion_status"] == "ready"


def test_invalid_citation_and_empty_knowledge(client, identities, app):
    headers = identities["analyst"]["headers"]
    empty = client.post("/api/v1/rag/query", headers=headers, json={"query": "absent", "document_ids": []})
    assert "Insufficient knowledge" in empty.json()["answer"]
    client.post("/api/v1/documents", headers=headers, files={"file": ("policy.txt", b"MFA review policy")})

    class InvalidCitationProvider(MockProvider):
        def answer(self, question, sources):
            return Answer([Claim(text="Unverified claim", chunk_ids=[str(uuid4())])])

    app.dependency_overrides[get_provider] = lambda: InvalidCitationProvider()
    try:
        result = client.post("/api/v1/rag/query", headers=headers, json={"query": "MFA"})
        assert result.status_code == 502 and result.json()["error"]["code"] == "invalid_citation"
    finally:
        app.dependency_overrides.clear()


def test_upload_limits_and_safe_errors(client, identities):
    headers = identities["analyst"]["headers"]
    response = client.post("/api/v1/documents", headers=headers, files={"file": ("bad.pdf", b"not a PDF")})
    assert response.status_code == 422
    assert set(response.json()["error"]) == {"code", "message", "request_id"}
    assert (
        client.post(
            "/api/v1/documents", headers=headers, files={"file": ("big.txt", b"x" * (10 * 1024 * 1024 + 1))}
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/v1/events/import",
            headers=headers,
            files={"file": ("big.csv", b"x" * (5 * 1024 * 1024 + 1))},
        ).status_code
        == 413
    )


def test_text_pdf_page_citation(client, identities):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 40 700 Td (MFA session policy requires human approval.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    headers = identities["analyst"]["headers"]
    uploaded = client.post(
        "/api/v1/documents", headers=headers, files={"file": ("policy.pdf", buffer.getvalue())}
    )
    assert uploaded.status_code == 201, uploaded.text
    result = client.post(
        "/api/v1/retrieval/search",
        headers=headers,
        json={"query": "MFA", "document_ids": [uploaded.json()["id"]], "content_type": "application/pdf"},
    )
    assert result.json()["sources"][0]["page"] == 1


def test_alert_scope_and_role(client, identities):
    payload = {"title": "Synthetic alert", "description": "Review", "event_time": "2026-01-15T09:00:00Z"}
    assert (
        client.post("/api/v1/alerts", headers=identities["reviewer"]["headers"], json=payload).status_code
        == 403
    )
    created = client.post("/api/v1/alerts", headers=identities["analyst"]["headers"], json=payload)
    assert created.status_code == 201
    assert (
        client.get(
            "/api/v1/alerts/" + created.json()["id"], headers=identities["outsider"]["headers"]
        ).status_code
        == 404
    )
