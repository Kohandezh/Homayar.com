from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.config import Settings, get_settings
from backend.database.relational import (
    configure_database,
    create_schema_for_test_or_development,
    get_engine,
    session_scope,
)
from backend.database.rls import assert_production_posture, describe_current_role
from backend.routers.v1 import admin as admin_v1
from backend.routers.v1 import ai_policy as ai_policy_v1
from backend.routers.v1 import api_keys as api_keys_v1
from backend.routers.v1 import auth as auth_v1
from backend.routers.v1 import billing as billing_v1
from backend.routers.v1 import ingestions as ingestions_v1
from backend.routers.v1 import insights as insights_v1
from backend.routers.v1 import locations as locations_v1
from backend.routers.v1 import meetings as meetings_v1
from backend.routers.v1 import members as members_v1
from backend.routers.v1 import organizations as organizations_v1
from backend.routers.v1 import privacy as privacy_v1
from backend.routers.v1 import shares as shares_v1
from backend.routers.v1 import teams as teams_v1
from backend.routers.v1 import voice as voice_v1
from backend.routers.v1 import voicemail as voicemail_v1
from backend.services.auth_service import ensure_development_demo_account
from backend.services.logging_service import logger
from backend.services.tenancy_service import ensure_plan_catalog

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")


def _verify_database_posture(settings: Settings) -> None:
    """Fail closed in production when the runtime role could bypass RLS."""
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as connection:
        if settings.is_production:
            posture = assert_production_posture(connection)
        else:
            posture = describe_current_role(connection)
            if posture["superuser"] or posture["bypassrls"] or not posture["rls_enabled"]:
                logger.warning(
                    "DATABASE_RLS_POSTURE_WEAK role=%s superuser=%s bypassrls=%s rls_enabled=%s",
                    posture["role"],
                    posture["superuser"],
                    posture["bypassrls"],
                    posture["rls_enabled"],
                )
    logger.info(
        "DATABASE_RLS_POSTURE role=%s rls_enabled=%s rls_forced=%s",
        posture["role"],
        posture["rls_enabled"],
        posture["rls_forced"],
    )


def _error_payload(*, code: str, message: str, request_id: str | None, details=None) -> dict:
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


def create_app(runtime_settings: Settings | None = None) -> FastAPI:
    settings = runtime_settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_database(settings)
        if settings.auto_create_schema:
            create_schema_for_test_or_development(settings)
        _verify_database_posture(settings)
        with session_scope() as db:
            ensure_plan_catalog(db, settings)
            ensure_development_demo_account(db, settings)

        if settings.enable_neo4j_projection:
            try:
                from backend.database.neo4j_connection import Neo4jConnection

                Neo4jConnection.get_driver()
            except Exception:
                logger.warning("Neo4j projection is configured but unavailable")
        if settings.environment in ("staging", "production") and not (
            settings.tombstone_journal_dir
        ):
            # WP07-T04: without the journal a restore cannot reapply deletions
            # made after the backup (the sweep also reports journal_backlog).
            logger.warning("TOMBSTONE_JOURNAL_NOT_CONFIGURED restores may resurrect deleted data")
        logger.info(
            "HOMAYAR_STARTED environment=%s deployment_mode=%s",
            settings.environment,
            settings.deployment_mode,
        )
        yield
        if settings.enable_neo4j_projection:
            from backend.database.neo4j_connection import Neo4jConnection

            Neo4jConnection.close()
        logger.info("HOMAYAR_STOPPED")

    app = FastAPI(
        title="Homayar Conversation Intelligence API",
        version="0.5.0",
        docs_url="/api/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.dependency_overrides[get_settings] = lambda: settings

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[host.strip() for host in settings.trusted_hosts.split(",") if host.strip()],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-CSRF-Token",
            "X-Request-ID",
            "X-Workspace-ID",
        ],
        expose_headers=["X-Request-ID", "Retry-After"],
    )

    @app.middleware("http")
    async def request_context_and_security_headers(request: Request, call_next):
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith(settings.api_prefix) else "no-cache"
        )
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response

    app.include_router(auth_v1.router, prefix=settings.api_prefix)
    app.include_router(admin_v1.router, prefix=settings.api_prefix)
    app.include_router(ai_policy_v1.router, prefix=settings.api_prefix)
    app.include_router(api_keys_v1.router, prefix=settings.api_prefix)
    app.include_router(billing_v1.router, prefix=settings.api_prefix)
    app.include_router(ingestions_v1.router, prefix=settings.api_prefix)
    app.include_router(meetings_v1.router, prefix=settings.api_prefix)
    app.include_router(insights_v1.router, prefix=settings.api_prefix)
    app.include_router(members_v1.router, prefix=settings.api_prefix)
    app.include_router(organizations_v1.router, prefix=settings.api_prefix)
    app.include_router(privacy_v1.router, prefix=settings.api_prefix)
    app.include_router(shares_v1.router, prefix=settings.api_prefix)
    app.include_router(teams_v1.router, prefix=settings.api_prefix)
    app.include_router(locations_v1.router, prefix=settings.api_prefix)
    app.include_router(voice_v1.router, prefix=settings.api_prefix)
    app.include_router(voicemail_v1.router, prefix=settings.api_prefix)

    @app.get("/healthz", tags=["system"])
    @app.get("/api/health", include_in_schema=False)
    async def liveness():
        return {
            "status": "ok",
            "service": "homayar-api",
            "version": "0.5.0",
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @app.get("/readyz", tags=["system"])
    async def readiness():
        checks: dict[str, str] = {}
        ready = True
        try:
            from backend.database.relational import get_engine

            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = "ready"
        except Exception:
            checks["database"] = "unavailable"
            ready = False
        try:
            import redis

            redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
            ).ping()
            checks["redis"] = "ready"
        except Exception:
            checks["redis"] = "unavailable"
            if not settings.tasks_eager:
                ready = False
        status_code = 200 if ready else 503
        return JSONResponse(
            status_code=status_code,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                code="validation_error",
                message="Request validation failed",
                request_id=getattr(request.state, "request_id", None),
                details=details,
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", "http_error"))
            message = str(exc.detail.get("message", "Request failed"))
            details = exc.detail.get("details")
        else:
            code = "http_error"
            message = str(exc.detail)
            details = None
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_error_payload(
                code=code,
                message=message,
                request_id=getattr(request.state, "request_id", None),
                details=details,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            "UNHANDLED_ERROR request_id=%s path=%s error_type=%s",
            request_id,
            request.url.path,
            type(exc).__name__,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                code="internal_error",
                message="Internal server error",
                request_id=request_id,
            ),
        )

    return app


app = create_app()
