import logging
import os
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..collectors import APIError, FacebookCollector, InstagramCollector
from ..collectors.meta import MetaClient
from ..config import Settings
from ..models import CollectionError, CollectionJob, Comment, JobStatus, Post, SocialAccount
from .token_vault import TokenVault, TokenVaultError

logger = logging.getLogger(__name__)


def collect_account(db: Session, account: SocialAccount, settings: Settings, *, client: MetaClient | None = None, backfill: bool = False) -> CollectionJob:
    job = CollectionJob(account_id=account.id, status=JobStatus.RUNNING, started_at=datetime.now(timezone.utc))
    db.add(job); db.commit()
    token = os.getenv(account.token_env_var) if account.token_env_var else settings.meta_access_token
    if not token and account.token_ref:
        try:
            token = TokenVault(settings.meta_token_encryption_key, settings.meta_token_vault_path).get(account.token_ref)
        except TokenVaultError as exc:
            return _fail(db, job, account, APIError(str(exc), code="token_store_error"))
    if not token:
        return _fail(db, job, account, APIError("No access token configured", code="invalid_credentials"))
    own_client = client is None
    api = client or MetaClient(settings, token)
    try:
        collector = FacebookCollector(api) if account.platform == "facebook" else InstagramCollector(api)
        # Recent syncs always start at the newest page so new posts are visible.
        # Backfills resume from the durable cursor and walk every available page.
        result = collector.collect(account.platform_account_id, account.cursor if backfill else None, exhaustive=False)
        for normalized in result.items:
            post = db.scalar(select(Post).where(Post.platform == account.platform, Post.platform_post_id == normalized.platform_post_id))
            if post is None:
                post = Post(account_id=account.id, platform=account.platform, platform_post_id=normalized.platform_post_id)
                db.add(post); db.flush(); job.posts_collected += 1
            post.url, post.text, post.media_type, post.media_url = normalized.url, normalized.text, normalized.media_type, normalized.media_url
            post.published_at, post.metrics, post.metadata_json = normalized.published_at, normalized.metrics, normalized.metadata
            for normalized_comment in normalized.comments:
                comment = db.scalar(select(Comment).where(Comment.platform == account.platform, Comment.platform_comment_id == normalized_comment.platform_comment_id))
                if comment is None:
                    db.add(Comment(post_id=post.id, platform=account.platform, platform_comment_id=normalized_comment.platform_comment_id, parent_comment_id=normalized_comment.parent_comment_id, text=normalized_comment.text, published_at=normalized_comment.published_at, metrics=normalized_comment.metrics, metadata_json=normalized_comment.metadata)); job.comments_collected += 1
                else:
                    comment.text, comment.metrics, comment.metadata_json = normalized_comment.text, normalized_comment.metrics, normalized_comment.metadata
        account.cursor = result.next_cursor if backfill else account.cursor
        account.last_collected_at = datetime.now(timezone.utc)
        job.next_cursor, job.status, job.finished_at = result.next_cursor, JobStatus.SUCCEEDED, datetime.now(timezone.utc)
        db.commit(); return job
    except APIError as exc:
        return _fail(db, job, account, exc)
    finally:
        if own_client: api.close()


def _fail(db: Session, job: CollectionJob, account: SocialAccount, exc: APIError) -> CollectionJob:
    job.status, job.finished_at, job.error_message = JobStatus.FAILED, datetime.now(timezone.utc), str(exc)
    if exc.code == "invalid_credentials":
        account.connection_status = "reauthorization_required"
    db.add(CollectionError(job_id=job.id, account_id=account.id, code=exc.code, message=str(exc), retryable=exc.retryable)); db.commit()
    logger.warning("collection failed for account %s: %s", account.id, exc.code)
    return job


def recover_stale_jobs(db: Session, max_age_minutes: int = 30) -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_minutes * 60
    recovered = 0
    for job in db.scalars(select(CollectionJob).where(CollectionJob.status == JobStatus.RUNNING)).all():
        if job.started_at and job.started_at.timestamp() < cutoff:
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)
            job.error_message = "Recovered stale running job after application restart"
            db.add(CollectionError(job_id=job.id, account_id=job.account_id, code="job_recovered", message=job.error_message, retryable=True))
            recovered += 1
    if recovered:
        db.commit()
    return recovered

