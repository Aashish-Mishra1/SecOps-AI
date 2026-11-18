from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import BoundedSemaphore
from time import monotonic, perf_counter
from uuid import UUID, uuid4

from langgraph.types import Command
from langsmith import tracing_context
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.ai.agents import AgentLLM, get_agent_llm
from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.graph.checkpoints import checkpoint_saver, connection
from app.graph.contracts import ReviewInput
from app.graph.workflow import build_graph
from app.models import AgentRun, Alert, AuditEvent, Investigation, LLMCall, Review, ToolRun, User, utcnow
from app.observability.tracing import tracer
from app.services.investigation_tools import safe_text


def lock_key(investigation_id: UUID) -> int:
    return int.from_bytes(investigation_id.bytes[:8], "big", signed=True)


@contextmanager
def execution_lock(investigation_id: UUID):
    with connection() as conn:
        acquired = conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key(investigation_id),)
        ).fetchone()["acquired"]
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key(investigation_id),))


def get_investigation(db, organization_id: UUID, investigation_id: UUID) -> Investigation:
    row = db.scalar(
        select(Investigation).where(
            Investigation.id == investigation_id, Investigation.organization_id == organization_id
        )
    )
    if row is None:
        raise AppError(404, "not_found", "Investigation not found")
    return row


def create_investigation(db, user: User, alert_id: UUID, request_id: UUID, auto_start: bool = False) -> Investigation:
    if user.role != "analyst":
        raise AppError(403, "forbidden", "Analyst role required")
    if not db.scalar(
        select(Alert.id).where(Alert.id == alert_id, Alert.organization_id == user.organization_id)
    ):
        raise AppError(404, "not_found", "Alert not found")
    llm = get_agent_llm()
    identity = uuid4()
    db.execute(
        insert(Investigation)
        .values(
            id=identity,
            organization_id=user.organization_id,
            alert_id=alert_id,
            initiated_by=user.id,
            graph_thread_id=str(identity),
            request_id=request_id,
            provider=llm.provider,
            model=llm.model,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "request_id"])
    )
    row = db.scalar(
        select(Investigation).where(
            Investigation.organization_id == user.organization_id, Investigation.request_id == request_id
        )
    )
    if row.alert_id != alert_id:
        db.rollback()
        raise AppError(409, "idempotency_conflict", "Request ID is already associated with another alert")
    if row.id == identity:
        if auto_start and get_settings().durable_worker:
            row.status, row.started_at = "running", utcnow()
        db.add(
            AuditEvent(
                organization_id=user.organization_id,
                actor_id=user.id,
                action="investigation.create",
                resource_id=row.id,
            )
        )
    db.commit()
    return row


def list_investigations(db, organization_id: UUID, limit: int, offset: int, status: str | None = None):
    conditions = [Investigation.organization_id == organization_id]
    if status:
        conditions.append(Investigation.status == status)
    return {
        "total": db.scalar(select(func.count()).select_from(Investigation).where(*conditions)),
        "items": db.scalars(
            select(Investigation)
            .where(*conditions)
            .order_by(Investigation.created_at.desc(), Investigation.id)
            .limit(limit)
            .offset(offset)
        ).all(),
    }


def record_review(db, user: User, investigation_id: UUID, review: ReviewInput) -> Investigation:
    if user.role != "reviewer":
        raise AppError(403, "forbidden", "Reviewer role required")
    row = db.scalar(
        select(Investigation)
        .where(Investigation.id == investigation_id, Investigation.organization_id == user.organization_id)
        .with_for_update()
    )
    if row is None:
        raise AppError(404, "not_found", "Investigation not found")
    previous = db.scalar(select(Review).where(Review.investigation_id == row.id))
    if previous:
        if (
            previous.reviewer_id == user.id
            and previous.decision == review.decision
            and previous.comment == review.comment
        ):
            db.commit()
            return row
        raise AppError(409, "review_conflict", "Investigation already has a review")
    if row.status != "awaiting_review":
        raise AppError(409, "invalid_transition", "Investigation is not awaiting review")
    db.add(
        Review(
            investigation_id=row.id,
            reviewer_id=user.id,
            decision=review.decision,
            comment=safe_text(review.comment, 2000),
        )
    )
    db.add(
        AuditEvent(
            organization_id=user.organization_id,
            actor_id=user.id,
            action="investigation.review",
            resource_id=row.id,
            details={"decision": review.decision},
        )
    )
    if get_settings().durable_worker:
        row.status, row.delivery_count = "running", 0
    db.commit()
    return row


def initial_state(row: Investigation, alert: Alert) -> dict:
    return {
        "investigation_id": str(row.id),
        "organization_id": str(row.organization_id),
        "alert_id": str(row.alert_id),
        "incident": {"title": safe_text(alert.title, 200), "event_time": alert.event_time.isoformat()},
        "findings": {},
        "agent_status": {},
        "evidence_ids": [],
        "retrieved_documents": [],
        "agent_run_ids": [],
        "reflection_count": 0,
        "recommendations": [],
        "approval_status": "pending",
        "errors": {},
        "usage": {},
    }


