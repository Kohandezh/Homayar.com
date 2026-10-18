from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import User
from backend.database.tenant_context import bind_user_context
from backend.schemas.auth import (
    AuthMethodsResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    OtpRequest,
    OtpRequestedResponse,
    OtpVerifyRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionResponse,
    UserResponse,
    VerificationResendResponse,
    VerifyEmailRequest,
    WorkspaceResponse,
)
from backend.security.authorization import role_permission_values
from backend.security.context import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    Actor,
    require_actor,
    require_csrf,
)
from backend.security.tokens import parse_refresh_session_id, token_digest
from backend.services.auth_service import (
    AuthenticationError,
    InvalidOneTimeTokenError,
    RefreshTokenReplayError,
    RegistrationConflictError,
    SessionTokens,
    VerificationNotApplicableError,
    authenticate_password,
    create_login_session,
    email_verification_pending,
    register_email_user,
    reissue_email_verification,
    request_password_reset,
    reset_password,
    revoke_session,
    rotate_refresh_token,
    verify_email,
)
from backend.services.google_oauth_service import (
    AccountLinkRequiredError,
    GoogleOAuthError,
    complete_google_oauth,
    start_google_oauth,
)
from backend.services.logging_service import logger
from backend.services.otp_service import (
    IdentityLinkConflictError,
    OtpCooldownError,
    OtpDeliveryError,
    OtpVerificationError,
    PhoneAuthUnavailableError,
    request_otp,
    verify_otp,
)
from backend.services.rate_limit_service import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitUnavailable,
)
from backend.services.tenancy_service import list_user_workspaces
from backend.speech.text_normalize import normalize_digits

router = APIRouter(prefix="/auth", tags=["authentication"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limit(
    settings: Settings,
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        RateLimiter(settings).hit(key, limit=limit, window_seconds=window_seconds)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "Too many requests"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "rate_limiter_unavailable", "message": "Try again later"},
        ) from exc


def _set_session_cookies(response: Response, tokens: SessionTokens, settings: Settings) -> None:
    common = {
        "secure": settings.secure_cookies,
        "samesite": "lax",
        "domain": settings.cookie_domain,
    }
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        httponly=True,
        max_age=tokens.access_expires_in,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        httponly=True,
        max_age=settings.refresh_token_days * 86400,
        path=f"{settings.api_prefix}/auth",
        **common,
    )
    response.set_cookie(
        CSRF_COOKIE,
        tokens.csrf_token,
        httponly=False,
        max_age=settings.refresh_token_days * 86400,
        path="/",
        **common,
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=settings.cookie_domain)
    response.delete_cookie(
        REFRESH_COOKIE, path=f"{settings.api_prefix}/auth", domain=settings.cookie_domain
    )
    response.delete_cookie(CSRF_COOKIE, path="/", domain=settings.cookie_domain)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        phone_e164=user.phone_e164,
        display_name=user.display_name,
        locale=user.locale,
        email_verified=user.email_verified_at is not None,
        phone_verified=user.phone_verified_at is not None,
        is_active=user.is_active,
        system_role=user.system_role,
    )


def _session_response(
    db: Session, user: User, access_expires_in: int
) -> SessionResponse:
    # The caller has just authenticated `user`; memberships are visible to the
    # database only for that user id (row-level security bootstrap path).
    bind_user_context(db, user.id)
    workspaces = [
        WorkspaceResponse(
            id=workspace.id,
            organization_id=workspace.organization_id,
            name=workspace.name,
            role=membership.role.value,
            permissions=role_permission_values(membership.role),
        )
        for workspace, membership in list_user_workspaces(db, user.id)
    ]
    verified = user.email_verified_at is not None or user.phone_verified_at is not None
    return SessionResponse(
        user=_user_response(user),
        workspaces=workspaces,
        access_expires_in=access_expires_in,
        requires_verification=not verified,
    )


class EmailDeliveryError(RuntimeError):
    """The message was not handed to an SMTP server (unconfigured or refused)."""


