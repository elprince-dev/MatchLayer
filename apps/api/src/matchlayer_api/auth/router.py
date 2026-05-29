"""Auth_Router: /api/v1/auth/* endpoints.

Pure HTTP-shape concerns only — no business logic (Components and
Interfaces import-boundary rule). All mutations delegate to Auth_Service.
"""

from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.auth.schemas import (
    LoginRequest,
    MePatchRequest,
    MeResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    RegisterRequest,
    TokenPairResponse,
    UserResponse,
)
from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.dependencies import (
    UnauthenticatedError,
    csrf_required,
    get_current_user,
    rate_limit,
)
from matchlayer_api.core.errors import MatchLayerError
from matchlayer_api.core.security.cookies import (
    clear_csrf_cookie,
    clear_refresh_cookie,
    set_csrf_cookie,
    set_refresh_cookie,
)
from matchlayer_api.core.security.jwt import InvalidTokenError, verify_token
from matchlayer_api.core.security.passwords import PasswordTooShortError
from matchlayer_api.db.models import User
from matchlayer_api.services.auth import (
    Auth_Service,
    AuthenticateOutcome,
    RefreshOutcome,
    RegistrationOutcome,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_SessionDep = Annotated[AsyncSession, Depends(get_session)]
_SettingsDep = Annotated[Settings, Depends(get_settings)]
_CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_auth_cookies(response: Response, *, refresh_token: str, settings: Settings) -> None:
    """Set both refresh and CSRF cookies on a successful auth response."""
    csrf_token = secrets.token_urlsafe(32)
    max_age = settings.auth_refresh_token_ttl_seconds
    set_refresh_cookie(response, value=refresh_token, max_age=max_age, settings=settings)
    set_csrf_cookie(response, value=csrf_token, max_age=max_age, settings=settings)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _me_response(user: User) -> MeResponse:
    return MeResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenPairResponse,
    dependencies=[Depends(rate_limit(endpoint="register", by=("ip",)))],
)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: _SessionDep,
    settings: _SettingsDep,
) -> TokenPairResponse:
    svc = Auth_Service(settings=settings)
    try:
        outcome: RegistrationOutcome = await svc.register(
            session,
            email=body.email,
            password=body.password,
            display_name=body.display_name,
        )
    except PasswordTooShortError:
        raise MatchLayerError(
            "Password must be at least 12 characters.",
            status_code=422,
            error_type="validation_error",
            title="Validation Error",
        ) from None
    except ValueError as exc:
        raise MatchLayerError(
            str(exc),
            status_code=422,
            error_type="validation_error",
            title="Validation Error",
        ) from None

    if outcome.status == "existing_email":
        # Enumeration defense: same shape, 201, but no cookies (Req 1.6).
        response.status_code = status.HTTP_201_CREATED
        return TokenPairResponse(
            access_token="",
            user=_user_response(outcome.user),
        )

    assert outcome.access_token is not None
    assert outcome.refresh_token is not None
    _set_auth_cookies(response, refresh_token=outcome.refresh_token, settings=settings)
    await session.commit()
    return TokenPairResponse(
        access_token=outcome.access_token,
        user=_user_response(outcome.user),
    )


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=TokenPairResponse,
    dependencies=[Depends(rate_limit(endpoint="login", by=("email", "ip")))],
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: _SessionDep,
    settings: _SettingsDep,
) -> TokenPairResponse:
    # Stash email for rate-limit dependency.
    request.state.rate_limit_email = body.email

    svc = Auth_Service(settings=settings)
    outcome: AuthenticateOutcome = await svc.authenticate(
        session, email=body.email, password=body.password
    )

    if outcome.status == "invalid_credentials":
        await session.commit()
        raise MatchLayerError(
            "Email or password is incorrect.",
            status_code=401,
            error_type="invalid_credentials",
            title="Invalid Credentials",
        )

    if outcome.status == "locked":
        await session.commit()
        raise MatchLayerError(
            "Account is temporarily locked. Try again later.",
            status_code=423,
            error_type="account_locked",
            title="Account Locked",
        )

    assert outcome.access_token is not None
    assert outcome.refresh_token is not None
    assert outcome.user is not None
    _set_auth_cookies(response, refresh_token=outcome.refresh_token, settings=settings)
    await session.commit()
    return TokenPairResponse(
        access_token=outcome.access_token,
        user=_user_response(outcome.user),
    )


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
async def get_me(user: _CurrentUser) -> MeResponse:
    return _me_response(user)


# ---------------------------------------------------------------------------
# PATCH /me
# ---------------------------------------------------------------------------


@router.patch("/me", response_model=MeResponse)
async def patch_me(
    body: MePatchRequest,
    user: _CurrentUser,
    session: _SessionDep,
) -> MeResponse:
    if body.display_name is not None:
        svc = Auth_Service()
        try:
            await svc.update_display_name(session, user=user, new_display_name=body.display_name)
        except ValueError as exc:
            raise MatchLayerError(
                str(exc),
                status_code=422,
                error_type="validation_error",
                title="Validation Error",
            ) from None
        await session.commit()
    return _me_response(user)


