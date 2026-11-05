from typing import Any
from .base import NormalizedComment, NormalizedPost, PagedResult
from .meta import MetaClient


class InstagramCollector:
    """Collects media owned by/authorized for an Instagram professional account."""
    def __init__(self, client: MetaClient):
        self.client = client

    def collect(self, account_id: str, cursor: str | None = None, exhaustive: bool = False) -> PagedResult:
        params = {"fields": "id,caption,permalink,timestamp,media_type,media_url,thumbnail_url,like_count,comments_count", "limit": 50}
        media: list[dict[str, Any]] = []
        next_cursor = cursor
        while True:
            request_params = dict(params)
            if next_cursor:
                request_params["after"] = next_cursor
            payload = self.client.get(f"/{account_id}/media", request_params)
            media.extend(payload.get("data", []))
            next_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not exhaustive or not next_cursor:
                break
        return PagedResult([self._post(item) for item in media], next_cursor if not exhaustive else None)

    def _post(self, item: dict[str, Any]) -> NormalizedPost:
        comments = []
        for comment in self.client.all_pages(f"/{item['id']}/comments", {"fields": "id,text,timestamp,like_count", "limit": 100}):
            comments.append(self._comment(comment))
            comments.extend(self._comment(reply, parent=comment.get("id")) for reply in self.client.all_pages(f"/{comment['id']}/replies", {"fields": "id,text,timestamp,like_count", "limit": 100}))
        metadata = {"raw_id": item.get("id"), "media_refs": []}
        if item.get("media_type") == "CAROUSEL_ALBUM":
            metadata["media_refs"] = self.client.all_pages(f"/{item['id']}/children", {"fields": "id,media_type,media_url,thumbnail_url", "limit": 100})
        return NormalizedPost(item["id"], item.get("permalink"), item.get("caption"), item.get("media_type"), item.get("media_url") or item.get("thumbnail_url"), self.client.dt(item.get("timestamp")), {"likes": item.get("like_count"), "comments": item.get("comments_count")}, metadata, comments)

    def _comment(self, item: dict[str, Any], parent: str | None = None) -> NormalizedComment:
        return NormalizedComment(item["id"], item.get("text"), self.client.dt(item.get("timestamp")), parent, {"likes": item.get("like_count")}, {"raw_id": item.get("id")})

