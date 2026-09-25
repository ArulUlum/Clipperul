from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import SocialProvider
from app.models.social_account import SocialAccount


class SocialAccountResponse(BaseModel):
    id: UUID
    provider: SocialProvider
    account_name: str
    connected_at: datetime | None

    model_config = {"from_attributes": True}


def build_social_account_response(account: SocialAccount) -> SocialAccountResponse:
    return SocialAccountResponse(
        id=account.id,
        provider=account.provider,
        account_name=account.account_name,
        connected_at=account.connected_at,
    )


class SocialAccountConnectResponse(BaseModel):
    authorization_url: str
