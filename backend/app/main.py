import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException

from app.config import get_settings
from app.db import get_engine
from app.errors import AppError
from app.observability.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app):
        from app.graph.checkpoints import setup_checkpoints
        from app.services.investigations import InvestigationRunner, reconcile_interrupted

        setup_checkpoints()
        reconcile_interrupted()
        runner = InvestigationRunner()
        app.state.investigation_runner = runner
        yield
        runner.shutdown()

    app = FastAPI(
        title="SecOps AI",
        version="0.2.0",
        lifespan=lifespan,
        description="Scoped ingestion, retrieval and persistent multi-agent investigations",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins.split(","),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def error(request: Request, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        started = time.perf_counter()
        # Bound the entire request body, including multipart overhead, before parsing.
        if request.method == "POST":
            total = 0
            body = bytearray()
            async for part in request.stream():
                total += len(part)
                if total > 11 * 1024 * 1024:
                    return error(request, 413, "upload_too_large", "Request exceeds 11 MB")
                body.extend(part)
            request._body = bytes(body)
        try:
            response = await call_next(request)
        except Exception:
            response = error(request, 500, "internal_error", "An internal error occurred")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        logging.getLogger("secops").info(
            "http_request",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return error(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error(request, 422, "validation_error", "Invalid request fields")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error(request, exc.status_code, "http_error", str(exc.detail))

    @app.get("/api/v1/health/live", tags=["health"])
    def live():
        return {"status": "ok", "provider": settings.ai_provider}

    @app.get("/api/v1/health/ready", tags=["health"])
    def ready():
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
                revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                vector = connection.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname='vector'")
                )
                if not vector.scalar_one_or_none() or revision != "0002":
                    raise ValueError("Schema not ready")
        except Exception:
            raise AppError(503, "not_ready", "Database or schema is unavailable") from None
        return {"status": "ready", "provider": settings.ai_provider}

    from app.api import alerts, auth, documents, events, investigations

    for router in (auth.router, alerts.router, events.router, documents.router, investigations.router):
        app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