async def _send_verification_message(
    user: User, token: str, settings: Settings
) -> None:
    from backend.services.email_service import send_verification_email

    if not await send_verification_email(user.email or "", token):
        raise EmailDeliveryError("verification e-mail not delivered")


async def _send_reset_message(user: User, token: str, settings: Settings) -> None:
    from backend.services.email_service import send_password_reset_email

    await send_password_reset_email(user.email or "", token)


@router.post("/register", response_model=MessageResponse, status_code=202)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    ip = _client_ip(request)
    _rate_limit(settings, f"register:ip:{token_digest(ip, settings)}", limit=5, window_seconds=3600)
    try:
        user, verification_token = register_email_user(
            db,
            email=str(payload.email),
            password=payload.password,
            display_name=payload.display_name,
            locale=payload.locale,
            settings=settings,
        )
        db.commit()
    except RegistrationConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "account_exists", "message": str(exc)},
        ) from exc
    try:
        await _send_verification_message(user, verification_token, settings)
    except Exception as exc:
        # The account remains valid and verification can be re-requested.
        logger.error(
            "VERIFICATION_EMAIL_DELIVERY_FAILED user_id=%s error_type=%s",
            user.id,
            type(exc).__name__,
        )
    return MessageResponse(
        status="pending_verification",
        message="Check your email to verify the account",
    )


def available_auth_methods(settings: Settings) -> AuthMethodsResponse:
    """Enabled *and* configured, never merely implemented (gap G1).  The settings
    validator already refuses an enabled method without its credentials; the
    same conditions are repeated so the answer cannot drift from that rule."""
    return AuthMethodsResponse(
        password=True,
        google=bool(
            settings.enable_google_auth
            and settings.google_client_id
            and settings.google_client_secret
            and settings.google_redirect_uri
        ),
        phone_otp=bool(
            settings.enable_phone_auth and settings.asanak_api_key and settings.asanak_sender
        ),
    )


@router.get("/methods", response_model=AuthMethodsResponse)
def auth_methods(settings: Settings = Depends(get_settings)) -> AuthMethodsResponse:
    """Public: which sign-in methods the console may offer.  No secret, no
    per-user data; the answer only changes with the deployment settings (the
    API-wide ``Cache-Control: no-store`` applies, so a change shows at once)."""
    return available_auth_methods(settings)


@router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    ip = _client_ip(request)
    identifier_key = token_digest(payload.identifier.casefold(), settings)
    _rate_limit(settings, f"login:ip:{token_digest(ip, settings)}", limit=20, window_seconds=900)
    _rate_limit(settings, f"login:id:{identifier_key}", limit=10, window_seconds=900)
    try:
        user = authenticate_password(
            db,
            identifier=payload.identifier,
            password=payload.password,
            settings=settings,
        )
    except AuthenticationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_credentials", "message": "Invalid credentials"},
        ) from exc
    tokens = create_login_session(
        db,
        user=user,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip,
        settings=settings,
    )
    db.commit()
    _set_session_cookies(response, tokens, settings)
    return _session_response(db, user, tokens.access_expires_in)


@router.post("/refresh", response_model=SessionResponse)
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    if not refresh_token or not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_refresh", "message": "Invalid refresh session"},
        )
    try:
        user, tokens = rotate_refresh_token(
            db,
            raw_refresh_token=refresh_token,
            csrf_token=csrf_header,
            settings=settings,
        )
        db.commit()
    except RefreshTokenReplayError as exc:
        # Persist the revocation before rejecting the replayed token.
        db.commit()
        _clear_session_cookies(response, settings)
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_refresh", "message": str(exc)},
        ) from exc
    except AuthenticationError as exc:
        db.rollback()
        _clear_session_cookies(response, settings)
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_refresh", "message": str(exc)},
        ) from exc
    _set_session_cookies(response, tokens, settings)
    return _session_response(db, user, tokens.access_expires_in)


@router.post("/logout", response_model=MessageResponse)
def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_failed", "message": "CSRF validation failed"},
        )
    if refresh_token:
        session_id = parse_refresh_session_id(refresh_token)
        if session_id:
            revoke_session(db, session_id)
            db.commit()
    _clear_session_cookies(response, settings)
    return MessageResponse(status="logged_out", message="Signed out")


