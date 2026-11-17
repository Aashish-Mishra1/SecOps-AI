import io
import json
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from app.ai.providers import MockProvider
from app.errors import AppError
from app.services.documents import chunk_text, extract
from app.services.events import parse_events
from app.services.retrieval import reciprocal_rank_fusion


def test_mock_deterministic_and_normalized():
    provider = MockProvider()
    first = provider.embed(["MFA session review", "!"])
    assert first == provider.embed(["MFA session review", "!"])
    assert first.input_tokens is None
    assert len(first.vectors[0]) == 768
    assert sum(v * v for v in first.vectors[0]) == pytest.approx(1)


def test_chunks_preserve_overlap_and_page_offsets():
    value = "one two three four five six seven"
    chunks = chunk_text([(2, value)], 4, 1)
    assert [c.content for c in chunks] == ["one two three four", "four five six seven"]
    assert all(c.page == 2 and value[c.start : c.end] == c.content for c in chunks)
    with pytest.raises(ValueError):
        chunk_text([(None, value)], 4, 4)


def test_rrf():
    a, b = uuid4(), uuid4()
    score = reciprocal_rank_fusion([[a, b], [b]])
    assert score[a] == pytest.approx(1 / 61)
    assert score[b] == pytest.approx(1 / 62 + 1 / 61)


def test_parser_atomic_error_row():
    row = {
        "source": "idp",
        "source_event_id": "1",
        "timestamp": "2026-01-15T09:00:00+01:00",
        "event_type": "login",
        "source_ip": "2001:0db8::1",
        "outcome": "failure",
    }
    event = parse_events(json.dumps([row]).encode(), "file.json")[0]
    assert event.timestamp.hour == 8
    assert event.source_ip == "2001:db8::1"
    with pytest.raises(AppError, match="Invalid record at row 2"):
        parse_events(json.dumps([row, {**row, "timestamp": "2026-01-15T09:00:00"}]).encode(), "file.json")


@pytest.mark.parametrize(
    "data,name",
    [
        (b"\x00binary", "x.txt"),
        (b"not-pdf", "x.pdf"),
        (b"%PDF-1.7", "x.md"),
        (b"", "x.txt"),
        (b"<html>evil", "x.txt"),
    ],
)
def test_invalid_documents(app, data, name):
    with pytest.raises(AppError):
        extract(data, name)


def test_encrypted_pdf_rejected(app):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(AppError):
        extract(output.getvalue(), "encrypted.pdf")
