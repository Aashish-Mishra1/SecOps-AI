import csv
import io
import json
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import AuditEvent, SecurityEvent, User
from app.schemas import EventIn, EventOut, ImportOut


def parse_events(data: bytes, filename: str) -> list[EventIn]:
    if len(data) > 5 * 1024 * 1024:
        raise AppError(413, "upload_too_large", "Event file exceeds 5 MB")
    try:
        decoded = data.decode("utf-8-sig")
        if filename.lower().endswith(".json"):
            rows = json.loads(decoded)
            if not isinstance(rows, list):
                raise ValueError()
        elif filename.lower().endswith(".csv"):
            reader = csv.DictReader(io.StringIO(decoded), strict=True)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError()
            rows = list(reader)
        else:
            raise AppError(415, "unsupported_type", "Use a CSV or JSON file")
        if not rows or len(rows) > 10_000:
            raise AppError(422, "row_limit", "Provide between 1 and 10000 records")
        events = []
        for number, row in enumerate(rows, 1):
            try:
                if isinstance(row, dict) and isinstance(row.get("metadata"), str):
                    row["metadata"] = json.loads(row["metadata"] or "{}")
                events.append(EventIn.model_validate(row))
            except (ValidationError, ValueError, TypeError):
                raise AppError(
                    422, "invalid_row", f"Invalid record at row {number}; no records imported"
                ) from None
        return events
    except (UnicodeDecodeError, ValueError, csv.Error, RecursionError):
        raise AppError(422, "invalid_file", "Malformed event file; no records imported") from None


def import_events(db: Session, user: User, events: list[EventIn]) -> ImportOut:
    accepted = 0
    # Each batch shares one transaction; duplicates preserve the first stored event.
    for start in range(0, len(events), 500):
        records = []
        for event in events[start : start + 500]:
            record = event.model_dump()
            record.update(id=uuid4(), organization_id=user.organization_id)
            records.append(record)
        result = db.execute(
            insert(SecurityEvent.__table__)
            .values(records)
            .on_conflict_do_nothing(index_elements=["organization_id", "source", "source_event_id"])
            .returning(SecurityEvent.id)
        )
        accepted += len(result.all())
    summary = ImportOut(accepted=accepted, duplicates=len(events) - accepted)
    db.add(
        AuditEvent(
            organization_id=user.organization_id,
            actor_id=user.id,
            action="events.import",
            details=summary.model_dump(),
        )
    )
    db.commit()
    return summary


def query_events(
    db: Session,
    organization_id: UUID,
    limit: int,
    offset: int,
    username: str | None = None,
    source_ip: str | None = None,
    event_type: str | None = None,
    outcome: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    conditions = [SecurityEvent.organization_id == organization_id]
    for column, value in (
        (SecurityEvent.username, username),
        (SecurityEvent.source_ip, source_ip),
        (SecurityEvent.event_type, event_type),
        (SecurityEvent.outcome, outcome),
    ):
        if value is not None:
            conditions.append(column == value)
    if start:
        conditions.append(SecurityEvent.timestamp >= start)
    if end:
        conditions.append(SecurityEvent.timestamp <= end)
    total = db.scalar(select(func.count()).select_from(SecurityEvent).where(*conditions))
    rows = db.scalars(
        select(SecurityEvent)
        .where(*conditions)
        .order_by(SecurityEvent.timestamp.desc(), SecurityEvent.id)
        .limit(limit)
        .offset(offset)
    )
    return {
        "total": total,
        "items": [
            EventOut(
                id=e.id,
                source=e.source,
                source_event_id=e.source_event_id,
                timestamp=e.timestamp,
                event_type=e.event_type,
                source_ip=e.source_ip,
                username=e.username,
                outcome=e.outcome,
                metadata=e.metadata_json,
            )
            for e in rows
        ],
    }
