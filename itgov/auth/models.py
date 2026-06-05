"""Pydantic models for authentication domain."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthenticatedUser(BaseModel):
    """Represents an authenticated user stored in the Flask session."""

    oid: str = Field(..., description="Microsoft Object ID (unique user identifier)")
    email: str = Field(..., description="User email address")
    name: str = Field(..., description="Display name")
    tenant_id: str = Field(..., description="Azure AD Tenant ID")
    preferred_username: str = Field(default="", description="UPN or email used to sign in")

    @classmethod
    def from_id_token_claims(cls, claims: dict) -> AuthenticatedUser:
        return cls(
            oid=claims["oid"],
            email=claims.get("email") or claims.get("preferred_username", ""),
            name=claims.get("name", ""),
            tenant_id=claims.get("tid", ""),
            preferred_username=claims.get("preferred_username", ""),
        )

    def to_session_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_session_dict(cls, data: dict) -> AuthenticatedUser:
        return cls.model_validate(data)


class TokenResponse(BaseModel):
    """Wraps the token bundle returned by MSAL after code exchange."""

    access_token: str
    id_token: str
    token_type: str = "Bearer"  # noqa: S105
    expires_in: int = 3600
    scope: str = ""
    id_token_claims: dict = Field(default_factory=dict)
