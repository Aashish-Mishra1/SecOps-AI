import re
from datetime import timedelta
from ipaddress import ip_address
from time import perf_counter
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert

from app.ai.providers import get_provider
from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.graph.contracts import ToolRequest
from app.models import Alert, Evidence, Investigation, SecurityEvent, ThreatIndicator, ToolRun, User
from app.schemas import RetrievalRequest
from app.services.execution import Execution, consume
from app.services.intelligence import MITRE, external_lookup
from app.services.retrieval import retrieve


def safe_text(value: str, limit: int = 1600) -> str:
    value = re.sub(
        r"(?i)(password|api[_-]?key|authorization|secret|token)\s*[:=]\s*\S+", r"\1=[REDACTED]", value
    )
    value = re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer [REDACTED]", value)
    return value[:limit]


class Fact(BaseModel):
    id: str
    statement: str = Field(max_length=1600)
    severity: str = "info"
    kind: str


class ToolResult(BaseModel):
    facts: list[Fact] = Field(default_factory=list, max_length=100)
    data: dict = Field(default_factory=dict)
    input_tokens: int | None = None


def event_scope(alert: Alert):
    # Model cannot widen the organization, identities, or this two-hour window.
    identity = []
    if alert.username:
        identity.append(SecurityEvent.username == alert.username)
    if alert.source_ip:
        identity.append(SecurityEvent.source_ip == alert.source_ip)
    return [
        SecurityEvent.organization_id == alert.organization_id,
        SecurityEvent.timestamp >= alert.event_time - timedelta(hours=1),
        SecurityEvent.timestamp <= alert.event_time + timedelta(hours=1),
        or_(*identity) if identity else SecurityEvent.id.is_(None),
    ]


def classification(events: list[SecurityEvent], failure_count: int) -> tuple[str, str]:
    if any(e.event_type == "privilege_change" and e.outcome == "success" for e in events):
        return "privilege_escalation", "high"
    if any(
        e.event_type == "data_transfer"
        and isinstance(e.metadata_json.get("bytes_out"), (int, float))
        and e.metadata_json["bytes_out"] >= 100_000_000
        for e in events
    ):
        return "potential_exfiltration", "high"
    if failure_count >= 10:
        return "brute_force", "high"
    if any(
        e.event_type == "network_connection" and e.metadata_json.get("unexpected") is True for e in events
    ):
        return "suspicious_network", "medium"
    if any(
        e.event_type == "authentication"
        and e.outcome == "success"
        and e.metadata_json.get("unexpected") is True
        for e in events
    ):
        return "suspicious_authentication", "medium"
    return "insufficient_evidence" if not events else "activity_for_review", "info"


