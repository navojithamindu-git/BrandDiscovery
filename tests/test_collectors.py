import httpx
import pytest
from app.collectors.facebook import FacebookCollector
from app.collectors.instagram import InstagramCollector
from app.collectors.meta import MetaClient
from app.config import Settings


def settings(**kwargs):
    return Settings(meta_graph_url="https://graph.test", meta_graph_version="v1", max_retries=2, **kwargs)


def test_facebook_posts_comments_and_replies():
    def handler(request):
        if request.url.path.endswith("/page/posts"):
            return httpx.Response(200, json={"data": [{"id": "p1", "message": "hello", "created_time": "2026-01-01T00:00:00Z", "permalink_url": "https://fb/p1", "attachments": {"data": [{"media_type": "VIDEO", "media": {"source": "https://cdn/video"}}]}, "likes": {"summary": {"total_count": 2}}, "comments": {"summary": {"total_count": 1}}}], "paging": {"cursors": {"after": "next"}}})
        if request.url.path.endswith("/p1/comments"):
            return httpx.Response(200, json={"data": [{"id": "c1", "message": "comment", "created_time": "2026-01-01T00:00:00Z"}]})
        if request.url.path.endswith("/c1/comments"):
            return httpx.Response(200, json={"data": [{"id": "r1", "message": "reply", "created_time": "2026-01-01T01:00:00Z"}]})
        return httpx.Response(200, json={"data": []})
    client = MetaClient(settings(), "secret", transport=httpx.MockTransport(handler))
    result = FacebookCollector(client).collect("page")
    assert result.next_cursor == "next"
    assert result.items[0].media_type == "VIDEO"
    assert [c.platform_comment_id for c in result.items[0].comments] == ["c1", "r1"]


def test_instagram_pagination_cursor_is_sent():
    seen = {}
    def handler(request):
        seen["after"] = request.url.params.get("after")
        return httpx.Response(200, json={"data": [], "paging": {"cursors": {"after": "next"}}})
    client = MetaClient(settings(), "secret", transport=httpx.MockTransport(handler))
    result = InstagramCollector(client).collect("ig", "previous")
    assert result.next_cursor == "next"
    assert seen["after"] == "previous"


def test_rate_limit_retries_then_succeeds():
    responses = iter([httpx.Response(429, json={}), httpx.Response(200, json={"data": []})])
    client = MetaClient(settings(), "secret", transport=httpx.MockTransport(lambda request: next(responses)))
    result = FacebookCollector(client).collect("page")
    assert result.items == []


def test_invalid_credentials_is_explicit():
    client = MetaClient(settings(), "secret", transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"message": "bad token"}})))
    with pytest.raises(Exception, match="bad token"):
        FacebookCollector(client).collect("page")


def test_facebook_exhaustive_backfill_walks_all_post_pages():
    calls = []
    def handler(request):
        calls.append(request.url.params.get("after"))
        if request.url.path.endswith("/page/posts"):
            if request.url.params.get("after") == "next-posts":
                return httpx.Response(200, json={"data": [{"id": "p2", "created_time": "2026-01-02T00:00:00Z"}]})
            return httpx.Response(200, json={"data": [{"id": "p1", "created_time": "2026-01-01T00:00:00Z"}], "paging": {"cursors": {"after": "next-posts"}}})
        return httpx.Response(200, json={"data": []})
    client = MetaClient(settings(), "secret", transport=httpx.MockTransport(handler))
    result = FacebookCollector(client).collect("page", exhaustive=True)
    assert [post.platform_post_id for post in result.items] == ["p1", "p2"]
    assert result.next_cursor is None
    assert calls[:2] == [None, "next-posts"]

