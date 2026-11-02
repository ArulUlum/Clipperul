import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin, pg_enum
from app.models.enums import SocialProvider


class SocialAccount(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "social_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[SocialProvider] = mapped_column(
        pg_enum(SocialProvider, "social_provider"), nullable=False
    )
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Who actually talks to the platform for this account: None = our own OAuth adapter for
    # `provider`; "zernio" = published through Zernio, where external_account_id is Zernio's
    # account id (see app.services.social.zernio).
    broker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Encrypted at rest via app.core.security.encrypt_token/decrypt_token — never serialized to the API.
    # Empty for brokered accounts: the broker holds the platform credentials, not us.
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="social_accounts")
    scheduled_posts: Mapped[list["ScheduledPost"]] = relationship(
        back_populates="social_account", cascade="all, delete-orphan"
    )
