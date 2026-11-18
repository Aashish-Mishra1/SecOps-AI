"""Offline synthetic comparison; writes measured results to stdout only."""

import json
from time import perf_counter
from uuid import UUID, uuid4

from app.db import session_factory
from app.graph.checkpoints import setup_checkpoints
from app.models import Alert, Evidence, Investigation, SecurityEvent, User
from app.seed.scenarios import SCENARIOS, seed_scenarios
from app.services.evidence_validation import valid_ids
from app.services.investigation_tools import classification, event_scope
from app.services.investigations import create_investigation, run_investigation
from sqlalchemy import func, select


def main():
    setup_checkpoints()
    rows = []
    with session_factory()() as db:
        analyst = db.scalar(select(User).where(User.email == "analyst@secops.example"))
        if analyst is None:
            raise SystemExit(
                "Run `docker compose exec backend python -m app.seed` first"
            )
        scenario_ids = seed_scenarios(db, analyst)
        analyst_id = analyst.id
    for expected in [name for name in SCENARIOS if name != "benign"]:
        alert_id = UUID(scenario_ids[expected])
        with session_factory()() as db:
            alert = db.get(Alert, alert_id)
            baseline_started = perf_counter()
            events = list(
                db.scalars(select(SecurityEvent).where(*event_scope(alert)).limit(110))
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
            baseline_classification, _ = classification(events, failures)
            baseline_ms = (perf_counter() - baseline_started) * 1000
            investigation = create_investigation(
                db, db.get(User, analyst_id), alert_id, uuid4()
            )
            investigation.status = "running"
            db.commit()
            identity = investigation.id
        run_investigation(identity)
        with session_factory()() as db:
            investigation = db.get(Investigation, identity)
            report = investigation.report or {}
            evidence_ids = [UUID(value) for value in report.get("evidence_ids", [])]
            rows.append(
                {
                    "scenario": expected,
                    "expected": expected,
                    "single_agent_baseline": baseline_classification,
                    "single_agent_correct": baseline_classification == expected,
                    "single_agent_latency_ms": baseline_ms,
                    "multi_agent": report.get("incident_classification"),
                    "multi_agent_correct": report.get("incident_classification")
                    == expected,
                    "multi_agent_active_ms": investigation.active_ms,
                    "status": investigation.status,
                    "citation_valid": valid_ids(
                        identity, investigation.organization_id, evidence_ids
                    ),
                    "policy_retrieved": db.scalar(
                        select(func.count())
                        .select_from(Evidence)
                        .where(
                            Evidence.investigation_id == identity,
                            Evidence.kind == "document",
                        )
                    )
                    > 0,
                    "input_tokens": None,
                    "output_tokens": None,
                    "provider": investigation.provider,
                }
            )
    print(
        json.dumps(
            {
                "evaluation": "synthetic_phase3_v1",
                "case_count": len(rows),
                "single_agent_accuracy": sum(
                    row["single_agent_correct"] for row in rows
                )
                / len(rows),
                "multi_agent_accuracy": sum(row["multi_agent_correct"] for row in rows)
                / len(rows),
                "citation_validity": sum(row["citation_valid"] for row in rows)
                / len(rows),
                "retrieval_recall_at_5_proxy": sum(
                    row["policy_retrieved"] for row in rows
                )
                / len(rows),
                "rows": rows,
                "limitations": [
                    "Synthetic rule-aligned cases are not evidence of real-world accuracy.",
                    "The baseline and workflow share deterministic classification rules.",
                    "Mock token usage and cost are unavailable and therefore null.",
                    "Human quality review and live-provider evaluation remain required.",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
