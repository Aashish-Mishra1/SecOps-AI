import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def app():
    # A fresh database is created and dropped; never truncate the user's demo data.
    url = make_url(os.environ["DATABASE_URL"])
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )
    name = "secops_test_" + uuid4().hex
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    old_url = os.environ["DATABASE_URL"]
    os.environ.update(
        DATABASE_URL=url.set(database=name).render_as_string(hide_password=False),
        APP_ENV="test",
        REGISTRATION_ENABLED="true",
        AI_PROVIDER="mock",
        REQUESTS_PER_MINUTE="1000",
    )
    from app.config import get_settings
    from app.db import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")
        # Exercise rollback/reapply only in this disposable test database.
        command.downgrade(Config("alembic.ini"), "base")
        command.upgrade(Config("alembic.ini"), "head")
        command.check(Config("alembic.ini"))
        from app.main import create_app

        yield create_app()
    finally:
        get_engine().dispose()
        get_engine.cache_clear()
        os.environ["DATABASE_URL"] = old_url
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def client(app):
    from app.services.auth import limiter

    limiter.entries.clear()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def identities(app):
    from app.config import get_settings
    from app.db import session_factory
    from app.models import Organization, User
    from app.schemas import Credentials
    from app.services.auth import hasher, login

    password = "test-only-strong-password"
    org = uuid4()
    other_org = uuid4()
    result = {}
    with session_factory()() as db:
        db.add_all([Organization(id=org, name="Test SOC"), Organization(id=other_org, name="Other SOC")])
        if not db.get(Organization, get_settings().demo_organization_id):
            db.add(Organization(id=get_settings().demo_organization_id, name="Registration SOC"))
        db.flush()
        for name, organization_id, role in (
            ("analyst", org, "analyst"),
            ("reviewer", org, "reviewer"),
            ("outsider", other_org, "analyst"),
        ):
            user = User(
                id=uuid4(),
                organization_id=organization_id,
                email=f"{uuid4().hex}@example.com",
                password_hash=hasher.hash(password),
                role=role,
            )
            db.add(user)
            db.flush()
            token = login(db, Credentials(email=user.email, password=password)).access_token
            result[name] = {
                "headers": {"Authorization": "Bearer " + token},
                "id": user.id,
                "email": user.email,
                "org": organization_id,
                "password": password,
            }
        db.commit()
    return result
