from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from starlette.types import Receive, Scope, Send

from backend.config import Settings, get_settings
from backend.database.relational import (
    configure_database,
    create_schema_for_test_or_development,
    get_engine,
    session_scope,
)
from backend.database.rls import assert_production_posture, describe_current_role
from backend.observability.correlation import correlation_scope, request_correlation_id
from backend.observability.metrics import (
    METRICS_CONTENT_TYPE,
    StateCollector,
    metrics_access,
    observe_http,
    render,
)
from backend.observability.release import code_migration_head
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
from backend.routers.v1 import reports as reports_v1
from backend.routers.v1 import shares as shares_v1
from backend.routers.v1 import teams as teams_v1
from backend.routers.v1 import voice as voice_v1
from backend.routers.v1 import voicemail as voicemail_v1
from backend.services.auth_service import ensure_development_demo_account
from backend.services.logging_service import install_server_log_filter, logger, tag_exception
from backend.services.tenancy_service import ensure_plan_catalog

METRICS_PATH = "/metrics"


class _TrustedHostExceptMetrics(TrustedHostMiddleware):
    """TRUSTED_HOSTS lists the public hostnames; Prometheus scrapes the API by its
    internal service name (``api:8000``). ``/metrics`` answers no host-derived
    content and needs its bearer token, so only that path skips the host check."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == METRICS_PATH:
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


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
        install_server_log_filter()  # in case the server reconfigured its loggers
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
        if settings.metrics_enabled:
            # Load the Alembic scripts once now, not inside the first scrape's deadline.
            code_migration_head()
        if settings.metrics_enabled and metrics_access(settings) == "disabled":
            # Staging/production without METRICS_BEARER_TOKEN and without the explicit
            # METRICS_ALLOW_UNAUTHENTICATED opt-out: /metrics answers 404 (HOM-113).
            logger.warning("METRICS_ENDPOINT_DISABLED reason=no_bearer_token")
        elif (
            settings.metrics_enabled
            and settings.metrics_allow_unauthenticated
            and not settings.metrics_bearer_token
            and settings.environment in ("staging", "production")
        ):
            # The on-premise opt-out (refused for DEPLOYMENT_MODE=cloud by Settings),
            # named at startup like the HOM-161 transport opt-outs.
            logger.warning(
                "METRICS_UNAUTHENTICATED_OPT_OUT deployment_mode=%s variable=%s",
                settings.deployment_mode,
                "METRICS_ALLOW_UNAUTHENTICATED",
            )
        logger.info(
            "HOMAYAR_STARTED environment=%s deployment_mode=%s",
            settings.environment,
            settings.deployment_mode,
        )
        yield
        if settings.enable_neo4j_projection:
            from backend.database.neo4j_connection import Neo4jConnection

            Neo4jConnection.close()
        state_collector.close()
        logger.info("HOMAYAR_STOPPED")

    state_collector = StateCollector(settings, cache_seconds=settings.metrics_cache_seconds)

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
        _TrustedHostExceptMetrics,
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
        # One correlation id per request (HOM-113): the validated X-Request-ID or a
        # fresh UUID, bound for the handler, its audit events and its log lines.
        request_id = request_correlation_id(request.headers.get("x-request-id", ""))
        request.state.request_id = request_id
        started = time.perf_counter()
        with correlation_scope(request_id):
            try:
                response = await call_next(request)
            except Exception:
                observe_http(request.method, _route_template(request), 500, _elapsed(started))
                raise
        observe_http(
            request.method, _route_template(request), response.status_code, _elapsed(started)
        )
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
    app.include_router(reports_v1.router, prefix=settings.api_prefix)
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

    @app.get(METRICS_PATH, include_in_schema=False)
    async def metrics_endpoint(request: Request) -> Response:
        """Prometheus exposition (HOM-113, ADR 0036): served on the API port only.

        Not routed by the proxy and exempt from the trusted-host check (scraped as
        ``api:8000``). ``metrics_access`` decides: ``disabled`` (404), ``token``
        (bearer token, compared in constant time) or ``open`` (development/test,
        or the explicit on-premise opt-out). Rendering runs on a limiter of one
        thread, so concurrent scrapes wait without taking the API's threadpool.
        """
        access = metrics_access(settings)
        if access == "disabled":
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": "Not Found"}
            )
        if access == "token":
            scheme, _, presented = request.headers.get("authorization", "").partition(" ")
            if scheme.casefold() != "bearer" or not secrets.compare_digest(
                presented.strip().encode("utf-8"),
                settings.metrics_bearer_token.strip().encode("utf-8"),
            ):
                raise HTTPException(
                    status_code=401,
                    detail={"code": "unauthorized", "message": "Metrics need a bearer token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        limiter = getattr(app.state, "metrics_limiter", None)
        if limiter is None:
            limiter = app.state.metrics_limiter = anyio.CapacityLimiter(1)
        body = await anyio.to_thread.run_sync(render, state_collector, limiter=limiter)
        return Response(content=body, media_type=METRICS_CONTENT_TYPE)

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
        # Starlette re-raises the exception after this response and uvicorn logs it
        # again; the tag gives that (scrubbed, traceback-free) line the request id.
        tag_exception(exc, request_id or "unknown-request")
        # The route template, never the raw path (share and invitation tokens live
        # in paths); the middleware's correlation scope has already ended here.
        with correlation_scope(request_id or "unknown-request"):
            logger.error(
                "UNHANDLED_ERROR request_id=%s route=%s error_type=%s",
                request_id,
                _route_template(request),
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


def _route_template(request: Request) -> str:
    """The matched route's path template (bounded), never the raw path."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


def _elapsed(started: float) -> float:
    return time.perf_counter() - started


app = create_app()
