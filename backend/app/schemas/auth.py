from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None

    @field_validator("password")
    @classmethod
    def _fits_bcrypt(cls, value: str) -> str:
        # Measured in bytes: bcrypt ignores everything after 72, so a longer password would silently be a shorter one.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes (72 plain characters; fewer if it has accents or emoji)")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str | None

    model_config = {"from_attributes": True}
