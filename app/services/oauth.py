import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ..collectors.meta import MetaClient
from ..collectors.base import APIError
from ..config import Settings
from .token_vault import TokenVault


@dataclass
class PendingConnection:
    brand_id: int
    pages: list[dict]
    expires_at: float


_pending: dict[str, PendingConnection] = {}
_used_states: set[str] = set()


def _secret(settings: Settings) -> str:
    return settings.meta_oauth_state_secret or settings.meta_app_secret or "development-only-change-me"


def create_state(brand_id: int, settings: Settings) -> str:
    payload = f"{brand_id}:{int(time.time())}:{secrets.token_urlsafe(18)}"
    signature = hmac.new(_secret(settings).encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_state(state: str, settings: Settings) -> int:
    parts = state.split(":")
    if len(parts) != 4:
        raise ValueError("invalid OAuth state")
    payload = ":".join(parts[:3])
    expected = hmac.new(_secret(settings).encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(parts[3], expected) or state in _used_states:
        raise ValueError("invalid OAuth state")
    try:
        brand_id, issued_at = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("invalid OAuth state") from exc
    if time.time() - issued_at > settings.meta_oauth_state_ttl_seconds:
        raise ValueError("OAuth state expired")
    _used_states.add(state)
    return brand_id


def authorization_url(settings: Settings, brand_id: int) -> str:
    if not settings.meta_app_id:
        raise ValueError("META_APP_ID is required")
    state = create_state(brand_id, settings)
    params = {"client_id": settings.meta_app_id, "redirect_uri": settings.meta_oauth_redirect_uri, "state": state, "response_type": "code"}
    if settings.meta_login_config_id:
        params["config_id"] = settings.meta_login_config_id
    else:
        params["scope"] = "pages_show_list,pages_read_engagement,pages_read_user_content,instagram_basic,instagram_manage_comments"
    return f"https://www.facebook.com/{settings.meta_graph_version}/dialog/oauth?{urlencode(params)}"


def exchange_code(settings: Settings, code: str) -> str:
    if not settings.meta_app_id or not settings.meta_app_secret:
        raise ValueError("META_APP_ID and META_APP_SECRET are required")
    with httpx.Client(base_url=f"{settings.meta_graph_url}/{settings.meta_graph_version}", timeout=settings.request_timeout_seconds) as client:
        response = client.get("/oauth/access_token", params={"client_id": settings.meta_app_id, "client_secret": settings.meta_app_secret, "redirect_uri": settings.meta_oauth_redirect_uri, "code": code})
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if response.status_code >= 400 or not body.get("access_token"):
        raise ValueError(body.get("error", {}).get("message", "Meta OAuth code exchange failed"))
    return body["access_token"]


def _business_portfolio_pages(client: MetaClient) -> list[dict]:
    """Find Page assets when Meta does not populate /me/accounts.

    Business Portfolio endpoints are not available for every app/user, so this
    is deliberately best-effort. The normal /me/accounts result remains the
    primary source and this fallback never replaces a non-empty result.
    """
    try:
        businesses = client.all_pages("/me/businesses", {"fields": "id", "limit": 100})
    except APIError:
        return []

    pages: list[dict] = []
    seen: set[str] = set()
    fields = "id,name,access_token,tasks,instagram_business_account"
    for business in businesses:
        business_id = business.get("id")
        if not business_id:
            continue
        for relation in ("owned_pages", "client_pages"):
            try:
                candidates = client.all_pages(f"/{business_id}/{relation}", {"fields": fields, "limit": 100})
            except APIError:
                continue
            for page in candidates:
                page_id = page.get("id")
                if page_id and page_id not in seen:
                    seen.add(page_id)
                    pages.append(page)
    return pages


def discover_pages(settings: Settings, user_token: str, brand_id: int) -> str:
    client = MetaClient(settings, user_token)
    try:
        pages = client.all_pages("/me/accounts", {"fields": "id,name,access_token,tasks,instagram_business_account", "limit": 100})
        if not pages:
            pages = _business_portfolio_pages(client)
    finally:
        client.close()
    selection_token = secrets.token_urlsafe(32)
    _pending[selection_token] = PendingConnection(brand_id=brand_id, pages=pages, expires_at=time.time() + settings.meta_oauth_state_ttl_seconds)
    # The user token is never returned; page tokens are held only in this short-lived server-side record.
    return selection_token


def take_page(settings: Settings, selection_token: str, page_id: str) -> tuple[int, dict, str]:
    pending = _pending.pop(selection_token, None)
    if not pending or pending.expires_at < time.time():
        raise ValueError("page selection expired")
    page = next((item for item in pending.pages if item.get("id") == page_id), None)
    if not page or not page.get("access_token"):
        raise ValueError("Page is not available in this authorization")
    token_ref = f"meta:page:{page_id}:{secrets.token_urlsafe(10)}"
    TokenVault(settings.meta_token_encryption_key, settings.meta_token_vault_path).set(token_ref, page["access_token"])
    safe_page = {key: page.get(key) for key in ("id", "name", "tasks", "instagram_business_account")}
    return pending.brand_id, safe_page, token_ref


def pending_pages(selection_token: str) -> list[dict]:
    pending = _pending.get(selection_token)
    if not pending or pending.expires_at < time.time():
        raise ValueError("page selection expired")
    return [{key: page.get(key) for key in ("id", "name", "tasks", "instagram_business_account")} for page in pending.pages]

