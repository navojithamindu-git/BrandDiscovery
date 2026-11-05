import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
import httpx
from ..config import Settings
from .base import APIError


class MetaClient:
    def __init__(self, settings: Settings, token: str, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.token = token
        self.client = httpx.Client(base_url=f"{settings.meta_graph_url}/{settings.meta_graph_version}", timeout=settings.request_timeout_seconds, transport=transport)

    def close(self) -> None:
        self.client.close()

    def post(self, path: str, params: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.post(path, params=params, data=data)
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = body.get("error", {}).get("message", response.text)
            raise APIError(detail, code="oauth_error", retryable=False)
        return body

    def get(self, path: str, params: dict[str, Any], *, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        params = {**params, "access_token": self.token}
        if self.settings.meta_app_secret:
            import hashlib
            import hmac
            params["appsecret_proof"] = hmac.new(self.settings.meta_app_secret.encode(), self.token.encode(), hashlib.sha256).hexdigest()
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.client.get(path, params=params)
            except httpx.HTTPError as exc:
                if attempt >= self.settings.max_retries:
                    raise APIError("Meta API network error", code="network_error", retryable=True) from exc
                sleep(min(60.0, 2**attempt + 0.1 * attempt))
                continue
            try:
                body = response.json() if response.content else {}
            except ValueError:
                body = {}
            error_code = body.get("error", {}).get("code")
            if response.status_code == 429 or response.status_code >= 500 or error_code in (1, 2, 4, 17, 32, 613):
                if attempt >= self.settings.max_retries:
                    raise APIError("Meta API rate limit or server error", code="rate_limited", retryable=True)
                sleep(min(60.0, 2**attempt + 0.1 * attempt))
                continue
            if response.status_code in (401, 403):
                detail = body.get("error", {}).get("message", "invalid or unauthorized credentials")
                raise APIError(detail, code="invalid_credentials")
            if response.status_code >= 400:
                detail = body.get("error", {}).get("message", response.text)
                raise APIError(detail)
            return body
        raise APIError("Meta API request failed", retryable=True)

    def all_pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = None
        seen: set[str] = set()
        while True:
            current = dict(params)
            if cursor:
                current["after"] = cursor
            payload = self.get(path, current)
            items.extend(payload.get("data", []))
            cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not cursor or cursor in seen:
                return items
            seen.add(cursor)

    @staticmethod
    def dt(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

