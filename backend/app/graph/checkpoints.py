from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from app.config import get_settings


def connection():
    url = make_url(get_settings().database_url.get_secret_value()).set(drivername="postgresql")
    return psycopg.connect(
        url.render_as_string(hide_password=False),
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
        connect_timeout=5,
        options="-c statement_timeout=15000",
    )


def setup_checkpoints() -> None:
    with connection() as conn:
        # Serialize library migrations across process startup; library owns these tables.
        conn.execute("SELECT pg_advisory_lock(732031)")
        try:
            PostgresSaver(conn).setup()
        finally:
            conn.execute("SELECT pg_advisory_unlock(732031)")


@contextmanager
def checkpoint_saver():
    with connection() as conn:
        yield PostgresSaver(conn)
