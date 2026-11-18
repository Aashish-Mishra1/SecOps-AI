"""Consume persisted running investigations. Session locks protect graph execution."""

from time import sleep

from sqlalchemy import select

from app.db import session_factory
from app.graph.checkpoints import setup_checkpoints
from app.models import Investigation
from app.services.investigations import run_investigation


def process_batch() -> int:
    with session_factory()() as db:
        ids = list(
            db.scalars(
                select(Investigation.id)
                .where(Investigation.status == "running")
                .order_by(Investigation.started_at, Investigation.id)
                .limit(32)
            )
        )
    for identity in ids:
        run_investigation(identity)
    return len(ids)


if __name__ == "__main__":
    setup_checkpoints()
    while True:
        process_batch()
        sleep(0.5)
