from fastapi import APIRouter, Request

from app.api.dependencies import DB, CurrentUser
from app.schemas import Credentials, TokenOut, UserOut
from app.services import auth

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(credentials: Credentials, request: Request, db: DB):
    auth.limiter.check("register:" + (request.client.host if request.client else "local"), 10)
    return auth.register(db, credentials)


@router.post("/login", response_model=TokenOut)
def login(credentials: Credentials, request: Request, db: DB):
    auth.limiter.check("login:" + (request.client.host if request.client else "local"), 20)
    return auth.login(db, credentials)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user
