import csv
import hashlib
import hmac
import io
import json
import logging
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import get_settings
from .db import Base, SessionLocal, engine, get_db, initialize_database
from .models import Brand, CollectionError, CollectionJob, Comment, JobStatus, Post, SocialAccount, WebhookDelivery
from .schemas import AccountCreate, AccountOut, BrandCreate, BrandOut, JobOut, PageSelection
from .services.collector_service import collect_account
from .services.mention_discovery import CollectedContentProvider, UnsupportedPublicSearchProvider
from .services.oauth import authorization_url, discover_pages, exchange_code, pending_pages, take_page, verify_state
from .services.token_vault import TokenVault, TokenVaultError
from .services.scheduler import build_scheduler

logging.basicConfig(level=get_settings().log_level)
scheduler = build_scheduler(get_settings())


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    scheduler.start(); yield; scheduler.shutdown(wait=False)


app = FastAPI(title="Brand Discovery Collector", version="0.1.0", lifespan=lifespan)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/health")
def health(): return {"status": "ok"}


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/ui/")


@app.post("/brands", response_model=BrandOut, status_code=201)
def create_brand(payload: BrandCreate, db: Session = Depends(get_db)):
    brand = Brand(**payload.model_dump()); db.add(brand)
    try: db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(409, "brand name already exists") from exc
    db.refresh(brand); return brand


@app.get("/brands", response_model=list[BrandOut])
def brands(db: Session = Depends(get_db)):
    return db.scalars(select(Brand).order_by(Brand.id)).all()


@app.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    if not db.get(Brand, payload.brand_id): raise HTTPException(404, "brand not found")
    account = SocialAccount(**payload.model_dump()); db.add(account)
    try: db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(409, "account already exists for this platform ID") from exc
    db.refresh(account); return account