@router.get("/me", response_model=SessionResponse)
def me(
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    return _session_response(db, actor.user, settings.access_token_minutes * 60)


@router.post("/verify-email", response_model=MessageResponse)
def verify_email_endpoint(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    try:
        verify_email(db, payload.token, settings)
        db.commit()
    except InvalidOneTimeTokenError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_verification_token", "message": str(exc)},
        ) from exc
    return MessageResponse(status="verified", message="Email verified")


VERIFICATION_RESEND_COOLDOWN_SECONDS = 60
VERIFICATION_RESEND_PER_HOUR = 5


@router.post(
    "/verify-email/resend", response_model=VerificationResendResponse, status_code=202
)
async def resend_verification_email(
    actor: Actor = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VerificationResendResponse:
    """A new verification link for the signed-in, unverified account (gap G2).

    Session + CSRF only: the caller is the account holder, so the answer
    reveals nothing about other addresses.  One request per cooldown and a
    small hourly cap per user; the new token supersedes every older one."""
    user = actor.user
    try:
        pending = email_verification_pending(user)
    except VerificationNotApplicableError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "email_not_set", "message": str(exc)},
        ) from exc
    if not pending:
        return VerificationResendResponse(status="already_verified", resend_after=0)
    user_key = token_digest(f"verify-resend:{user.id}", settings)
    for window, limit in (
        (VERIFICATION_RESEND_COOLDOWN_SECONDS, 1),
        (3600, VERIFICATION_RESEND_PER_HOUR),
    ):
        try:
            RateLimiter(settings).hit(
                f"verify-resend:{window}:{user_key}", limit=limit, window_seconds=window
            )
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "verification_cooldown",
                    "message": "A verification link was sent recently",
                },
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except RateLimitUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "rate_limiter_unavailable", "message": "Try again later"},
            ) from exc
    token = reissue_email_verification(db, user=user, settings=settings)
    db.commit()
    try:
        await _send_verification_message(user, token, settings)
    except Exception as exc:
        # Same rule as registration: the account stays valid; the failure is
        # an operator problem (SMTP), logged without the address.
        logger.error(
            "VERIFICATION_EMAIL_DELIVERY_FAILED user_id=%s error_type=%s",
            user.id,
            type(exc).__name__,
        )
    return VerificationResendResponse(
        status="accepted", resend_after=VERIFICATION_RESEND_COOLDOWN_SECONDS
    )


