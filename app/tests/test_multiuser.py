"""Several people on one server: each one's data stays in their own database, whatever the way in (web,
admin view-as, WhatsApp, scheduler, outbox, CLI), and the single-person install is adopted without losing rows."""

from __future__ import annotations

import sqlite3
from datetime import date

import bcrypt
import pytest
from factories import log_session, make_exercise
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fitos.core import accounts, sessions, tenancy
from fitos.core import db as dbmod
from fitos.core.models import Base, BodyMetric, JobRun, WaMessage
from fitos.core.settings import get_settings
from fitos.main import app

SECRET = "s3cr3t-de-prueba"
PROXY = {"X-Fitos-Proxy": SECRET}
GYM = "120363999999999999@g.us"


@pytest.fixture
def alex():
    """A second, web-only person with their own (in-memory) database."""
    accounts.create("alex", "Alex", password_hash=bcrypt.hashpw(b"clave-de-alex", bcrypt.gensalt(rounds=4)).decode())
    eng = dbmod.init_engine("sqlite:///:memory:", user="alex")
    Base.metadata.create_all(eng)
    yield "alex"
    dbmod.dispose("alex")


@pytest.fixture
def prod(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(s, "dev_user_login", "")
    monkeypatch.setattr(s, "trust_tailscale_headers", False)
    monkeypatch.setattr(s, "proxy_secret", SECRET)
    accounts.set_password("tester", "clave-de-tester")
    sessions.reset_failures()
    yield s
    sessions.reset_failures()


def _signed_in(user: str, password: str) -> TestClient:
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    r = c.post("/entrar", headers=PROXY, data={"username": user, "password": password})
    assert r.status_code == 303, r.text
    return c


def _count(user: str, model) -> int:
    with tenancy.as_user(user), dbmod.session_scope() as s:
        return s.scalar(select(func.count()).select_from(model))


def _tester_session() -> int:
    with tenancy.as_user("tester"), dbmod.session_scope() as s:
        make_exercise(s, "ex1", name="Sentadilla del dueño")
        return log_session(s, date(2026, 11, 17), "ex1", [(8, 60, 2)]).id


# ── web ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_user_sees_only_their_own_data(prod, alex):
    sid = _tester_session()
    with _signed_in("alex", "clave-de-alex") as c:
        assert c.get(f"/entrenos/{sid}", headers=PROXY).status_code == 404  # not in his database
        assert "Sentadilla del dueño" not in c.get("/entrenos", headers=PROXY).text
        assert c.post("/progreso/peso", headers=PROXY, data={"weight_kg": "80"}).status_code == 303
        assert c.get("/admin", headers=PROXY).status_code == 403
        c.post("/ver-como", headers=PROXY, data={"user": "tester"})  # not an admin: ignored
        c.cookies.set("fitos_view", "tester")  # and a forged cookie doesn't help
        assert c.get(f"/entrenos/{sid}", headers=PROXY).status_code == 404
    assert _count("alex", BodyMetric) == 1 and _count("tester", BodyMetric) == 0
    with _signed_in("tester", "clave-de-tester") as c:
        assert c.get(f"/entrenos/{sid}", headers=PROXY).status_code == 200


def test_admin_views_another_account_read_only(prod, alex):
    sid = _tester_session()
    with _signed_in("tester", "clave-de-tester") as c:
        page = c.get("/admin", headers=PROXY)
        assert page.status_code == 200 and "Alex" in page.text
        assert c.post("/ver-como", headers=PROXY, data={"user": "alex"}).status_code == 303
        r = c.get("/progreso", headers=PROXY)
        assert r.status_code == 200 and "solo lectura" in r.text and "Alex" in r.text
        assert c.get(f"/entrenos/{sid}", headers=PROXY).status_code == 404  # his data, not mine
        assert c.post("/progreso/peso", headers=PROXY, data={"weight_kg": "80"}).status_code == 403
        assert c.get("/admin", headers=PROXY).status_code == 200  # admin pages still work
        c.post("/ver-como", headers=PROXY, data={"user": "tester"})
        assert c.get(f"/entrenos/{sid}", headers=PROXY).status_code == 200
        assert c.post("/progreso/peso", headers=PROXY, data={"weight_kg": "81"}).status_code == 303
    assert _count("alex", BodyMetric) == 0 and _count("tester", BodyMetric) == 1


def test_admin_creates_and_manages_users(prod, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    with _signed_in("tester", "clave-de-tester") as c:
        r = c.post("/admin/usuarios", headers=PROXY,
                   data={"username": "ana", "display_name": "Ana", "password": "corta", "role": "user"})
        assert r.status_code == 400 and accounts.get("ana") is None
        r = c.post("/admin/usuarios", headers=PROXY,
                   data={"username": "Ana!", "display_name": "Ana", "password": "una-clave-larga", "role": "user"})
        assert r.status_code == 400
        r = c.post("/admin/usuarios", headers=PROXY,
                   data={"username": "ana", "display_name": "Ana", "password": "una-clave-larga", "role": "user"})
        assert r.status_code == 303
        assert (tmp_path / "users" / "ana" / "fitos.sqlite").is_file()  # her own, migrated database
        assert c.post("/admin/usuarios/tester", headers=PROXY, data={"role": "user"}).status_code == 400  # last admin
    with _signed_in("ana", "una-clave-larga") as c:
        r = c.get("/", headers=PROXY)
        assert r.status_code == 303 and r.headers["location"] == "/bienvenida"  # new: the welcome flow first
        assert c.get("/bienvenida", headers=PROXY).status_code == 200
    with _signed_in("tester", "clave-de-tester") as c:
        c.post("/admin/usuarios/ana", headers=PROXY, data={"display_name": "Ana", "role": "user"})  # unchecked
    assert accounts.get("ana").active is False
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        assert c.post("/entrar", headers=PROXY, data={"username": "ana", "password": "una-clave-larga"}).status_code == 401
    dbmod.dispose("ana")


def test_admin_deletes_a_user_and_their_data_is_archived(prod, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    with _signed_in("tester", "clave-de-tester") as c:
        c.post("/admin/usuarios", headers=PROXY,
               data={"username": "ana", "display_name": "Ana", "password": "una-clave-larga", "role": "user"})
        assert "Eliminar usuario" in c.get("/admin", headers=PROXY).text
        r = c.post("/admin/usuarios/ana/eliminar", headers=PROXY, data={"confirm": "otra"})
        assert r.status_code == 400 and accounts.get("ana") is not None  # must type the name
        assert c.post("/admin/usuarios/tester/eliminar", headers=PROXY,
                      data={"confirm": "tester"}).status_code == 400  # not yourself
        r = c.post("/admin/usuarios/ana/eliminar", headers=PROXY, data={"confirm": "ana"})
        assert r.status_code == 303 and r.headers["location"] == "/admin?ok=deleted"
    assert accounts.get("ana") is None and not (tmp_path / "users" / "ana").exists()
    [archived] = list((tmp_path / "deleted").iterdir())
    assert archived.name.startswith("ana-") and (archived / "fitos.sqlite").is_file()  # nothing lost
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        assert c.post("/entrar", headers=PROXY, data={"username": "ana", "password": "una-clave-larga"}).status_code == 401
    with pytest.raises(ValueError, match="last admin"):
        accounts.delete("tester")


def test_only_an_admin_sets_integration_app_credentials(prod, alex):
    with _signed_in("alex", "clave-de-alex") as c:
        r = c.post("/integraciones/whoop/app", headers=PROXY, data={"client_id": "x", "client_secret": "y"})
        assert r.status_code == 403
        assert c.post("/integraciones/whoop/app/quitar", headers=PROXY).status_code == 403
        assert "Client Secret" not in c.get("/integraciones", headers=PROXY).text


# ── WhatsApp, outbox, scheduler ──────────────────────────────────────────────────────────────────────────
async def test_webhook_routes_by_group_and_drops_others(alex):
    from fitos.wa.webhook import ingest

    def event(chat: str, n: int) -> dict:
        return {"event": "message.any", "payload": {"id": f"true_{chat}_IN{n}", "from": chat, "fromMe": True,
                                                    "source": "app", "body": "hola"}}

    assert await ingest(event(GYM, 1)) == "accepted"
    assert await ingest(event("120363111111111111@g.us", 2)) == "foreign"  # nobody's group
    assert _count("tester", WaMessage) >= 1
    assert _count("alex", WaMessage) == 0


async def test_web_only_users_get_nothing_sent(alex, fake_wa):
    from fitos.wa.outbox import drain_once, enqueue_text

    with tenancy.as_user("alex"), dbmod.session_scope() as s:
        msg = enqueue_text(s, "solo para Alex")
        assert msg.status == "skipped" and msg.chat_id == ""
    with tenancy.as_user("tester"), dbmod.session_scope() as s:
        enqueue_text(s, "para el grupo")
    await drain_once()
    assert [m.text for m in fake_wa.sent] == ["para el grupo"]


async def test_scheduler_runs_for_each_person_in_their_database(alex, monkeypatch):
    from fitos import jobs, scheduler

    ran: list[tuple[str | None, str]] = []

    async def record(name):
        ran.append((tenancy.current(), name))
        return "ok"

    for name in ("waha_check", "session_timeouts", "morning_plan", "day_close", "weekly_summary"):
        monkeypatch.setitem(scheduler._handlers, name, lambda n=name: record(n))
    assert jobs  # handlers registered
    await scheduler.tick()
    assert ("tester", "waha_check") in ran and ("alex", "waha_check") not in ran  # server job: primary only
    assert ("tester", "session_timeouts") in ran and ("alex", "session_timeouts") in ran
    assert _count("alex", JobRun) >= 1 and _count("tester", JobRun) >= 2


def test_no_user_no_database():
    tenancy.set_fallback(None)
    with pytest.raises(tenancy.NoUserContext):
        dbmod.session_factory()


def test_cli_needs_to_know_whose_data(alex, capsys):
    from fitos.cli import main

    with pytest.raises(SystemExit, match="--user"):
        main(["plan", "show"])
    assert main(["--user", "alex", "plan", "show", "2026-11-18"]) == 1  # their (empty) database
    assert "no plan" in capsys.readouterr().out


# ── adopting the single-person install ───────────────────────────────────────────────────────────────────
def _legacy_install(data_dir) -> None:
    (data_dir / "db").mkdir(parents=True)
    (data_dir / "caddy").mkdir()
    con = sqlite3.connect(data_dir / "db" / "fitos.sqlite")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("create table workout_sessions (id integer primary key, date text)")
    con.executemany("insert into workout_sessions (date) values (?)", [("2026-11-17",), ("2026-11-18",)])
    con.commit()
    con.close()
    pw = bcrypt.hashpw(b"la-clave-de-siempre", bcrypt.gensalt(rounds=4)).decode()
    (data_dir / "caddy" / "users.caddy").write_text(f"admin {pw}\n#disabled-alex $2b$x\n")


def test_adopt_legacy_keeps_every_row(monkeypatch, tmp_path):
    from fitos.users import adopt_legacy

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    _legacy_install(tmp_path)
    accounts.init("sqlite:///:memory:")  # a fresh install: no accounts yet
    assert adopt_legacy("owner", "Owner") == "owner"

    new = tmp_path / "users" / "owner" / "fitos.sqlite"
    rows = sqlite3.connect(new).execute("select date from workout_sessions order by id").fetchall()
    assert rows == [("2026-11-17",), ("2026-11-18",)]
    assert not (tmp_path / "db" / "fitos.sqlite").exists()
    assert len(list((tmp_path / "db").glob("fitos.sqlite.adopted-*"))) >= 1  # kept, renamed, not deleted
    owner = accounts.get("owner")
    assert owner.is_admin and owner.wa_chat_id == GYM
    assert accounts.check_password("owner", "la-clave-de-siempre") is not None
    assert adopt_legacy("owner") is None  # nothing left to adopt


def test_adopt_legacy_refuses_to_guess(monkeypatch, tmp_path):
    from fitos.users import adopt_legacy

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    _legacy_install(tmp_path)
    with pytest.raises(RuntimeError, match="accounts already"):
        adopt_legacy("owner")  # the suite's account exists: never merge into someone
    assert (tmp_path / "db" / "fitos.sqlite").is_file()


def test_version_footer_by_role_and_no_welcome_redirect_while_viewing(prod, alex, monkeypatch):
    monkeypatch.setattr(prod, "git_sha", "dc4fe4b0123456789")
    monkeypatch.setattr(prod, "git_branch", "main")
    with tenancy.as_user("alex"), dbmod.session_scope() as s:
        from fitos.body.service import ensure_defaults

        ensure_defaults(s).onboarded = True
    with _signed_in("alex", "clave-de-alex") as c:
        page = c.get("/", headers=PROXY).text
        assert "Fit·OS dc4fe4b" in page and "main · " not in page  # people see the version, not the branch
    with tenancy.as_user("alex"), dbmod.session_scope() as s:
        ensure_defaults(s).onboarded = False
    with _signed_in("tester", "clave-de-tester") as c:
        c.post("/ver-como", headers=PROXY, data={"user": "alex"})
        r = c.get("/", headers=PROXY)
        assert r.status_code == 200 and "main · dc4fe4b" in r.text  # his Today, read-only: no redirect