def store_fact(
    db,
    investigation: Investigation,
    kind: str,
    source_ref: str,
    statement: str,
    severity: str = "info",
    metadata: dict | None = None,
) -> Fact:
    evidence_id = uuid5(investigation.id, f"{kind}:{source_ref}")
    data = {**(metadata or {}), "severity": severity}
    db.execute(
        insert(Evidence.__table__)
        .values(
            id=evidence_id,
            organization_id=investigation.organization_id,
            investigation_id=investigation.id,
            kind=kind,
            source_ref=source_ref,
            excerpt=safe_text(statement),
            metadata=data,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    saved = db.get(Evidence, evidence_id)
    return Fact(
        id=str(saved.id),
        statement=saved.excerpt,
        severity=saved.metadata_json.get("severity", "info"),
        kind=kind,
    )


class InvestigationTools:
    def __init__(self, execution: Execution, organization_id: UUID):
        self.execution, self.organization_id = execution, organization_id

    def run(self, request: ToolRequest, allowed: set[str]) -> ToolResult:
        from app.observability.tracing import span

        with span("tool." + request.name):
            return self._run(request, allowed)

    def _run(self, request: ToolRequest, allowed: set[str]) -> ToolResult:
        if request.name not in allowed:
            raise AppError(403, "tool_forbidden", "Tool is not authorized for this specialist")
        self.execution.check()
        consume(self.execution.investigation_id, "tool")
        started, run_id = perf_counter(), uuid4()
        with session_factory()() as db:
            db.add(
                ToolRun(
                    id=run_id,
                    investigation_id=self.execution.investigation_id,
                    agent_run_id=self.execution.id,
                    tool=request.name,
                    status="running",
                )
            )
            db.commit()
        result, error = None, None
        try:
            with session_factory()() as db:
                db.execute(
                    text("SELECT set_config('statement_timeout', :value, true)"),
                    {"value": str(get_settings().tool_timeout_seconds * 1000)},
                )
                investigation = db.scalar(
                    select(Investigation).where(
                        Investigation.id == self.execution.investigation_id,
                        Investigation.organization_id == self.organization_id,
                    )
                )
                if investigation is None:
                    raise AppError(404, "not_found", "Investigation not found")
                actor = db.get(User, investigation.initiated_by)
                if actor is None or actor.organization_id != self.organization_id or actor.role != "analyst":
                    raise AppError(403, "tool_forbidden", "Investigation initiator is no longer authorized")
                alert = db.scalar(
                    select(Alert).where(
                        Alert.id == investigation.alert_id, Alert.organization_id == self.organization_id
                    )
                )
                result = self.dispatch(db, investigation, alert, request)
                self.execution.check()
                db.commit()
        except Exception as exc:
            error = exc.code if isinstance(exc, AppError) else "tool_error"
        elapsed = (perf_counter() - started) * 1000
        with session_factory()() as db:
            run = db.get(ToolRun, run_id)
            run.status, run.error_code, run.latency_ms = ("failed" if error else "completed"), error, elapsed
            run.input_tokens = result.input_tokens if result else None
            db.commit()
        if error:
            raise AppError(502, error, "Read-only tool failed")
        return result

    def dispatch(self, db, inv: Investigation, alert: Alert, request: ToolRequest) -> ToolResult:
        name = request.name
        if name == "security_documents":
            found = retrieve(
                db,
                inv.organization_id,
                RetrievalRequest(
                    query=request.query or "authentication policy",
                    mode=request.mode,
                    document_ids=request.document_ids,
                    top_k=3,
                ),
                get_provider(),
            )
            facts = []
            for source in found.sources:
                facts.append(
                    store_fact(
                        db,
                        inv,
                        "document",
                        str(source.chunk_id),
                        f"Policy excerpt: {source.excerpt[:1400]}",
                        metadata={
                            "document_id": str(source.document_id),
                            "chunk_id": str(source.chunk_id),
                            "title": safe_text(source.title, 200),
                            "page": source.page,
                            "score": source.score,
                            "embedding_model": found.embedding_model,
                        },
                    )
                )
            return ToolResult(
                facts=facts,
                data={
                    "document_ids": [str(s.document_id) for s in found.sources],
                    "retrieval_latency_ms": found.retrieval_latency_ms,
                },
                input_tokens=found.input_tokens,
            )
        if name == "historical_incidents":
            rows = db.scalars(
                select(Investigation)
                .join(Alert, Alert.id == Investigation.alert_id)
                .where(
                    Investigation.organization_id == inv.organization_id,
                    Investigation.id != inv.id,
                    Investigation.status.in_(["approved", "rejected"]),
                    Alert.organization_id == inv.organization_id,
                    or_(
                        Alert.username == alert.username if alert.username else False,
                        Alert.source_ip == alert.source_ip if alert.source_ip else False,
                    ),
                )
                .order_by(Investigation.created_at.desc(), Investigation.id)
                .limit(3)
            ).all()
            return ToolResult(
                facts=[
                    store_fact(
                        db,
                        inv,
                        "history",
                        str(row.id),
                        f"Prior investigation {row.id} ended with recommendation review status {row.status}; approval is not proof of compromise.",
                        metadata={"investigation_id": str(row.id)},
                    )
                    for row in rows
                ]
            )
        events = list(
            db.scalars(
                select(SecurityEvent)
                .where(*event_scope(alert))
                .order_by(SecurityEvent.timestamp, SecurityEvent.id)
                .limit(100)
            )
        )
        failures = (
            db.scalar(
                select(func.count())
                .select_from(SecurityEvent)
                .where(
                    *event_scope(alert),
                    SecurityEvent.event_type == "authentication",
                    SecurityEvent.outcome == "failure",
                )
            )
            or 0
        )
        successes = (
            db.scalar(
                select(func.count())
                .select_from(SecurityEvent)
                .where(
                    *event_scope(alert),
                    SecurityEvent.event_type == "authentication",
                    SecurityEvent.outcome == "success",
                )
            )
            or 0
        )
        # Separate tail sample captures successes after large failure bursts without unbounded rows.
        tail = list(
            db.scalars(
                select(SecurityEvent)
                .where(*event_scope(alert))
                .order_by(SecurityEvent.timestamp.desc(), SecurityEvent.id)
                .limit(10)
            )
        )
        sampled = list({e.id: e for e in events + tail}.values())
        kind, severity = classification(sampled, failures)
        if name in {"security_events", "ip_lookup"}:
            selected = [e for e in sampled if name != "ip_lookup" or e.source_ip == alert.source_ip][:20]
            return ToolResult(
                facts=[
                    store_fact(
                        db,
                        inv,
                        "event",
                        str(e.id),
                        f"{e.timestamp.isoformat()}: {safe_text(e.event_type, 100)} outcome={e.outcome} from {e.source_ip or 'unknown'}.",
                        metadata={"event_id": str(e.id), "timestamp": e.timestamp.isoformat()},
                    )
                    for e in selected
                ],
                data={"sample_limit": 20, "window_hours": 2},
            )
        if name in {"event_correlation", "severity_assessment"}:
            total = db.scalar(select(func.count()).select_from(SecurityEvent).where(*event_scope(alert))) or 0
            statement = (
                f"Within the scoped two-hour window, {total} events include {failures} authentication failures "
                f"and {successes} successes. Rule assessment: {kind} ({severity}); this is not confirmation of compromise."
            )
            facts = [
                store_fact(
                    db,
                    inv,
                    "correlation",
                    "alert-window",
                    statement,
                    severity,
                    {
                        "classification": kind,
                        "total": total,
                        "failures": failures,
                        "successes": successes,
                        "source_event_ids": [str(e.id) for e in sampled],
                        "window_hours": 2,
                    },
                )
            ]
            return ToolResult(facts=facts, data={"classification": kind, "severity": severity})
        if name == "mitre_lookup":
            if kind not in MITRE:
                return ToolResult(
                    data={"limitation": "No supported ATT&CK mapping for the observed activity."}
                )
            technique, title = MITRE[kind]
            statement = f"Observed rule category {kind} suggests a tentative ATT&CK mapping to {technique} ({title}); technique execution is unconfirmed."
            return ToolResult(
                facts=[
                    store_fact(
                        db,
                        inv,
                        "mitre",
                        technique,
                        statement,
                        severity,
                        {
                            "technique_id": technique,
                            "name": title,
                            "url": f"https://attack.mitre.org/techniques/{technique}/",
                            "classification": kind,
                            "source_event_ids": [str(e.id) for e in sampled],
                            "mapping": "hypothesis",
                        },
                    )
                ]
            )
        if name == "threat_intelligence":
            indicators: set[tuple[str, str]] = set()
            if alert.source_ip:
                indicators.add(("ip", str(ip_address(alert.source_ip))))
            for event in sampled:
                metadata = event.metadata_json
                domain, digest = metadata.get("destination_domain"), metadata.get("file_sha256")
                if isinstance(domain, str) and re.fullmatch(
                    r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", domain
                ):
                    indicators.add(("domain", domain.lower()))
                if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    indicators.add(("sha256", digest.lower()))
            facts = []
            for indicator_type, value in sorted(indicators)[:5]:
                if get_settings().threat_intel_provider == "offline":
                    record = db.scalar(
                        select(ThreatIndicator)
                        .where(
                            ThreatIndicator.organization_id == inv.organization_id,
                            ThreatIndicator.indicator_type == indicator_type,
                            ThreatIndicator.value == value,
                        )
                        .order_by(ThreatIndicator.observed_at.desc())
                        .limit(1)
                    )
                    details = {
                        "classification": record.classification if record else "unknown",
                        "source": record.source if record else "offline snapshot",
                        "mode": "synthetic_snapshot"
                        if record and "synthetic" in record.source.lower()
                        else "offline_snapshot",
                        "observed_at": record.observed_at.isoformat() if record else None,
                        "limitation": "Offline reputation may be stale; unknown does not mean benign.",
                    }
                else:
                    details = external_lookup(indicator_type, value)
                statement = f"{indicator_type} {value}: reputation {details['classification']} from {details['source']} ({details['mode']}). {details['limitation']}"
                facts.append(
                    store_fact(
                        db,
                        inv,
                        "indicator",
                        f"{indicator_type}:{value}",
                        statement,
                        "medium" if details["classification"] in {"suspicious", "malicious"} else "info",
                        {**details, "indicator_type": indicator_type, "value": value},
                    )
                )
            return ToolResult(facts=facts)
        raise AppError(403, "tool_forbidden", "Unknown tool")


def evidence_facts(investigation_id: UUID, organization_id: UUID, ids: list[str] | None = None) -> list[dict]:
    with session_factory()() as db:
        query = select(Evidence).where(
            Evidence.investigation_id == investigation_id, Evidence.organization_id == organization_id
        )
        if ids is not None:
            query = query.where(Evidence.id.in_([UUID(value) for value in ids]))
        return [
            {
                "id": str(e.id),
                "statement": e.excerpt,
                "severity": e.metadata_json.get("severity", "info"),
                "kind": e.kind,
            }
            for e in db.scalars(query.order_by(Evidence.kind, Evidence.id).limit(100))
        ]
