from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import User
from app.services.auth import limiter

DB = Annotated[Session, Depends(get_db)]
bearer = HTTPBearer(auto_error=False)


def current_user(
    db: DB, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> User:
    if not credentials:
        raise AppError(401, "unauthenticated", "Bearer token required")
    try:
        claims = jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience="secops-api",
            issuer="secops-ai",
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
        user = db.get(User, UUID(claims["sub"]))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise AppError(401, "invalid_token", "Invalid or expired token") from None
    if user is None:
        raise AppError(401, "invalid_token", "Invalid or expired token")
    limiter.check(str(user.id), get_settings().requests_per_minute)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def analyst(user: CurrentUser) -> User:
    if user.role != "analyst":
        raise AppError(403, "forbidden", "Analyst role required")
    return user


Analyst = Annotated[User, Depends(analyst)]
