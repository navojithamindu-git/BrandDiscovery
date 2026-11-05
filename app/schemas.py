from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class BrandCreate(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class AccountCreate(BaseModel):
    brand_id: int
    platform: str
    platform_account_id: str
    username: str | None = None
    display_name: str | None = None
    token_env_var: str | None = None
    token_ref: str | None = None


class BrandOut(BrandCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class AccountOut(AccountCreate):
    id: int
    enabled: bool
    last_collected_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)


class JobOut(BaseModel):
    id: int
    account_id: int
    status: str
    posts_collected: int
    comments_collected: int
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None = None
    model_config = ConfigDict(from_attributes=True)


class PageSelection(BaseModel):
    selection_token: str
    page_id: str

