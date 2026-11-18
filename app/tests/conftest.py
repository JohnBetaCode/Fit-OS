from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("WAHA_MODE", "fake")
os.environ.setdefault("LLM_MODE", "off")
os.environ.setdefault("WA_GYM_CHAT_ID", "120363999999999999@g.us")
os.environ.setdefault("WAHA_WEBHOOK_HMAC_KEY", "test-key")
os.environ.setdefault("WA_SEND_GAP_SECONDS", "0")
os.environ.setdefault("DEV_USER_LOGIN", "tester@example.com")
os.environ.setdefault("DATA_DIR", "/tmp/fitos-test-data")
# The existing suite asserts Spanish copy; English is covered by test_i18n.py.
os.environ.setdefault("DEFAULT_LANGUAGE", "es")

import pytest  # noqa: E402

from fitos.core import accounts, tenancy  # noqa: E402
from fitos.core import db as dbmod  # noqa: E402
from fitos.core.models import Base  # noqa: E402
from fitos.i18n import set_lang  # noqa: E402
from fitos.wa import client as waclient  # noqa: E402

TEST_USER = "tester"


@pytest.fixture(autouse=True)
def fresh_db():
    # One admin account with the WhatsApp group, as the single person most tests are about. Code outside any
    # user context (tests calling services directly) runs as them; production has no such fallback.
    accounts.init("sqlite:///:memory:")
    accounts.create(TEST_USER, "Tester", role="admin", wa_chat_id=os.environ["WA_GYM_CHAT_ID"])
    tenancy.set_fallback(TEST_USER)
    eng = dbmod.init_engine("sqlite:///:memory:", user=TEST_USER)
    Base.metadata.create_all(eng)
    fake = waclient.FakeWaha()
    waclient.set_client(fake)
    set_lang(None)
    yield
    set_lang(None)
    Base.metadata.drop_all(eng)
    dbmod.dispose()
    tenancy.set_fallback(None)
    waclient.set_client(None)


@pytest.fixture
def fake_wa():
    return waclient.get_client()


@pytest.fixture
def db():
    with dbmod.session_scope() as s:
        yield s
