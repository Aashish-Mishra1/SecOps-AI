from datetime import datetime
from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile

from app.api.dependencies import DB, Analyst, CurrentUser
from app.errors import AppError
from app.schemas import ImportOut
from app.services.events import import_events, parse_events, query_events

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/import", response_model=ImportOut)
def upload(db: DB, user: Analyst, file: Annotated[UploadFile, File()]):
    data = file.file.read(5 * 1024 * 1024 + 1)
    return import_events(db, user, parse_events(data, file.filename or ""))


@router.get("")
def list_events(
    db: DB,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    username: str | None = Query(None, max_length=200),
    source_ip: str | None = None,
    event_type: str | None = Query(None, max_length=100),
    outcome: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
):
    try:
        if source_ip:
            source_ip = str(ip_address(source_ip))
        if any(d and d.tzinfo is None for d in (start, end)) or (start and end and start > end):
            raise ValueError()
    except ValueError:
        raise AppError(422, "invalid_filter", "Invalid IP or timezone-aware time range") from None
    return query_events(
        db, user.organization_id, limit, offset, username, source_ip, event_type, outcome, start, end
    )
