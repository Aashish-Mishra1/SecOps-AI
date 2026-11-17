import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import PurePath
from uuid import UUID, uuid4

from pypdf import PdfReader
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.ai.providers import Provider
from app.config import get_settings
from app.errors import AppError
from app.models import AuditEvent, Document, DocumentChunk, User


@dataclass
class Chunk:
    page: int | None
    start: int
    end: int
    content: str


def extract(data: bytes, filename: str) -> tuple[str, list[tuple[int | None, str]]]:
    settings = get_settings()
    if len(data) > 10 * 1024 * 1024:
        raise AppError(413, "upload_too_large", "Document exceeds 10 MB")
    suffix = PurePath(filename).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md"}:
        raise AppError(415, "unsupported_type", "Use a PDF, TXT or Markdown file")
    try:
        if suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError()
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted or len(reader.pages) > settings.max_pdf_pages:
                raise ValueError()
            pages = []
            total = 0
            for index, page in enumerate(reader.pages, 1):
                # Limit decompressed content streams before expensive text extraction.
                content = page.get_contents()
                if content is not None and len(content.get_data()) > 5_000_000:
                    raise ValueError()
                value = page.extract_text() or ""
                total += len(value)
                if total > settings.max_document_chars:
                    raise ValueError()
                pages.append((index, value))
            content_type = "application/pdf"
        else:
            value = data.decode("utf-8-sig")
            if data.startswith(b"%PDF-") or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
                raise ValueError()
            if re.match(r"\s*(<!doctype html|<html)", value, re.IGNORECASE):
                raise ValueError()
            pages = [(None, value)]
            content_type = "text/markdown" if suffix == ".md" else "text/plain"
        if sum(len(value) for _, value in pages) > settings.max_document_chars:
            raise ValueError()
        pages = [(page, value.replace("\r\n", "\n").replace("\r", "\n")) for page, value in pages]
        if not any(value.strip() for _, value in pages):
            raise ValueError()
        return content_type, pages
    except Exception:
        raise AppError(
            422,
            "unreadable_document",
            "Document is unreadable, encrypted, empty or exceeds extraction limits",
        ) from None


def chunk_text(pages: list[tuple[int | None, str]], size: int, overlap: int) -> list[Chunk]:
    if not 0 <= overlap < size:
        raise ValueError("Invalid overlap")
    chunks = []
    for page, value in pages:
        words = list(re.finditer(r"\S+", value))
        for index in range(0, len(words), size - overlap):
            end_index = min(index + size, len(words))
            start, end = words[index].start(), words[end_index - 1].end()
            chunks.append(Chunk(page, start, end, value[start:end]))
            if end_index == len(words):
                break
    return chunks


def ingest(db: Session, user: User, data: bytes, filename: str, provider: Provider) -> Document:
    # Strip both Unix and Windows path components; never use uploads as filesystem paths.
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:255]
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > 10 * 1024 * 1024:
        raise AppError(413, "upload_too_large", "Document exceeds 10 MB")
    # Transaction-scoped lock serializes same-org/content submissions, including first insert.
    lock_key = int.from_bytes(
        hashlib.sha256(f"{user.organization_id}:{digest}".encode()).digest()[:8], "big", signed=True
    )
    acquired = db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key})
    if not acquired:
        raise AppError(409, "ingestion_in_progress", "An identical document is being ingested")
    document = db.scalar(
        select(Document).where(Document.organization_id == user.organization_id, Document.sha256 == digest)
    )
    if (
        document
        and document.ingestion_status == "ready"
        and document.embedding_model == provider.embedding_model
    ):
        return document
    if not document:
        document = Document(
            id=uuid4(),
            organization_id=user.organization_id,
            title=filename[:200],
            filename=filename,
            sha256=digest,
            content_type="application/octet-stream",
            ingestion_status="processing",
            embedding_model=provider.embedding_model,
        )
        db.add(document)
        db.flush()
    document.ingestion_status = "processing"
    document.error_code = None
    try:
        # Savepoint preserves a failed document record while removing any partial chunks.
        with db.begin_nested():
            content_type, pages = extract(data, filename)
            settings = get_settings()
            chunks = chunk_text(pages, settings.chunk_words, settings.chunk_overlap_words)
            embeddings = provider.embed([chunk.content for chunk in chunks])
            if len(embeddings.vectors) != len(chunks):
                raise AppError(502, "provider_error", "Invalid embedding count")
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
            for index, (chunk, vector) in enumerate(zip(chunks, embeddings.vectors, strict=True)):
                db.add(
                    DocumentChunk(
                        organization_id=user.organization_id,
                        document_id=document.id,
                        chunk_index=index,
                        page=chunk.page,
                        char_start=chunk.start,
                        char_end=chunk.end,
                        content=chunk.content,
                        embedding=vector,
                        embedding_model=provider.embedding_model,
                    )
                )
            document.content_type = content_type
            document.embedding_model = provider.embedding_model
            document.input_tokens = embeddings.input_tokens
            document.ingestion_status = "ready"
            db.flush()
    except AppError as exc:
        document.ingestion_status = "failed"
        document.error_code = exc.code
        db.add(
            AuditEvent(
                organization_id=user.organization_id,
                actor_id=user.id,
                action="document.failed",
                resource_id=document.id,
                details={"code": exc.code},
            )
        )
        db.commit()
        raise
    db.add(
        AuditEvent(
            organization_id=user.organization_id,
            actor_id=user.id,
            action="document.ready",
            resource_id=document.id,
        )
    )
    db.commit()
    return document


def list_documents(db: Session, organization_id: UUID, limit: int, offset: int) -> dict:
    condition = Document.organization_id == organization_id
    total = db.scalar(select(func.count()).select_from(Document).where(condition))
    items = db.scalars(
        select(Document)
        .where(condition)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {"total": total, "items": items}


def get_document(db: Session, organization_id: UUID, document_id: UUID) -> Document:
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.organization_id == organization_id)
    )
    if document is None:
        raise AppError(404, "not_found", "Document not found")
    return document
