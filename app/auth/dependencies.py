from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.auth.authorization import WorkspaceAuthorizer
from app.auth.idempotency import IdempotencyService
from app.auth.security import TokenDecodeError, decode_access_token
from app.core.config import settings
from app.models.user import User


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    id: int
    email: str
    is_demo: bool = False


_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    access_cookie: str | None = Cookie(
        default=None,
        alias="techpilot_access_token",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> AuthPrincipal:
    token = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    elif access_cookie:
        token = access_cookie

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id, token_email = decode_access_token(
            token=token,
            secret_key=settings.auth_secret_key,
        )
    except TokenDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.email.lower() != token_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_demo and not settings.auth_demo_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="demo authentication is disabled",
        )
    return AuthPrincipal(id=user.id, email=user.email, is_demo=user.is_demo)


def get_workspace_authorizer(
    session: AsyncSession = Depends(get_db_session),
) -> WorkspaceAuthorizer:
    return WorkspaceAuthorizer(session=session)


def get_idempotency_service(
    session: AsyncSession = Depends(get_db_session),
) -> IdempotencyService:
    return IdempotencyService(session=session)
