try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ModuleNotFoundError:  # Optional in constrained test environments.
    class BackgroundScheduler:
        def __init__(self, *args, **kwargs): pass
        def add_job(self, *args, **kwargs): pass
        def start(self): pass
        def shutdown(self, wait=False): pass
from sqlalchemy import select
from ..config import Settings
from ..db import SessionLocal
from ..models import SocialAccount
from .collector_service import collect_account, recover_stale_jobs


def collect_all(settings: Settings) -> None:
    db = SessionLocal()
    try:
        recover_stale_jobs(db)
        for account in db.scalars(select(SocialAccount).where(SocialAccount.enabled)).all(): collect_account(db, account, settings)
    finally: db.close()


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(lambda: collect_all(settings), "interval", minutes=settings.collection_interval_minutes, id="collect-all", replace_existing=True, max_instances=1)
    return scheduler

