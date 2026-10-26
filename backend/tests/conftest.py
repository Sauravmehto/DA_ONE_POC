"""Test environment, set before any test module imports the app.

`app.core.config.settings` is a module-level singleton built once, at first
import of app.core.config. Anything that wants to influence it via environment
variables therefore has to set them BEFORE that import happens.

Individual test modules used to do this at their own import time, which worked
only while they happened to be the first module pytest imported — an ordering
dependency that silently broke as soon as an alphabetically-earlier test file
was added. conftest.py is loaded before any test module, so this is the one
place the ordering is guaranteed.

Values are deliberately fake: tests must never reach a real Snowflake account
or OAuth endpoint.
"""

import os


def _default(name: str, value: str) -> None:
    """Set `name` unless it already has a value. An EMPTY variable counts as
    unset: `pipenv run` exports a developer's backend/.env, whose template
    leaves these blank, and a blank OAUTH_CLIENT_ID would otherwise win and
    fail the OAuth tests on that machine only."""
    if not os.environ.get(name):
        os.environ[name] = value


# Forced, not defaulted: tests must not write sessions or conversation files
# to disk, and a developer's backend/.env (CONVERSATION_STORE=file) would
# otherwise make one run read what an earlier run left behind.
os.environ["SESSION_BACKEND"] = "memory"
os.environ["CONVERSATION_STORE"] = "memory"
os.environ["SESSION_STORE"] = "memory"
_default("JWT_SECRET", "test-secret")

# OAuth config — the Connect Snowflake tests need a client id and account,
# without which the flow answers 503.
_default("OAUTH_CLIENT_ID", "test-client")
_default("OAUTH_CLIENT_SECRET", "test-secret")
_default("SNOWFLAKE_ACCOUNT", "test-account")
_default("SNOWFLAKE_WAREHOUSE", "TEST_WH")

# The engine's Snowflake client reads these at import time.
_default("SNOWFLAKE_USER", "test-user")

# Accounts go to a throwaway file, never the developer's own data/app.db.
import tempfile

os.environ["APP_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="app-db-"), "app.db")
# Forced, not defaulted: a developer's own ADMIN_EMAILS or SIGNUP_MODE in
# backend/.env must not decide who is an administrator in a test.
os.environ["SIGNUP_MODE"] = "invite"
os.environ["EMAIL_VERIFICATION_REQUIRED"] = "true"
os.environ["ADMIN_EMAILS"] = ""

if not os.environ.get("TOKEN_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet

    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()


import time

import pytest


@pytest.fixture
def fresh_accounts(tmp_path, monkeypatch):
    """An empty account file for one test."""
    from app.accounts import store
    from app.core.config import settings

    monkeypatch.setattr(settings, "APP_DB_PATH", str(tmp_path / "app.db"))
    store.reset_for_tests()
    yield
    store.reset_for_tests()


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Each test counts sign-in attempts from zero; the rate-limit tests set
    their own limits."""
    from app.core import rate_limit

    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


@pytest.fixture(autouse=True)
def _data_groups_not_set_up():
    """Every request reads the data-group rules; without this each test would
    open a Snowflake connection to find there are none. Tests about data
    groups replace the rules themselves."""
    from app.engine import data_groups

    data_groups._cache = (time.monotonic() + 3600, data_groups.Snapshot(configured=False))
    yield
    data_groups.invalidate()
