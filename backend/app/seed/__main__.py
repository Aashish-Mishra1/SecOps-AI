import json
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.ai.providers import MockProvider
from app.config import get_settings
from app.db import session_factory
from app.models import Alert, Organization, ThreatIndicator, User
from app.schemas import EventIn
from app.services.auth import hasher
from app.services.documents import ingest
from app.services.events import import_events


def seed() -> None:
    settings = get_settings()
    if settings.app_env != "development" or not settings.demo_password:
        raise SystemExit("Seed requires development mode and a local DEMO_PASSWORD")
    password = settings.demo_password.get_secret_value()
    if len(password) < 12:
        raise SystemExit("DEMO_PASSWORD must be at least 12 characters")
    org = settings.demo_organization_id
    fixed = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
    with session_factory()() as db:
        if not db.get(Organization, org):
            db.add(Organization(id=org, name="Synthetic SOC Demo"))
            db.flush()
        for role in ("analyst", "reviewer"):
            email = f"{role}@secops.example"
            user = db.scalar(select(User).where(User.email == email))
            if not user:
                db.add(
                    User(
                        id=uuid5(NAMESPACE_URL, email),
                        organization_id=org,
                        email=email,
                        role=role,
                        password_hash=hasher.hash(password),
                    )
                )
        db.commit()
        analyst = db.scalar(
            select(User).where(User.email == "analyst@secops.example", User.organization_id == org)
        )
        if analyst is None:
            raise SystemExit("Demo user belongs to another organization")
        records = []
        for index in range(151):
            records.append(
                EventIn(
                    source="synthetic-idp",
                    source_event_id=f"suspicious-{index:03}",
                    timestamp=fixed + timedelta(seconds=index * 4),
                    event_type="authentication",
                    source_ip="192.0.2.45",
                    username="demo-analyst-01",
                    outcome="failure" if index < 150 else "success",
                    metadata={"scenario": "repeated_failures", "synthetic": True},
                )
            )
        for index in range(4):
            records.append(
                EventIn(
                    source="synthetic-idp",
                    source_event_id=f"benign-{index:03}",
                    timestamp=fixed + timedelta(hours=1, seconds=index * 30),
                    event_type="authentication",
                    source_ip="198.51.100.8",
                    username="svc-demo-backup",
                    outcome="failure" if index < 3 else "success",
                    metadata={"scenario": "scheduled_maintenance", "synthetic": True},
                )
            )
        import_events(db, analyst, records)
        for key, title, ip, username, severity in (
            ("suspicious", "Repeated failures followed by success", "192.0.2.45", "demo-analyst-01", "high"),
            ("benign", "Scheduled backup login retries", "198.51.100.8", "svc-demo-backup", "info"),
            ("unknown", "Unknown offline indicator", "203.0.113.99", "demo-unknown", "low"),
            (
                "missing-policy",
                "Policy coverage unavailable for new service",
                "203.0.113.100",
                "svc-new-demo",
                "info",
            ),
        ):
            alert_id = uuid5(NAMESPACE_URL, f"secops-demo:{key}")
            if not db.get(Alert, alert_id):
                db.add(
                    Alert(
                        id=alert_id,
                        organization_id=org,
                        title=title,
                        description=f"Synthetic {key} scenario; requires analyst interpretation.",
                        event_time=fixed,
                        source_ip=ip,
                        username=username,
                        severity_hint=severity,
                    )
                )
        indicator_id = uuid5(NAMESPACE_URL, "secops-demo:indicator")
        if not db.get(ThreatIndicator, indicator_id):
            db.add(
                ThreatIndicator(
                    id=indicator_id,
                    organization_id=org,
                    indicator_type="ip",
                    value="192.0.2.45",
                    classification="suspicious",
                    source="synthetic offline snapshot (not live intelligence)",
                    observed_at=fixed,
                )
            )
        db.commit()
        policy = (
            "# Synthetic authentication response policy\n\n"
            "Repeated failed login attempts followed by successful authentication require analyst review. "
            "Check MFA status and review active sessions. Session revocation requires human approval.\n\n"
            "The synthetic service account svc-demo-backup retries three times during its scheduled "
            "10:00–10:15 UTC maintenance window from 198.51.100.8. This alone does not establish compromise."
        )
        ingest(db, analyst, policy.encode(), "authentication-policy.md", MockProvider())
        # Samples are provided in the repository; seeding never exports credentials.
        print(
            json.dumps(
                {"status": "seeded", "events": len(records), "provider": "mock", "organization": str(org)}
            )
        )


if __name__ == "__main__":
    seed()
