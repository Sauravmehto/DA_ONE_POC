"""Password reset, signing out everywhere, and moving an account made by the
old Snowflake sign-in onto a password without losing it (spec 0007, phase C)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.accounts import mailer, store
from app.accounts import service as accounts
from app.core.config import settings
from app.main import app

PASSWORD = "correct horse battery"
NEW = "a much better passphrase"


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str]]:
    """Every email, as (to, link), instead of sending it."""
    out: list[tuple[str, str]] = []
    monkeypatch.setattr(mailer, "reset_email", lambda to, link: out.append((to, link)))
    monkeypatch.setattr(mailer, "sign_in_setup_email", lambda to, link: out.append((to, link)))
    monkeypatch.setattr(mailer, "configured", lambda: True)
    return out


@pytest.fixture
def client(fresh_accounts, sent):
    with TestClient(app) as test_client:
        yield test_client


def _token(link: str) -> str:
    assert "#" in link, "the token travels in the fragment, never the path"
    return link.rsplit("#", 1)[1]


def _sign_in(client, email="ana@example.com", password=PASSWORD):
    return client.post("/api/v1/auth/password", json={"email": email, "password": password})


# --- asking for a reset ---------------------------------------------------------------

def test_forgot_answers_the_same_for_a_real_an_unknown_and_a_disabled_email(client, sent):
    accounts.create_account("ana@example.com", PASSWORD)
    off = accounts.create_account("off@example.com", PASSWORD)
    with store.write() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (off.user_id,))
    answers = [client.post("/api/v1/auth/password/forgot", json={"email": e})
               for e in ("ana@example.com", "nobody@example.com", "off@example.com")]
    assert {a.status_code for a in answers} == {202}
    assert len({a.text for a in answers}) == 1
    assert [to for to, _ in sent] == ["ana@example.com"]


def test_a_reset_signs_out_every_other_session_and_signs_this_browser_in(client, sent):
    accounts.create_account("ana@example.com", PASSWORD)
    other = TestClient(app)
    assert _sign_in(other).status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 200

    client.post("/api/v1/auth/password/forgot", json={"email": "ana@example.com"})
    token = _token(sent[-1][1])
    assert client.post("/api/v1/auth/password/reset/check", json={"token": token}).json() == {"email": "ana@example.com"}
    done = client.post("/api/v1/auth/password/reset", json={"token": token, "password": NEW})
    assert done.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200, "the resetting browser is signed in"

    assert other.get("/api/v1/auth/me").status_code == 401, "the old session is over"
    assert _sign_in(client, password=PASSWORD).status_code == 401
    assert _sign_in(client, password=NEW).status_code == 200


def test_a_reset_link_works_once(client, sent):
    accounts.create_account("ana@example.com", PASSWORD)
    client.post("/api/v1/auth/password/forgot", json={"email": "ana@example.com"})
    token = _token(sent[-1][1])
    assert client.post("/api/v1/auth/password/reset", json={"token": token, "password": NEW}).status_code == 200
    again = client.post("/api/v1/auth/password/reset", json={"token": token, "password": "another passphrase"})
    assert again.status_code == 400
    assert client.post("/api/v1/auth/password/reset/check", json={"token": token}).status_code == 404


def test_only_the_newest_reset_link_works(client, sent):
    accounts.create_account("ana@example.com", PASSWORD)
    for _ in range(2):
        client.post("/api/v1/auth/password/forgot", json={"email": "ana@example.com"})
    older, newer = _token(sent[0][1]), _token(sent[1][1])
    assert client.post("/api/v1/auth/password/reset", json={"token": older, "password": NEW}).status_code == 400
    assert client.post("/api/v1/auth/password/reset", json={"token": newer, "password": NEW}).status_code == 200


def test_a_reset_link_expires(client, sent):
    accounts.create_account("ana@example.com", PASSWORD)
    client.post("/api/v1/auth/password/forgot", json={"email": "ana@example.com"})
    with store.write() as conn:
        conn.execute("UPDATE email_tokens SET expires_at = ? WHERE purpose = 'reset'",
                     ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),))
    token = _token(sent[-1][1])
    assert client.post("/api/v1/auth/password/reset", json={"token": token, "password": NEW}).status_code == 400


def test_a_reset_refuses_a_weak_password_and_leaves_the_old_one(client, sent):
    accounts.create_account("ana@example.com", PASSWORD)
    client.post("/api/v1/auth/password/forgot", json={"email": "ana@example.com"})
    token = _token(sent[-1][1])
    assert client.post("/api/v1/auth/password/reset",
                       json={"token": token, "password": "password123"}).status_code == 400
    assert _sign_in(client).status_code == 200


def test_a_reset_lifts_a_lock_and_confirms_the_email(client, sent):
    made = accounts.create_account("ana@example.com", PASSWORD, email_verified=False)
    with store.write() as conn:
        conn.execute("UPDATE users SET failed_logins = 20, locked_until = ? WHERE user_id = ?",
                     ((datetime.now(UTC) + timedelta(hours=1)).isoformat(), made.user_id))
    client.post("/api/v1/auth/password/forgot", json={"email": "ana@example.com"})
    client.post("/api/v1/auth/password/reset", json={"token": _token(sent[-1][1]), "password": NEW})
    assert accounts.get_account(made.user_id).email_verified
    assert _sign_in(client, password=NEW).status_code == 200


def test_no_reset_link_or_password_reaches_the_log(client, sent, caplog):
    accounts.create_account("ana@example.com", PASSWORD)
    with caplog.at_level(logging.DEBUG):
        client.post("/api/v1/auth/password/forgot", json={"email": "ana@example.com"})
        token = _token(sent[-1][1])
        client.post("/api/v1/auth/password/reset", json={"token": token, "password": NEW})
    assert token not in caplog.text and NEW not in caplog.text


# --- changing a password ------------------------------------------------------------------

def test_changing_a_password_ends_the_other_sessions_but_not_this_one(client):
    accounts.create_account("ana@example.com", PASSWORD)
    other = TestClient(app)
    _sign_in(other)
    _sign_in(client)
    changed = client.post("/api/v1/auth/account/password",
                          json={"new_password": NEW, "current_password": PASSWORD})
    assert changed.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401


def test_sessions_stored_before_the_counter_existed_keep_working(client):
    """The deploy that adds session_epoch signs nobody out."""
    import asyncio

    from app.session.base import SessionData

    made = accounts.create_account("ana@example.com", PASSWORD)
    old = SessionData.model_validate({"session_id": "old", "snowflake_user": made.username,
                                      "auth_mode": "password", "user_id": made.user_id})
    asyncio.run(app.state.session_store.set("old", old, 600))
    client.cookies.set("session_id", "old")
    assert client.get("/api/v1/auth/me").status_code == 200


# --- an account made by the old Snowflake sign-in --------------------------------------------

def _snowflake_made_account() -> accounts.Account:
    """As the removed Snowflake sign-in left one: username the Snowflake user,
    no email, no password, a linked Snowflake credential."""
    with store.write() as conn:
        user_id = accounts._insert_user(conn, username="ALICE", email=None, display_name=None,
                                        is_admin=False, created_by="snowflake-sign-in")
        conn.execute("INSERT INTO credentials (user_id, kind, subject, secret, updated_at) "
                     "VALUES (?, 'snowflake', 'ALICE', 'enc', ?)",
                     (user_id, datetime.now(UTC).isoformat()))
    return accounts.get_account(user_id)


def _admin(client):
    accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    _sign_in(client, "admin@example.com")


def test_an_administrator_moves_a_snowflake_account_onto_a_password_keeping_it(client, sent):
    alice = _snowflake_made_account()
    _admin(client)
    done = client.post(f"/api/v1/users/{alice.user_id}/sign-in-setup",
                       json={"email": "alice@example.com"})
    assert done.status_code == 200 and done.json()["emailed"] is True
    assert "link" not in done.json(), "the link goes only to the person, never the admin"
    assert sent[-1][0] == "alice@example.com"

    person = TestClient(app)
    reset = person.post("/api/v1/auth/password/reset", json={"token": _token(sent[-1][1]), "password": NEW})
    assert reset.status_code == 200
    after = accounts.get_account(alice.user_id)
    assert after.user_id == alice.user_id and after.username == "ALICE"
    assert after.email == "alice@example.com" and after.email_verified and after.has_password
    assert after.snowflake_user == "ALICE", "the Snowflake link, as a data source, is kept"
    me = person.get("/api/v1/auth/me").json()
    assert me["username"] == "ALICE" and me["auth_mode"] == "password"


def test_until_the_link_is_opened_nothing_can_sign_in(client, sent):
    alice = _snowflake_made_account()
    _admin(client)
    client.post(f"/api/v1/users/{alice.user_id}/sign-in-setup", json={"email": "alice@example.com"})
    assert _sign_in(TestClient(app), "alice@example.com", NEW).status_code == 401
    assert not accounts.get_account(alice.user_id).email_verified


def test_setup_can_be_repeated_to_correct_an_address(client, sent):
    alice = _snowflake_made_account()
    _admin(client)
    client.post(f"/api/v1/users/{alice.user_id}/sign-in-setup", json={"email": "alce@example.com"})
    again = client.post(f"/api/v1/users/{alice.user_id}/sign-in-setup",
                        json={"email": "alice@example.com"})
    assert again.status_code == 200
    first = client.post("/api/v1/auth/password/reset", json={"token": _token(sent[0][1]), "password": NEW})
    assert first.status_code == 400, "the link to the mistyped address no longer works"


def test_setup_is_refused_for_an_account_that_has_a_password(client):
    ana = accounts.create_account("ana@example.com", PASSWORD)
    _admin(client)
    refused = client.post(f"/api/v1/users/{ana.user_id}/sign-in-setup",
                          json={"email": "attacker@example.com"})
    assert refused.status_code == 400
    assert accounts.get_account(ana.user_id).email == "ana@example.com"


def test_setup_is_refused_for_an_email_another_account_has(client):
    accounts.create_account("taken@example.com", PASSWORD)
    alice = _snowflake_made_account()
    _admin(client)
    assert client.post(f"/api/v1/users/{alice.user_id}/sign-in-setup",
                       json={"email": "taken@example.com"}).status_code == 400


def test_setup_needs_mail_and_an_administrator(client, monkeypatch):
    alice = _snowflake_made_account()
    accounts.create_account("ana@example.com", PASSWORD)
    _sign_in(client)
    assert client.post(f"/api/v1/users/{alice.user_id}/sign-in-setup",
                       json={"email": "alice@example.com"}).status_code == 403
    client.post("/api/v1/auth/logout")
    _admin(client)
    monkeypatch.setattr(mailer, "configured", lambda: False)
    monkeypatch.setattr(settings, "EMAIL_LINKS_IN_LOG", False)
    assert client.post(f"/api/v1/users/{alice.user_id}/sign-in-setup",
                       json={"email": "alice@example.com"}).status_code == 400
