from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def validate_password_strength(value: str) -> str:
    if len(value) < 10 or len(value) > 128:
        raise ValueError("Password must contain between 10 and 128 characters")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"[0-9]", value):
        raise ValueError("Password must contain at least one letter and one number")
    return value


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictRequest):
    email: EmailStr
    password: str
    display_name: str = Field(min_length=1, max_length=160)
    locale: Literal["fa", "en"] = "fa"

    _password_strength = field_validator("password")(validate_password_strength)


class LoginRequest(StrictRequest):
    identifier: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailRequest(StrictRequest):
    token: str = Field(min_length=20, max_length=300)


class ForgotPasswordRequest(StrictRequest):
    email: EmailStr


class ResetPasswordRequest(StrictRequest):
    token: str = Field(min_length=20, max_length=300)
    password: str

    _password_strength = field_validator("password")(validate_password_strength)


class OtpRequest(StrictRequest):
    phone: str = Field(min_length=7, max_length=30)


class OtpVerifyRequest(StrictRequest):
    challenge_id: str
    code: str = Field(pattern=r"^[0-9۰-۹٠-٩]{6}$")


class UserResponse(BaseModel):
    id: str
    email: str | None
    phone_e164: str | None
    display_name: str
    locale: str
    email_verified: bool
    phone_verified: bool
    is_active: bool
    system_role: Literal["user", "operator"]


class WorkspaceResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    role: str
    # ``ROLE_PERMISSIONS[role]`` as sorted strings.  It only drives what the
    # console shows; every route still enforces its own permission.
    permissions: list[str]


class SessionResponse(BaseModel):
    user: UserResponse
    workspaces: list[WorkspaceResponse]
    access_expires_in: int
    requires_verification: bool


class MessageResponse(BaseModel):
    status: str
    message: str


class OtpRequestedResponse(BaseModel):
    challenge_id: str
    expires_in: int
    resend_after: int


class AuthMethodsResponse(BaseModel):
    """Sign-in methods this deployment offers (Session B gap G1).

    A method is ``true`` only when it is enabled *and* its provider settings
    are present; it says nothing about the provider being reachable right now.
    """

    password: bool
    google: bool
    phone_otp: bool


class VerificationResendResponse(BaseModel):
    status: Literal["accepted", "already_verified"]
    resend_after: int