@tracer.start_as_current_span("investigation.execute", record_exception=False, set_status_on_exception=False)
def run_investigation(investigation_id: UUID, llm: AgentLLM | None = None) -> None:
    """One bounded local job. Session advisory lock survives transaction boundaries."""
    llm = llm or get_agent_llm()
    with execution_lock(investigation_id) as acquired:
        if not acquired:
            return
        from opentelemetry import trace

        trace.get_current_span().set_attribute("investigation.id", str(investigation_id))
        started = perf_counter()
        with session_factory()() as db:
            row = db.get(Investigation, investigation_id)
            if row is None or row.status != "running":
                return
            if (row.provider, row.model) != (llm.provider, llm.model):
                row.status, row.error_code = "failed", "provider_changed"
                db.commit()
                return
            if row.delivery_count:
                for model in (AgentRun, ToolRun, LLMCall):
                    db.execute(update(model).where(model.investigation_id == row.id, model.status == "running")
                               .values(status="interrupted", error_code="worker_interrupted"))
            if row.delivery_count >= 3:
                row.status, row.error_code, row.ended_at = "failed", "delivery_limit", utcnow()
                db.commit()
                return
            row.delivery_count += 1
            db.commit()
            remaining = get_settings().investigation_timeout_seconds - row.active_ms / 1000
            alert = db.get(Alert, row.alert_id)
            seed = initial_state(row, alert)
            review = db.scalar(select(Review).where(Review.investigation_id == row.id))
            review_id = str(review.id) if review else None
        config = {
            "configurable": {"thread_id": str(investigation_id)},
            "recursion_limit": get_settings().graph_recursion_limit,
            "max_concurrency": 3,
        }
        status, error = "failed", None
        try:
            if remaining <= 0:
                raise AppError(408, "execution_timeout", "Investigation active time budget exhausted")
            with checkpoint_saver() as saver, tracing_context(enabled=False):
                graph = build_graph(saver, llm, monotonic() + remaining)
                snapshot = graph.get_state(config)
                if snapshot.values:
                    has_interrupt = any(task.interrupts for task in snapshot.tasks)
                    if has_interrupt and not review_id:
                        status = "awaiting_review"
                    else:
                        payload = Command(resume={"review_id": review_id}) if has_interrupt else None
                        graph.invoke(payload, config)
                else:
                    graph.invoke(seed, config)
                snapshot = graph.get_state(config)
                if any(task.interrupts for task in snapshot.tasks):
                    status = "awaiting_review"
                elif not snapshot.next and snapshot.values.get("approval_status") in {"approved", "rejected"}:
                    status = snapshot.values["approval_status"]
                elif status != "awaiting_review":
                    raise AppError(500, "incomplete_execution", "Workflow did not reach a valid stop")
        except Exception as exc:
            status, error = "failed", exc.code if isinstance(exc, AppError) else "workflow_error"
        with session_factory()() as db:
            row = db.get(Investigation, investigation_id)
            row.status, row.error_code = status, error
            row.active_ms += (perf_counter() - started) * 1000
            row.updated_at = utcnow()
            if status in {"approved", "rejected", "failed"}:
                row.ended_at = utcnow()
            db.commit()


class InvestigationRunner:
    """Single-process bounded executor; persisted checkpoints are durable, its queue is not."""

    def __init__(self, llm: AgentLLM | None = None):
        settings = get_settings()
        self.pool = ThreadPoolExecutor(
            max_workers=settings.investigation_workers, thread_name_prefix="investigation"
        )
        self.slots = BoundedSemaphore(settings.investigation_workers + settings.investigation_queue_size)
        self.llm = llm

    def submit(self, investigation_id: UUID, organization_id: UUID, mode: str = "start") -> bool:
        if not self.slots.acquire(blocking=False):
            raise AppError(429, "executor_full", "Local executor is full; retry start or resume later")
        try:
            with session_factory()() as db:
                row = get_investigation(db, organization_id, investigation_id)
                llm = self.llm or get_agent_llm()
                if (row.provider, row.model) != (llm.provider, llm.model):
                    raise AppError(
                        409,
                        "provider_changed",
                        "Resume requires the original provider/model; create a new investigation",
                    )
                allowed = ["queued"] if mode == "start" else ["interrupted", "failed", "awaiting_review"]
                if row.status not in allowed:
                    return False
                if row.status == "awaiting_review" and not db.scalar(
                    select(Review.id).where(Review.investigation_id == row.id)
                ):
                    raise AppError(
                        409, "review_required", "A reviewer decision is required before resumption"
                    )
                claimed = db.execute(
                    update(Investigation)
                    .where(Investigation.id == investigation_id, Investigation.status.in_(allowed))
                    .values(
                        status="running",
                        delivery_count=0,
                        error_code=None,
                        ended_at=None,
                        started_at=func.coalesce(Investigation.started_at, utcnow()),
                    )
                    .returning(Investigation.id)
                ).scalar_one_or_none()
                db.commit()
                if claimed is None:
                    return False
            if get_settings().durable_worker:
                return True
            future = self.pool.submit(run_investigation, investigation_id, self.llm)
            future.add_done_callback(lambda _: self.slots.release())
            return True
        finally:
            # Successful submissions release through their completion callback.
            if "future" not in locals():
                self.slots.release()

    def shutdown(self):
        self.pool.shutdown(wait=True, cancel_futures=False)


def reconcile_interrupted() -> int:
    """Recover orphaned jobs only when no process holds their execution lock."""
    count = 0
    with session_factory()() as db:
        ids = list(db.scalars(select(Investigation.id).where(Investigation.status == "running")))
    for identity in ids:
        with execution_lock(identity) as acquired:
            if not acquired:
                continue
            with session_factory()() as db:
                row = db.get(Investigation, identity)
                if row.status != "running":
                    continue
                row.status, row.error_code = "interrupted", "process_interrupted"
                for model in (AgentRun, LLMCall, ToolRun):
                    db.execute(
                        update(model)
                        .where(model.investigation_id == identity, model.status == "running")
                        .values(status="interrupted", error_code="process_interrupted")
                    )
                db.commit()
                count += 1
    return count