# ---------------------------------------------------------------------------
# POST /refresh
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    dependencies=[
        Depends(csrf_required),
        Depends(rate_limit(endpoint="refresh", by=("ip",))),
    ],
)
async def refresh(
    request: Request,
    response: Response,
    session: _SessionDep,
    settings: _SettingsDep,
) -> TokenPairResponse:
    refresh_cookie = request.cookies.get("matchlayer_refresh")
    if not refresh_cookie:
        clear_refresh_cookie(response, settings=settings)
        clear_csrf_cookie(response, settings=settings)
        raise MatchLayerError(
            "Missing refresh cookie.",
            status_code=401,
            error_type="missing_refresh_cookie",
            title="Missing Refresh Cookie",
        )

    # Verify the JWT to extract sub and jti.
    try:
        claims = verify_token(refresh_cookie, expected_type="refresh")
    except InvalidTokenError:
        clear_refresh_cookie(response, settings=settings)
        clear_csrf_cookie(response, settings=settings)
        raise MatchLayerError(
            "Invalid refresh token.",
            status_code=401,
            error_type="invalid_refresh_token",
            title="Invalid Refresh Token",
        ) from None

    user_id = UUID(claims["sub"])
    jti = UUID(claims["jti"])

    svc = Auth_Service(settings=settings)
    outcome: RefreshOutcome = await svc.rotate_refresh_token(
        session, presented_jti=jti, user_id=user_id
    )

    if outcome.status == "invalid":
        clear_refresh_cookie(response, settings=settings)
        clear_csrf_cookie(response, settings=settings)
        await session.commit()
        raise MatchLayerError(
            "Invalid refresh token.",
            status_code=401,
            error_type="invalid_refresh_token",
            title="Invalid Refresh Token",
        )

    if outcome.status == "reused":
        clear_refresh_cookie(response, settings=settings)
        clear_csrf_cookie(response, settings=settings)
        await session.commit()
        raise MatchLayerError(
            "Refresh token reuse detected. All sessions revoked.",
            status_code=401,
            error_type="refresh_token_reused",
            title="Refresh Token Reused",
        )

    # Success — rotated.
    assert outcome.access_token is not None
    assert outcome.refresh_token is not None
    _set_auth_cookies(response, refresh_token=outcome.refresh_token, settings=settings)

    # Load user for response.
    user = await svc.get_user_by_id(session, user_id=user_id)
    if user is None:
        raise UnauthenticatedError()

    await session.commit()
    return TokenPairResponse(
        access_token=outcome.access_token,
        user=_user_response(user),
    )


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_required)],
)
async def logout(
    request: Request,
    response: Response,
    session: _SessionDep,
    settings: _SettingsDep,
) -> None:
    # Always clear cookies regardless of outcome.
    clear_refresh_cookie(response, settings=settings)
    clear_csrf_cookie(response, settings=settings)

    refresh_cookie = request.cookies.get("matchlayer_refresh")
    if not refresh_cookie:
        return

    try:
        claims = verify_token(refresh_cookie, expected_type="refresh")
    except InvalidTokenError:
        return

    jti = UUID(claims["jti"])
    svc = Auth_Service(settings=settings)
    await svc.logout(session, presented_jti=jti)
    await session.commit()


# ---------------------------------------------------------------------------
# POST /password-reset/request
# ---------------------------------------------------------------------------


@router.post(
    "/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit(endpoint="password_reset_request", by=("email", "ip")))],
)
async def password_reset_request(
    body: PasswordResetRequestRequest,
    request: Request,
    session: _SessionDep,
    settings: _SettingsDep,
) -> None:
    request.state.rate_limit_email = body.email
    svc = Auth_Service(settings=settings)
    await svc.request_password_reset(session, email=body.email)
    await session.commit()


# ---------------------------------------------------------------------------
# POST /password-reset/confirm
# ---------------------------------------------------------------------------


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit(endpoint="password_reset_confirm", by=("ip",)))],
)
async def password_reset_confirm(
    body: PasswordResetConfirmRequest,
    session: _SessionDep,
    settings: _SettingsDep,
) -> None:
    svc = Auth_Service(settings=settings)
    try:
        success = await svc.confirm_password_reset(
            session, token=body.token, new_password=body.new_password
        )
    except PasswordTooShortError:
        raise MatchLayerError(
            "Password must be at least 12 characters.",
            status_code=422,
            error_type="validation_error",
            title="Validation Error",
        ) from None
    except ValueError as exc:
        raise MatchLayerError(
            str(exc),
            status_code=422,
            error_type="validation_error",
            title="Validation Error",
        ) from None

    if not success:
        raise MatchLayerError(
            "This password-reset link is invalid or expired.",
            status_code=400,
            error_type="invalid_reset_token",
            title="Invalid Reset Token",
        )
    await session.commit()
