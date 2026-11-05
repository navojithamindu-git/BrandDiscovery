from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class APIError(Exception):
    def __init__(self, message: str, *, code: str = "api_error", retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass
class NormalizedComment:
    platform_comment_id: str
    text: str | None
    published_at: datetime | None
    parent_comment_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedPost:
    platform_post_id: str
    url: str | None
    text: str | None
    media_type: str | None
    media_url: str | None
    published_at: datetime | None
    metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    comments: list[NormalizedComment] = field(default_factory=list)


@dataclass
class PagedResult:
    items: list[NormalizedPost]
    next_cursor: str | None = None


@dataclass
class RawPage:
    items: list[dict[str, Any]]
    next_cursor: str | None


class Collector(Protocol):
    def collect(self, account_id: str, cursor: str | None = None, exhaustive: bool = False) -> PagedResult: ...

