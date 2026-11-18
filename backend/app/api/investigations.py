from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.dependencies import DB, Analyst, CurrentUser
from app.errors import AppError
from app.graph.contracts import ReviewInput
from app.models import AgentRun, Evidence, Review
from app.services.investigation_metrics import investigation_metrics
from app.services.investigations import (
    create_investigation,
    get_investigation,
    list_investigations,
    record_review,
)

router = APIRouter(prefix="/investigations", tags=["investigations"])


class InvestigationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_id: UUID
    request_id: UUID
    auto_start: bool = True


class InvestigationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    alert_id: UUID
    request_id: UUID
    status: str
    provider: str
    model: str
    graph_thread_id: str
    attempt: int
    report: dict | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class InvestigationList(BaseModel):
    items: list[InvestigationOut]
    total: int


@router.post("", response_model=InvestigationOut, status_code=202)
def create(body: InvestigationCreate, request: Request, db: DB, user: Analyst):
    row = create_investigation(db, user, body.alert_id, body.request_id, auto_start=body.auto_start)
    if body.auto_start and row.status == "queued":
        try:
            request.app.state.investigation_runner.submit(row.id, user.organization_id)
        except AppError as exc:
            if exc.code != "executor_full":
                raise
            # The created queued ID remains discoverable and can be started explicitly.
    db.refresh(row)
    return row


@router.get("", response_model=InvestigationList)
def list_all(
    db: DB,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = None,
):
    return list_investigations(db, user.organization_id, limit, offset, status)


@router.get("/{investigation_id}", response_model=InvestigationOut)
def detail(investigation_id: UUID, db: DB, user: CurrentUser):
    return get_investigation(db, user.organization_id, investigation_id)


@router.post("/{investigation_id}/start", response_model=InvestigationOut, status_code=202)
def start(investigation_id: UUID, request: Request, db: DB, user: Analyst):
    row = get_investigation(db, user.organization_id, investigation_id)
    if row.status not in {"queued", "running"}:
        raise AppError(409, "invalid_transition", "Only queued investigations can start")
    request.app.state.investigation_runner.submit(row.id, user.organization_id)
    db.refresh(row)
    return row


@router.post("/{investigation_id}/resume", response_model=InvestigationOut, status_code=202)
def resume(investigation_id: UUID, request: Request, db: DB, user: CurrentUser):
    row = get_investigation(db, user.organization_id, investigation_id)
    review = db.scalar(select(Review).where(Review.investigation_id == row.id))
    if (review and user.role != "reviewer") or (not review and user.role != "analyst"):
        raise AppError(
            403, "forbidden", "Reviewer resumes reviewed work; analyst resumes interrupted analysis"
        )
    if row.status not in {"interrupted", "failed", "awaiting_review", "running"}:
        raise AppError(409, "invalid_transition", "Investigation cannot be resumed")
    request.app.state.investigation_runner.submit(row.id, user.organization_id, "resume")
    db.refresh(row)
    return row


@router.post("/{investigation_id}/review", response_model=InvestigationOut, status_code=202)
def review(investigation_id: UUID, body: ReviewInput, request: Request, db: DB, user: CurrentUser):
    row = record_review(db, user, investigation_id, body)
    if row.status in {"awaiting_review", "interrupted", "failed"}:
        request.app.state.investigation_runner.submit(row.id, user.organization_id, "resume")
    db.refresh(row)
    return row


@router.get("/{investigation_id}/report")
def report(investigation_id: UUID, db: DB, user: CurrentUser):
    row = get_investigation(db, user.organization_id, investigation_id)
    if row.report is None:
        raise AppError(409, "report_not_ready", "Report has not been generated")
    return row.report


@router.get("/{investigation_id}/timeline")
def timeline(investigation_id: UUID, db: DB, user: CurrentUser):
    row = get_investigation(db, user.organization_id, investigation_id)
    runs = db.scalars(
        select(AgentRun).where(AgentRun.investigation_id == row.id).order_by(AgentRun.started_at, AgentRun.id)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "agent": r.agent,
                "attempt": r.attempt,
                "status": r.status,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "latency_ms": r.latency_ms,
                "model": r.model,
                "prompt_version": r.prompt_version,
                "error_code": r.error_code,
            }
            for r in runs
        ]
    }


@router.get("/{investigation_id}/findings")
def findings(investigation_id: UUID, db: DB, user: CurrentUser):
    row = get_investigation(db, user.organization_id, investigation_id)
    runs = db.scalars(
        select(AgentRun)
        .where(AgentRun.investigation_id == row.id, AgentRun.result.is_not(None))
        .order_by(AgentRun.started_at, AgentRun.id)
    )
    results = {}
    for run in runs:
        results.update(run.result.get("findings", {}))
    return {"items": results, "validated_report_available": row.report is not None}


@router.get("/{investigation_id}/evidence")
def evidence(
    investigation_id: UUID,
    db: DB,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    row = get_investigation(db, user.organization_id, investigation_id)
    rows = db.scalars(
        select(Evidence)
        .where(Evidence.investigation_id == row.id, Evidence.organization_id == user.organization_id)
        .order_by(Evidence.created_at, Evidence.id)
        .limit(limit)
        .offset(offset)
    )
    return {
        "items": [
            {
                "id": e.id,
                "kind": e.kind,
                "source_ref": e.source_ref,
                "excerpt": e.excerpt,
                "metadata": e.metadata_json,
            }
            for e in rows
        ]
    }


@router.get("/{investigation_id}/metrics")
def metrics(investigation_id: UUID, db: DB, user: CurrentUser):
    return investigation_metrics(db, get_investigation(db, user.organization_id, investigation_id))
