from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.auth.dependencies import AuthPrincipal, get_current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.core.config import settings
from app.models.user import User


router = APIRouter(prefix="/auth", tags=["auth"])
_COOKIE_NAME = "techpilot_access_token"


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1:
            raise ValueError("email must contain one @")
        local, domain = normalized.split("@", 1)
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("email format is invalid")
        return normalized


class TokenRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class CurrentUserResponse(BaseModel):
    id: int
    email: str
    is_demo: bool


def _normalize_identifier(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "demo":
        return "demo@techpilot.local"
    return normalized


def _issue_token(user: User, response: Response) -> TokenResponse:
    token = create_access_token(
        user_id=user.id,
        email=user.email,
        secret_key=settings.auth_secret_key,
        expires_minutes=settings.auth_access_token_minutes,
    )
    max_age = settings.auth_access_token_minutes * 60
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.app_env.lower() == "production",
        samesite="lax",
        path="/",
    )
    return TokenResponse(access_token=token, expires_in_seconds=max_age)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    email = request.email
    if await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="email already registered")

    user = User(email=email, password_hash=hash_password(request.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="email already registered") from exc
    await session.refresh(user)
    return _issue_token(user, response)


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    request: TokenRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    email = _normalize_identifier(request.identifier)
    user = await session.scalar(select(User).where(User.email == email))
    if (
        user is None
        or not user.is_active
        or not verify_password(request.password, user.password_hash)
        or (user.is_demo and not settings.auth_demo_enabled)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_token(user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    response.delete_cookie(key=_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=CurrentUserResponse)
async def current_user(
    principal: Annotated[AuthPrincipal, Depends(get_current_user)],
) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=principal.id,
        email=principal.email,
        is_demo=principal.is_demo,
    )
