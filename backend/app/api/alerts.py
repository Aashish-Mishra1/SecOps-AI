from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.dependencies import DB, Analyst, CurrentUser
from app.schemas import AlertIn, AlertOut
from app.services import alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertList(BaseModel):
    items: list[AlertOut]
    total: int


@router.post("", response_model=AlertOut, status_code=201)
def create(data: AlertIn, db: DB, user: Analyst):
    return alerts.create(db, user, data)


@router.get("", response_model=AlertList)
def list_alerts(
    db: DB,
    user: CurrentUser,
    status: str | None = None,
    severity: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return alerts.list_alerts(db, user.organization_id, status, severity, limit, offset)


@router.get("/{alert_id}", response_model=AlertOut)
def get_alert(alert_id: UUID, db: DB, user: CurrentUser):
    return alerts.get_alert(db, user.organization_id, alert_id)
