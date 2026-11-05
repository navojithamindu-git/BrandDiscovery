from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.collectors.base import NormalizedComment, NormalizedPost, PagedResult
from app.collectors.meta import MetaClient
from app.config import Settings
from app.db import Base
from app.models import Brand, CollectionJob, Comment, JobStatus, Post, SocialAccount
from app.services.collector_service import collect_account, recover_stale_jobs


class FakeCollector:
    def __init__(self, client): self.client = client
    def collect(self, account_id, cursor=None, exhaustive=False):
        return PagedResult([NormalizedPost("post-1", "https://url", "brand mention", "IMAGE", "https://image", datetime.now(timezone.utc), comments=[NormalizedComment("comment-1", "hello", datetime.now(timezone.utc)), NormalizedComment("reply-1", "reply", datetime.now(timezone.utc), parent_comment_id="comment-1")])], "cursor-1")


def test_service_deduplicates_posts_and_comments(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    brand = Brand(name="Acme"); db.add(brand); db.commit()
    account = SocialAccount(brand_id=brand.id, platform="instagram", platform_account_id="ig", token_env_var="TEST_TOKEN"); db.add(account); db.commit()
    monkeypatch.setenv("TEST_TOKEN", "token")
    monkeypatch.setattr("app.services.collector_service.InstagramCollector", FakeCollector)
    settings = Settings(database_url=str(tmp_path / "test.db"))
    first = collect_account(db, account, settings, client=MetaClient(settings, "token", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))), backfill=True)
    second = collect_account(db, account, settings, client=MetaClient(settings, "token", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))), backfill=True)
    assert first.posts_collected == 1 and first.comments_collected == 2
    assert second.posts_collected == 0 and second.comments_collected == 0
    assert db.scalar(select(Post).where(Post.platform_post_id == "post-1")) is not None
    assert len(db.scalars(select(Comment)).all()) == 2
    assert account.cursor == "cursor-1"


def test_stale_running_job_is_recovered(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    brand = Brand(name="Recovery Co"); db.add(brand); db.commit()
    account = SocialAccount(brand_id=brand.id, platform="facebook", platform_account_id="page"); db.add(account); db.commit()
    job = CollectionJob(account_id=account.id, status=JobStatus.RUNNING, started_at=datetime.now(timezone.utc) - timedelta(hours=1)); db.add(job); db.commit()
    assert recover_stale_jobs(db) == 1
    db.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error_message.startswith("Recovered stale")