@router.post("/forgot-password", response_model=MessageResponse, status_code=202)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    ip = _client_ip(request)
    _rate_limit(settings, f"reset:ip:{token_digest(ip, settings)}", limit=10, window_seconds=3600)
    result = request_password_reset(db, str(payload.email), settings)
    db.commit()
    if result:
        user, reset_token = result
        try:
            await _send_reset_message(user, reset_token, settings)
        except Exception as exc:
            logger.error(
                "PASSWORD_RESET_EMAIL_DELIVERY_FAILED user_id=%s error_type=%s",
                user.id,
                type(exc).__name__,
            )
    return MessageResponse(
        status="accepted",
        message="If an account exists, password reset instructions will be sent",
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password_endpoint(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    try:
        reset_password(
            db,
            raw_token=payload.token,
            new_password=payload.password,
            settings=settings,
        )
        db.commit()
    except InvalidOneTimeTokenError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_reset_token", "message": str(exc)},
        ) from exc
    return MessageResponse(status="password_reset", message="Password updated")


def _send_asanak_code(phone: str, code: str, settings: Settings) -> None:
    if not settings.asanak_api_key or not settings.asanak_sender:
        raise PhoneAuthUnavailableError("Asanak SMS is not configured")
    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                settings.asanak_base_url,
                data={
                    "source": settings.asanak_sender,
                    "destination": phone,
                    "message": f"Homayar verification code: {code}",
                },
                headers={"Authorization": f"Bearer {settings.asanak_api_key}"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OtpDeliveryError(type(exc).__name__) from exc


@router.post("/otp/request", response_model=OtpRequestedResponse, status_code=202)
def otp_request(
    payload: OtpRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OtpRequestedResponse:
    ip = _client_ip(request)
    _rate_limit(
        settings,
        f"otp-send:ip:{token_digest(ip, settings)}",
        limit=10,
        window_seconds=3600,
    )
    try:
        challenge = request_otp(
            db,
            phone=payload.phone,
            send_code=lambda phone, code: _send_asanak_code(phone, code, settings),
            settings=settings,
        )
        db.commit()
    except OtpCooldownError as exc:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail={"code": "otp_cooldown", "message": str(exc)},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_phone", "message": str(exc)},
        ) from exc
    except PhoneAuthUnavailableError as exc:
        # Disabled or unconfigured is a deployment state, not a server fault
        # and never a wrong number (gaps G1/G7).
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "phone_auth_unavailable",
                "message": "Phone sign-in is not available",
            },
        ) from exc
    except OtpDeliveryError as exc:
        # Nothing is committed: the challenge that was never delivered cannot
        # be verified and does not start a resend cooldown.
        db.rollback()
        logger.error("OTP_DELIVERY_FAILED error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={"code": "otp_delivery_failed", "message": "The code could not be sent"},
            headers={"Retry-After": "30"},
        ) from exc
    return OtpRequestedResponse(
        challenge_id=challenge.id,
        expires_in=settings.otp_ttl_seconds,
        resend_after=settings.otp_resend_cooldown_seconds,
    )


@router.post("/otp/verify", response_model=SessionResponse)
def otp_verify(
    payload: OtpVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    _rate_limit(
        settings,
        f"otp-verify:{token_digest(payload.challenge_id, settings)}",
        limit=settings.otp_max_attempts + 1,
        window_seconds=settings.otp_ttl_seconds,
    )
    try:
        user = verify_otp(
            db,
            challenge_id=payload.challenge_id,
            code=normalize_digits(payload.code),
            settings=settings,
        )
        tokens = create_login_session(
            db,
            user=user,
            user_agent=request.headers.get("user-agent"),
            ip_address=_client_ip(request),
            settings=settings,
        )
        db.commit()
    except OtpVerificationError as exc:
        # Failed attempts are security state and must survive the rejected request.
        db.commit()
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_otp", "message": str(exc)},
        ) from exc
    except IdentityLinkConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_otp", "message": str(exc)},
        ) from exc
    _set_session_cookies(response, tokens, settings)
    return _session_response(db, user, tokens.access_expires_in)


@router.get("/google/start")
def google_start(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        url = start_google_oauth(db, settings=settings)
        db.commit()
    except GoogleOAuthError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "google_auth_unavailable", "message": str(exc)},
        ) from exc
    return RedirectResponse(url=url, status_code=302)


@router.get("/google/link/start")
def google_link_start(
    actor: Actor = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        url = start_google_oauth(
            db, intent="link", link_user_id=actor.user.id, settings=settings
        )
        db.commit()
    except GoogleOAuthError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={"code": "google_link_failed", "message": str(exc)},
        ) from exc
    return RedirectResponse(url=url, status_code=302)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    state: str,
    code: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        user, redirect_path = await complete_google_oauth(
            db, state=state, code=code, settings=settings
        )
        tokens = create_login_session(
            db,
            user=user,
            user_agent=request.headers.get("user-agent"),
            ip_address=_client_ip(request),
            settings=settings,
        )
        db.commit()
    except AccountLinkRequiredError:
        db.rollback()
        query = urlencode({"error": "account_link_required"})
        return RedirectResponse(f"{settings.frontend_url}/login?{query}", status_code=302)
    except GoogleOAuthError:
        db.rollback()
        query = urlencode({"error": "google_auth_failed"})
        return RedirectResponse(f"{settings.frontend_url}/login?{query}", status_code=302)
    response = RedirectResponse(f"{settings.public_url}{redirect_path}", status_code=302)
    _set_session_cookies(response, tokens, settings)
    return response
