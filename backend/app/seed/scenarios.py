"""Reproducible Phase 3 fixtures; no real identities, hosts or incident claims."""

from datetime import datetime, timedelta, timezone
from uuid import uuid5

from app.ai.providers import MockProvider
from app.models import Alert
from app.schemas import EventIn
from app.services.documents import ingest
from app.services.events import import_events

SCENARIOS = {
    "brute_force": ("Repeated authentication failures", "authentication", "failure", {}, "high"),
    "suspicious_authentication": (
        "Unexpected successful authentication",
        "authentication",
        "success",
        {"unexpected": True},
        "medium",
    ),
    "potential_exfiltration": (
        "Large outbound data transfer",
        "data_transfer",
        "success",
        {"bytes_out": 250_000_000, "destination_domain": "archive.example"},
        "high",
    ),
    "privilege_escalation": (
        "Unexpected privilege change",
        "privilege_change",
        "success",
        {"privileged": True},
        "high",
    ),
    "suspicious_network": (
        "Unexpected network connection",
        "network_connection",
        "success",
        {"unexpected": True, "destination_domain": "beacon.example", "file_sha256": "a" * 64},
        "medium",
    ),
    "benign": (
        "Scheduled authentication retries",
        "authentication",
        "failure",
        {"maintenance": True},
        "info",
    ),
}


def seed_scenarios(db, analyst) -> dict[str, str]:
    fixed = datetime(2026, 1, 16, 12, tzinfo=timezone.utc)
    result = {}
    for index, (name, (title, event_type, outcome, metadata, severity)) in enumerate(SCENARIOS.items()):
        username, ip = f"phase3-demo-{name}", f"192.0.2.{60 + index}"
        count = 20 if name == "brute_force" else 3 if name == "benign" else 1
        records = [
            EventIn(
                source="phase3-synthetic",
                source_event_id=f"{name}-{number}",
                timestamp=fixed + timedelta(seconds=number * 10),
                event_type=event_type,
                source_ip=ip,
                username=username,
                outcome=outcome,
                metadata={**metadata, "synthetic": True},
            )
            for number in range(count)
        ]
        if name in {"brute_force", "benign"}:
            records.append(
                EventIn(
                    source="phase3-synthetic",
                    source_event_id=f"{name}-success",
                    timestamp=fixed + timedelta(minutes=5),
                    event_type="authentication",
                    source_ip=ip,
                    username=username,
                    outcome="success",
                    metadata={"synthetic": True},
                )
            )
        import_events(db, analyst, records)
        identity = uuid5(analyst.organization_id, f"phase3:{name}")
        if not db.get(Alert, identity):
            db.add(
                Alert(
                    id=identity,
                    organization_id=analyst.organization_id,
                    title=title,
                    description=f"Synthetic {name} evaluation scenario; no confirmed incident.",
                    event_time=fixed,
                    source_ip=ip,
                    username=username,
                    severity_hint=severity,
                )
            )
        db.commit()
        result[name] = str(identity)
    policy = (
        "# Synthetic incident response playbook\n"
        "Repeated authentication failures and unexpected successful authentication require MFA and session review. "
        "Large outbound data transfer requires checking destination, data ownership and approved export records. "
        "Unexpected privilege change requires validating change approval and account ownership. "
        "Unexpected network connection requires passive log review; no probing is authorized. "
        "Scheduled authentication retries alone do not establish compromise. "
        "All account restrictions, network changes and revocations require human approval and rollback planning."
    )
    ingest(db, analyst, policy.encode(), "phase3-response-playbook.md", MockProvider())
    return result