@app.get("/accounts", response_model=list[AccountOut])
def accounts(brand_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(SocialAccount).order_by(SocialAccount.id)
    if brand_id:
        stmt = stmt.where(SocialAccount.brand_id == brand_id)
    return db.scalars(stmt).all()


@app.post("/accounts/{account_id}/collect", response_model=JobOut, status_code=202)
def collect(account_id: int, backfill: bool = Query(False), db: Session = Depends(get_db)):
    account = db.get(SocialAccount, account_id)
    if not account: raise HTTPException(404, "account not found")
    settings = get_settings()
    if not backfill:
        return collect_account(db, account, settings)
    last_job = None
    previous_cursor = object()
    for _ in range(settings.max_backfill_pages_per_request):
        cursor_before = account.cursor
        last_job = collect_account(db, account, settings, backfill=True)
        db.refresh(account)
        if last_job.status == JobStatus.FAILED or not account.cursor or account.cursor == cursor_before or account.cursor == previous_cursor:
            break
        previous_cursor = account.cursor
    return last_job


@app.get("/auth/meta/start")
def meta_auth_start(brand_id: int, db: Session = Depends(get_db)):
    if not db.get(Brand, brand_id):
        raise HTTPException(404, "brand not found")
    try:
        return RedirectResponse(authorization_url(get_settings(), brand_id))
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/auth/meta/callback")
def meta_auth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(400, f"Meta authorization failed: {error}")
    if not code or not state:
        raise HTTPException(400, "Meta authorization requires code and state")
    settings = get_settings()
    try:
        brand_id = verify_state(state, settings)
        user_token = exchange_code(settings, code)
        selection_token = discover_pages(settings, user_token, brand_id)
        return RedirectResponse(f"/ui/?selection_token={selection_token}")
    except (ValueError, TokenVaultError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/auth/meta/pending")
def meta_pending(selection_token: str):
    try:
        return {"selection_token": selection_token, "pages": pending_pages(selection_token)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/auth/meta/select", response_model=AccountOut, status_code=201)
def meta_select_page(payload: PageSelection, db: Session = Depends(get_db)):
    try:
        brand_id, page, token_ref = take_page(get_settings(), payload.selection_token, payload.page_id)
    except (ValueError, TokenVaultError) as exc:
        raise HTTPException(400, str(exc)) from exc
    account = db.scalar(select(SocialAccount).where(SocialAccount.platform == "facebook", SocialAccount.platform_account_id == page["id"]))
    if account is None:
        account = SocialAccount(brand_id=brand_id, platform="facebook", platform_account_id=page["id"], display_name=page.get("name"), token_ref=token_ref)
        db.add(account)
    else:
        account.brand_id, account.display_name, account.token_ref, account.connection_status = brand_id, page.get("name"), token_ref, "connected"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Page connection already exists") from exc
    db.refresh(account)
    instagram = page.get("instagram_business_account") or {}
    if instagram.get("id"):
        vault = TokenVault(get_settings().meta_token_encryption_key, get_settings().meta_token_vault_path)
        ig_ref = f"meta:instagram:{instagram['id']}:{secrets.token_urlsafe(10)}"
        token = vault.get(token_ref)
        if token:
            vault.set(ig_ref, token)
            ig_account = db.scalar(select(SocialAccount).where(SocialAccount.platform == "instagram", SocialAccount.platform_account_id == instagram["id"]))
            if ig_account is None:
                db.add(SocialAccount(brand_id=brand_id, platform="instagram", platform_account_id=instagram["id"], display_name=f"Instagram for {page.get('name', page['id'])}", token_ref=ig_ref))
            else:
                ig_account.brand_id, ig_account.token_ref, ig_account.enabled, ig_account.connection_status = brand_id, ig_ref, True, "connected"
            db.commit()
    return account


@app.post("/accounts/{account_id}/disconnect", response_model=AccountOut)
def disconnect_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(SocialAccount, account_id)
    if not account:
        raise HTTPException(404, "account not found")
    if account.token_ref:
        try:
            TokenVault(get_settings().meta_token_encryption_key, get_settings().meta_token_vault_path).delete(account.token_ref)
        except TokenVaultError as exc:
            raise HTTPException(503, str(exc)) from exc
    account.enabled = False
    account.connection_status = "disconnected"
    account.token_ref = None
    db.commit(); db.refresh(account)
    return account


@app.get("/jobs", response_model=list[JobOut])
def jobs(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    return db.scalars(select(CollectionJob).order_by(desc(CollectionJob.id)).limit(limit)).all()


def _comment_dict(comment: Comment) -> dict:
    return {"id": comment.id, "post_id": comment.post_id, "platform": comment.platform, "platform_comment_id": comment.platform_comment_id, "parent_comment_id": comment.parent_comment_id, "text": comment.text, "published_at": comment.published_at, "collected_at": comment.collected_at, "metrics": comment.metrics}


def _post_dict(post: Post) -> dict:
    return {"id": post.id, "platform": post.platform, "platform_post_id": post.platform_post_id, "url": post.url, "text": post.text, "media_type": post.media_type, "media_url": post.media_url, "published_at": post.published_at, "collected_at": post.collected_at, "metrics": post.metrics, "comments": [_comment_dict(c) for c in post.comments]}


@app.get("/posts")
def posts(limit: int = Query(100, le=500), platform: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Post).order_by(desc(Post.published_at)).limit(limit)
    if platform: stmt = stmt.where(Post.platform == platform)
    return [_post_dict(post) for post in db.scalars(stmt).all()]


@app.get("/comments")
def comments(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    return [_comment_dict(comment) for comment in db.scalars(select(Comment).order_by(desc(Comment.published_at)).limit(limit)).all()]


@app.get("/errors")
def errors(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    return [{"id": e.id, "job_id": e.job_id, "account_id": e.account_id, "code": e.code, "message": e.message, "retryable": e.retryable, "occurred_at": e.occurred_at} for e in db.scalars(select(CollectionError).order_by(desc(CollectionError.id)).limit(limit)).all()]


@app.get("/export/{kind}")
def export_data(kind: str, format: str = Query("json", pattern="^(json|csv)$"), db: Session = Depends(get_db)):
    if kind == "posts": rows = [_post_dict(p) for p in db.scalars(select(Post).order_by(Post.id)).all()]
    elif kind == "comments": rows = [_comment_dict(c) for c in db.scalars(select(Comment).order_by(Comment.id)).all()]
    else: raise HTTPException(404, "export kind must be posts or comments")
    if format == "json": return rows
    output = io.StringIO(); fields = sorted({key for row in rows for key in row}); writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
    writer.writerows([{key: json.dumps(value, default=str) if isinstance(value, (dict, list)) else value for key, value in row.items()} for row in rows])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={kind}.csv"})


@app.get("/brands/{brand_id}/discover")
def discover(brand_id: int, db: Session = Depends(get_db)):
    brand = db.get(Brand, brand_id)
    if not brand: raise HTTPException(404, "brand not found")
    return {"brand_id": brand_id, "results": [CollectedContentProvider().discover(brand, db).__dict__, UnsupportedPublicSearchProvider().discover(brand, db).__dict__]}


@app.get("/webhooks/meta")
def verify_webhook(request: Request, hub_mode: str = Query(..., alias="hub.mode"), hub_challenge: str = Query(..., alias="hub.challenge"), hub_verify_token: str = Query(..., alias="hub.verify_token")):
    settings = get_settings()
    if hub_mode != "subscribe" or not settings.meta_webhook_verify_token or not hmac.compare_digest(hub_verify_token, settings.meta_webhook_verify_token): raise HTTPException(403, "webhook verification failed")
    return int(hub_challenge)


def _process_webhook_entry(platform: str, entry: dict) -> None:
    account_id = str(entry.get("id") or entry.get("uid") or "")
    if not account_id:
        return
    db = SessionLocal()
    try:
        account = db.scalar(select(SocialAccount).where(SocialAccount.platform == platform, SocialAccount.platform_account_id == account_id, SocialAccount.enabled.is_(True)))
        if account:
            collect_account(db, account, get_settings())
    finally:
        db.close()


@app.post("/webhooks/meta")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.body(); settings = get_settings(); signature = request.headers.get("x-hub-signature-256")
    if settings.meta_webhook_app_secret and (not signature or not hmac.compare_digest(signature, "sha256=" + hmac.new(settings.meta_webhook_app_secret.encode(), body, hashlib.sha256).hexdigest())): raise HTTPException(403, "invalid webhook signature")
    event_hash = hashlib.sha256(body).hexdigest()
    if db.scalar(select(WebhookDelivery).where(WebhookDelivery.event_hash == event_hash)):
        return {"accepted": True, "duplicate": True}
    payload = json.loads(body or b"{}")
    object_type = payload.get("object", "page")
    platform = "instagram" if object_type == "instagram" else "facebook"
    db.add(WebhookDelivery(event_hash=event_hash, platform=platform)); db.commit()
    for entry in payload.get("entry", []):
        background_tasks.add_task(_process_webhook_entry, platform, entry)
    return {"accepted": True, "entries": len(payload.get("entry", [])), "queued": len(payload.get("entry", []))}


app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

