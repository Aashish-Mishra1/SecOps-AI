import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.ai.agents import MockAgentLLM
from app.db import session_factory
from app.graph.checkpoints import checkpoint_saver
from app.graph.contracts import ReviewInput
from app.graph.workflow import build_graph
from app.models import AgentRun, Investigation, Review, User
from app.seed.scenarios import seed_scenarios
from app.services.investigations import (
    InvestigationRunner,
    create_investigation,
    reconcile_interrupted,
    record_review,
    run_investigation,
)


@pytest.fixture
def scenarios(identities):
    with session_factory()() as db:
        return seed_scenarios(db, db.get(User, identities["analyst"]["id"]))


def wait_for(client, identity, headers, statuses=("awaiting_review", "approved", "rejected", "failed")):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/investigations/{identity}", headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] in statuses:
            return result
        time.sleep(0.05)
    pytest.fail("Investigation did not reach a terminal or review state")


def test_full_graph_parallel_review_and_metrics(client, identities, scenarios):
    analyst, reviewer = identities["analyst"]["headers"], identities["reviewer"]["headers"]
    body = {"alert_id": scenarios["brute_force"], "request_id": str(uuid4())}
    created = client.post("/api/v1/investigations", headers=analyst, json=body)
    assert created.status_code == 202, created.text
    identity = created.json()["id"]
    assert client.post("/api/v1/investigations", headers=analyst, json=body).json()["id"] == identity
    ready = wait_for(client, identity, analyst)
    assert ready["status"] == "awaiting_review", ready
    report = ready["report"]
    assert report["incident_classification"] == "brute_force" and report["severity"] == "high"
    assert report["approval_status"] == "pending" and report["evidence_ids"]
    assert report["provider"] == "mock"
    assert all(rec["requires_approval"] for rec in report["recommendations"])
    with session_factory()() as db:
        runs = list(
            db.scalars(
                select(AgentRun).where(
                    AgentRun.investigation_id == UUID(identity),
                    AgentRun.agent.in_(["log_analysis", "knowledge", "threat_intel"]),
                    AgentRun.attempt == 0,
                )
            )
        )
        assert len(runs) == 3
        assert max(r.started_at for r in runs) < min(r.ended_at for r in runs), (
            "Branches must actually overlap"
        )
    assert (
        client.post(
            f"/api/v1/investigations/{identity}/review", headers=analyst, json={"decision": "approve"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/investigations/{identity}", headers=identities["outsider"]["headers"]
        ).status_code
        == 404
    )
    assert client.post(f"/api/v1/investigations/{identity}/resume", headers=analyst).status_code == 409
    approved = client.post(
        f"/api/v1/investigations/{identity}/review", headers=reviewer, json={"decision": "approve"}
    )
    assert approved.status_code == 202, approved.text
    final = wait_for(client, identity, analyst, ("approved", "rejected", "failed"))
    assert final["status"] == "approved", final
    assert final["report"]["approval_status"] == "approved"
    assert (
        client.post(
            f"/api/v1/investigations/{identity}/review", headers=reviewer, json={"decision": "approve"}
        ).status_code
        == 202
    )
    assert (
        client.post(
            f"/api/v1/investigations/{identity}/review",
            headers=reviewer,
            json={"decision": "reject", "comment": "No"},
        ).status_code
        == 409
    )
    metrics = client.get(f"/api/v1/investigations/{identity}/metrics", headers=analyst).json()
    assert metrics["tool_invocations"] >= 7 and metrics["llm_invocations"] >= 7
    assert (
        metrics["active_execution_ms"] > 0 and metrics["input_tokens"] is None and metrics["cost_usd"] is None
    )
    assert metrics["time_to_first_token_ms"] is None


def test_checkpoint_reinstantiation_and_rejected_review(client, identities, scenarios):
    with session_factory()() as db:
        row = create_investigation(
            db, db.get(User, identities["analyst"]["id"]), UUID(scenarios["benign"]), uuid4()
        )
        row.status = "running"
        db.commit()
        identity = row.id
    run_investigation(identity)
    with checkpoint_saver() as saver:
        graph = build_graph(saver, MockAgentLLM())
        snapshot = graph.get_state({"configurable": {"thread_id": str(identity)}})
        assert snapshot.next == ("human_review",)
        assert any(task.interrupts for task in snapshot.tasks)
    with session_factory()() as db:
        row = db.get(Investigation, identity)
        assert row.status == "awaiting_review"
        assert row.report["severity"] == "info"
        record_review(
            db,
            db.get(User, identities["reviewer"]["id"]),
            identity,
            ReviewInput(decision="reject", comment="Synthetic rejection"),
        )
        row.status = "running"  # emulate a process exit after accepting review, before resuming
        db.commit()
    assert reconcile_interrupted() >= 1
    runner = InvestigationRunner()
    assert runner.submit(identity, identities["analyst"]["org"], "resume")
    runner.shutdown()
    with session_factory()() as db:
        row = db.get(Investigation, identity)
        assert row.status == "rejected" and row.report["review"]["decision"] == "reject"
        assert (
            db.scalar(select(func.count()).select_from(Review).where(Review.investigation_id == identity))
            == 1
        )


@pytest.mark.parametrize(
    "name",
    ["suspicious_authentication", "potential_exfiltration", "privilege_escalation", "suspicious_network"],
)
def test_scenarios(client, identities, scenarios, name):
    headers = identities["analyst"]["headers"]
    result = client.post(
        "/api/v1/investigations",
        headers=headers,
        json={"alert_id": scenarios[name], "request_id": str(uuid4())},
    )
    ready = wait_for(client, result.json()["id"], headers)
    assert ready["status"] == "awaiting_review", ready
    assert ready["report"]["incident_classification"] == name
    assert ready["report"]["mitre_mappings"]
