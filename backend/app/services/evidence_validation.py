from uuid import UUID

from sqlalchemy import func, select

from app.db import session_factory
from app.graph.contracts import Finding
from app.models import Document, DocumentChunk, Evidence, Investigation, SecurityEvent

RANK = {name: i for i, name in enumerate(("info", "low", "medium", "high", "critical"))}


def valid_source(db, evidence: Evidence) -> bool:
    org = evidence.organization_id
    if evidence.kind == "document":
        return (
            db.scalar(
                select(DocumentChunk.id)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    DocumentChunk.id == UUID(evidence.source_ref),
                    DocumentChunk.organization_id == org,
                    Document.organization_id == org,
                    Document.ingestion_status == "ready",
                    DocumentChunk.embedding_model == evidence.metadata_json["embedding_model"],
                )
            )
            is not None
        )
    if evidence.kind == "event":
        return (
            db.scalar(
                select(SecurityEvent.id).where(
                    SecurityEvent.id == UUID(evidence.source_ref), SecurityEvent.organization_id == org
                )
            )
            is not None
        )
    if evidence.kind in {"correlation", "mitre"}:
        ids = [UUID(value) for value in evidence.metadata_json.get("source_event_ids", [])]
        return (
            db.scalar(
                select(func.count())
                .select_from(SecurityEvent)
                .where(SecurityEvent.id.in_(ids), SecurityEvent.organization_id == org)
            )
            or 0
        ) == len(ids)
    if evidence.kind == "history":
        return (
            db.scalar(
                select(Investigation.id).where(
                    Investigation.id == UUID(evidence.source_ref),
                    Investigation.organization_id == org,
                    Investigation.status.in_(["approved", "rejected"]),
                )
            )
            is not None
        )
    return evidence.kind == "indicator"


def validate_finding(investigation_id: UUID, organization_id: UUID, finding: Finding) -> bool:
    with session_factory()() as db:
        ids = set(finding.evidence_ids)
        evidence = list(
            db.scalars(
                select(Evidence).where(
                    Evidence.id.in_(ids),
                    Evidence.organization_id == organization_id,
                    Evidence.investigation_id == investigation_id,
                )
            )
        )
        return (
            len(evidence) == len(ids)
            and all(valid_source(db, e) for e in evidence)
            and any(finding.claim == e.excerpt for e in evidence)
            and RANK[finding.severity]
            <= max((RANK[e.metadata_json.get("severity", "info")] for e in evidence), default=0)
        )


def valid_ids(investigation_id: UUID, organization_id: UUID, ids: list[UUID]) -> bool:
    with session_factory()() as db:
        rows = list(
            db.scalars(
                select(Evidence).where(
                    Evidence.id.in_(set(ids)),
                    Evidence.organization_id == organization_id,
                    Evidence.investigation_id == investigation_id,
                )
            )
        )
        return bool(ids) and len(rows) == len(set(ids)) and all(valid_source(db, e) for e in rows)
