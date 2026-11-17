from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import AuditEvent, Organization, User
from app.schemas import Credentials, TokenOut, UserOut

hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
dummy_hash = hasher.hash("non-user-dummy-verification-value")


class RateLimiter:
    """Bounded per-process limiter; intentionally not a distributed security control."""

    def __init__(self):
        self.entries: dict[str, deque] = defaultdict(deque)
        self.lock = Lock()

    def check(self, key: str, limit: int) -> None:
        now = monotonic()
        with self.lock:
            if len(self.entries) > 5000:
                self.entries = defaultdict(deque, {k: v for k, v in self.entries.items() if v[-1] > now - 60})
                if len(self.entries) > 5000:
                    raise AppError(429, "rate_limited", "Try again later")
            times = self.entries[key]
            while times and times[0] < now - 60:
                times.popleft()
            if len(times) >= limit:
                raise AppError(429, "rate_limited", "Try again later")
            times.append(now)


limiter = RateLimiter()


def register(db: Session, credentials: Credentials) -> User:
    settings = get_settings()
    if not settings.registration_enabled:
        raise AppError(403, "registration_disabled", "Registration is disabled")
    if not db.get(Organization, settings.demo_organization_id):
        raise AppError(503, "seed_required", "Initialize the demo organization first")
    user = User(
        organization_id=settings.demo_organization_id,
        email=str(credentials.email),
        password_hash=hasher.hash(credentials.password),
        role="analyst",
    )
    db.add(user)
    try:
        db.flush()
        db.add(
            AuditEvent(
                organization_id=user.organization_id,
                actor_id=user.id,
                action="user.register",
                resource_id=user.id,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "account_unavailable", "Account cannot be registered") from None
    return user


def login(db: Session, credentials: Credentials) -> TokenOut:
    user = db.scalar(select(User).where(User.email == str(credentials.email)))
    try:
        hasher.verify(user.password_hash if user else dummy_hash, credentials.password)
    except VerificationError:
        raise AppError(401, "invalid_credentials", "Invalid email or password") from None
    if user is None:
        raise AppError(401, "invalid_credentials", "Invalid email or password")
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(credentials.password)
    now = datetime.now(timezone.utc)
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": str(user.id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
            "iss": "secops-ai",
            "aud": "secops-api",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    db.add(
        AuditEvent(
            organization_id=user.organization_id, actor_id=user.id, action="user.login", resource_id=user.id
        )
    )
    db.commit()
    return TokenOut(access_token=token, user=UserOut.model_validate(user))
