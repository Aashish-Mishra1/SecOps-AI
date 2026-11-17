from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Alert, AuditEvent, User
from app.schemas import AlertIn


def create(db: Session, user: User, data: AlertIn) -> Alert:
    alert = Alert(organization_id=user.organization_id, **data.model_dump())
    db.add(alert)
    db.flush()
    db.add(
        AuditEvent(
            organization_id=user.organization_id,
            actor_id=user.id,
            action="alert.create",
            resource_id=alert.id,
        )
    )
    db.commit()
    return alert


def list_alerts(
    db: Session, org: UUID, status: str | None, severity: str | None, limit: int, offset: int
) -> dict:
    conditions = [Alert.organization_id == org]
    if status:
        conditions.append(Alert.status == status)
    if severity:
        conditions.append(Alert.severity_hint == severity)
    return {
        "total": db.scalar(select(func.count()).select_from(Alert).where(*conditions)),
        "items": db.scalars(
            select(Alert)
            .where(*conditions)
            .order_by(Alert.event_time.desc(), Alert.id)
            .limit(limit)
            .offset(offset)
        ).all(),
    }


def get_alert(db: Session, org: UUID, alert_id: UUID) -> Alert:
    alert = db.scalar(select(Alert).where(Alert.id == alert_id, Alert.organization_id == org))
    if alert is None:
        raise AppError(404, "not_found", "Alert not found")
    return alert
