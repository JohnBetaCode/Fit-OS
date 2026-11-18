"""Fit-OS: FastAPI + scheduler + drenador de la outbox en un solo proceso."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import (
    jobs,  # noqa: F401 — registra los handlers del scheduler
    scheduler,
)
from .core.auth import AccessMiddleware
from .core.settings import get_settings
from .wa import outbox
from .wa.webhook import recover_pending
from .wa.webhook import router as wa_router
from .web import STATIC_DIR, routes_exercises
from .web.routes import placeholder_router
from .web.routes import router as web_router
from .web.routes_admin import router as admin_router
from .web.routes_auth import router as auth_router
from .web.routes_body import router as body_router
from .web.routes_exercises import router as exercises_router
from .web.routes_labs import router as labs_router
from .web.routes_main import router as main_router
from .web.routes_train import router as train_router
from .web.routes_week import router as week_router
from .web.routes_welcome import router as welcome_router


def _setup_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _bootstrap(log) -> None:
    """Accounts, then default rows (profile, equipment, biomarkers) in every person's database. Idempotent.
    In development with no accounts yet: the old single database becomes `dev`'s, or `dev` starts empty."""
    from .core import accounts
    from .users import adopt_legacy, bootstrap, create_user, provision

    accounts.init()
    s = get_settings()
    if s.is_dev and not accounts.all_accounts():
        if s.legacy_db_path.is_file():
            adopt_legacy("dev", "Dev")
        else:
            create_user("dev", "Dev", password="", role="admin")
        accounts.update("dev", wa_chat_id=s.wa_gym_chat_id or "120363000000000000@g.us")
    for name in accounts.active_usernames():
        try:
            if s.is_dev:
                provision(name)  # migrations too: `make dev` has no deploy step
            else:
                bootstrap(name)
        except Exception:  # noqa: BLE001 — a person's database must not stop everyone else's
            log.exception("bootstrap incompleto para %s", name)
    routes_exercises.set_history_provider(_exercise_history)


def _exercise_history(db, exercise_id: str) -> list[dict]:
    from sqlalchemy import select

    from .core.models import WorkoutSession, WorkoutSet

    rows = db.scalars(select(WorkoutSet).join(WorkoutSession).where(WorkoutSet.exercise_id == exercise_id)
                      .order_by(WorkoutSession.date.desc(), WorkoutSet.id).limit(30)).all()
    return [{"date": s.session.date, "weight_kg": s.weight_kg, "reps": s.reps, "e1rm_kg": s.e1rm_kg} for s in rows]


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    s = get_settings()
    log = logging.getLogger("fitos")
    if s.app_env == "production" and not s.waha_webhook_hmac_key:
        raise RuntimeError("WAHA_WEBHOOK_HMAC_KEY es obligatoria en producción")
    if s.app_env != "test":
        _bootstrap(log)
    tasks = []
    if s.app_env != "test":
        tasks.append(asyncio.create_task(outbox.run_forever(), name="outbox"))
        tasks.append(asyncio.create_task(scheduler.run_forever(), name="scheduler"))
        await recover_pending()
    log.info("Fit-OS %s arriba (env=%s, waha=%s, llm=%s)", s.git_sha[:7], s.app_env, s.waha_mode, s.llm_mode)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(title="Fit-OS", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(AccessMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(wa_router)
app.include_router(web_router)
app.include_router(main_router)
app.include_router(exercises_router)
app.include_router(labs_router)
app.include_router(week_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(welcome_router)
app.include_router(train_router)
app.include_router(body_router)
app.include_router(placeholder_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "sha": get_settings().git_sha[:7]}
