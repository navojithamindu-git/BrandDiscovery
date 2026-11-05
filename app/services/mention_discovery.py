from dataclasses import dataclass
from typing import Protocol
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import Brand, Post


@dataclass
class DiscoveryResult:
    provider: str
    supported: bool
    records: list[dict]
    message: str


class DiscoveryProvider(Protocol):
    name: str
    def discover(self, brand: Brand, db: Session) -> DiscoveryResult: ...


class CollectedContentProvider:
    name = "collected_content"
    def discover(self, brand: Brand, db: Session) -> DiscoveryResult:
        terms = [brand.name, *(brand.aliases or []), *(brand.keywords or [])]
        terms = [term.casefold() for term in terms if term.strip()]
        records = []
        posts = db.scalars(select(Post).where(Post.account_id.in_(select(Post.account_id).where(Post.account.has(brand_id=brand.id))))).all()
        for post in posts:
            if any(term in f"{post.text or ''} {post.url or ''}".casefold() for term in terms):
                records.append({"type": "post", "id": post.id, "platform": post.platform, "url": post.url})
            for comment in post.comments:
                if any(term in (comment.text or "").casefold() for term in terms):
                    records.append({"type": "comment", "id": comment.id, "post_id": post.id, "platform": comment.platform})
        return DiscoveryResult(self.name, True, records, "Matched content already collected by authorized platform collectors.")


class UnsupportedPublicSearchProvider:
    name = "unrestricted_public_search"
    def discover(self, brand: Brand, db: Session) -> DiscoveryResult:
        return DiscoveryResult(self.name, False, [], "Unrestricted Facebook/Instagram keyword search is not claimed or implemented; use approved Meta discovery features and authorized accounts.")

