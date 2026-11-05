from typing import Any
from .base import NormalizedComment, NormalizedPost, PagedResult
from .meta import MetaClient


class FacebookCollector:
    """Collects data owned by/authorized for a Page through the Graph API."""
    def __init__(self, client: MetaClient):
        self.client = client

    def collect(self, account_id: str, cursor: str | None = None, exhaustive: bool = False) -> PagedResult:
        params = {"fields": "id,message,permalink_url,created_time,attachments{media_type,media,url},likes.summary(true),comments.summary(true)", "limit": 50}
        posts: list[dict[str, Any]] = []
        next_cursor = cursor
        while True:
            request_params = dict(params)
            if next_cursor:
                request_params["after"] = next_cursor
            payload = self.client.get(f"/{account_id}/posts", request_params)
            posts.extend(payload.get("data", []))
            next_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not exhaustive or not next_cursor:
                break
        return PagedResult([self._post(item) for item in posts], next_cursor if not exhaustive else None)

    def _post(self, item: dict[str, Any]) -> NormalizedPost:
        attachment = (item.get("attachments", {}).get("data") or [{}])[0]
        comments = []
        for comment in self.client.all_pages(f"/{item['id']}/comments", {"fields": "id,message,created_time,like_count", "limit": 100}):
            comments.append(self._comment(comment))
            comments.extend(self._comment(reply, parent=comment.get("id")) for reply in self.client.all_pages(f"/{comment['id']}/comments", {"fields": "id,message,created_time,like_count", "limit": 100}))
        media = attachment.get("media", {})
        media_refs = [{"media_type": a.get("media_type"), "url": a.get("media", {}).get("image", {}).get("src") or a.get("media", {}).get("source")} for a in item.get("attachments", {}).get("data", [])]
        return NormalizedPost(item["id"], item.get("permalink_url"), item.get("message"), attachment.get("media_type"), media.get("image", {}).get("src") or media.get("source"), self.client.dt(item.get("created_time")), {"likes": item.get("likes", {}).get("summary", {}).get("total_count"), "comments": item.get("comments", {}).get("summary", {}).get("total_count")}, {"raw_id": item.get("id"), "media_refs": media_refs}, comments)

    def _comment(self, item: dict[str, Any], parent: str | None = None) -> NormalizedComment:
        return NormalizedComment(item["id"], item.get("message"), self.client.dt(item.get("created_time")), parent, {"likes": item.get("like_count")}, {"raw_id": item.get("id")})

