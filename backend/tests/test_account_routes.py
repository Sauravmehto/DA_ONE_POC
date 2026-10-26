"""Signing in with an app account, invitations, the admin screens, and linking
Snowflake to an account, through the API."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.accounts import service as accounts
from app.api.v1.routes import auth as auth_routes
from app.core.config import settings
from app.main import app
from app.services.oauth import OAuthTokens
from app.services.snowflake import SnowflakeNotLinked, SnowflakeService
from app.session.base import SessionData
from app.session.memory import InMemorySessionStore

PASSWORD = "correct horse battery"


@pytest.fixture
def client(fresh_accounts, monkeypatch):
    monkeypatch.setattr(auth_routes.settings, "FRONTEND_URL", "http://app.test")
    with TestClient(app) as test_client:
        yield test_client


def sign_in(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/password", json={"email": email, "password": password})


def oauth_returning(username: str) -> AsyncMock:
    service = AsyncMock()
    service.exchange_code.return_value = OAuthTokens(
        access_token=f"access-{username}", refresh_token=f"refresh-{username}",
        expires_in=3600, username=username,
    )
    service.build_authorize_url = lambda state: f"https://sf.test/oauth/authorize?state={state}"
    return service


# --- capabilities and password sign-in --------------------------------------------

def test_capabilities_offer_passwords_and_report_the_signup_mode(client, monkeypatch):
    body = client.get("/api/v1/auth/capabilities").json()
    assert body["password"] is True and body["signup"] is False
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    assert client.get("/api/v1/auth/capabilities").json()["signup"] is True


def test_password_sign_in_starts_a_session_without_snowflake(client):
    accounts.create_account("ana@example.com", PASSWORD, "Ana")
    response = sign_in(client, "ana@example.com")
    assert response.status_code == 200
    assert "session_id" in response.cookies

    me = client.get("/api/v1/auth/me").json()
    assert me["username"] == "ana@example.com" == me["snowflake_user"]
    assert me["email"] == "ana@example.com" and me["display_name"] == "Ana"
    assert me["auth_mode"] == "password"
    assert me["snowflake_linked"] is False and me["has_password"] is True
    assert me["is_admin"] is False


def test_a_wrong_password_is_401_with_no_session(client):
    accounts.create_account("ana@example.com", PASSWORD)
    response = sign_in(client, "ana@example.com", "wrong password here")
    assert response.status_code == 401
    assert "session_id" not in response.cookies
    assert client.get("/api/v1/auth/me").status_code == 401


def test_each_sign_in_gets_a_new_session_id(client):
    accounts.create_account("ana@example.com", PASSWORD)
    first = sign_in(client, "ana@example.com").cookies["session_id"]
    second = sign_in(client, "ana@example.com").cookies["session_id"]
    assert first != second


def test_disabling_an_account_ends_its_session_at_the_next_request(client):
    admin = accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    ana = accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    assert client.get("/api/v1/auth/me").status_code == 200

    accounts.update_account(ana.user_id, actor_user_id=admin.user_id, is_active=False)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert "disabled" in response.json()["detail"]


def test_an_account_with_no_snowflake_simply_has_no_snowflake_databases(client):
    """This used to be a 409 saying "connect your Snowflake account", on the
    reasoning that the app could act on it. In front of somebody who has no
    Snowflake — on a deployment that may have none — it reads as an error
    about somebody else's problem, and the page that offers to connect one
    goes by the account's own link, not by this call failing.

    The 409 is kept for an account that HAS connected and whose token has
    since died, which is the case where connecting again is something the
    person can do. See test_no_snowflake_is_not_an_error.py."""
    accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    response = client.get("/api/v1/databases")
    assert response.status_code == 200
    assert response.json() == []


@patch.object(SnowflakeService, "resolve_identity", new_callable=AsyncMock)
@patch("app.api.v1.routes.auth._get_oauth_service")
def test_connecting_a_snowflake_user_someone_else_has_says_so_where_it_is_seen(
    oauth_factory, resolve, client
):
    """The failure used to redirect to the sign-in page, which sends anyone
    already signed in straight back to the app — so the person saw the page
    they started on and no reason at all. Connecting happens while signed in,
    so the answer belongs where the connectors are."""
    first = accounts.create_account("first@example.com", PASSWORD)
    accounts.link_snowflake(first.user_id, "SAURABH", "encrypted")

    second = accounts.create_account("second@example.com", PASSWORD)
    sign_in(client, "second@example.com")
    assert client.get("/api/v1/auth/me").json()["snowflake_linked"] is False

    resolve.return_value = ("SAURABH", "ANALYST")
    oauth_factory.return_value = oauth_returning("SAURABH")
    accounts.start_snowflake_link(second.user_id, "st-link", 600)
    asyncio.run(app.state.session_store.set_oauth_state("st-link", 600))

    answer = client.get("/api/v1/auth/callback?code=c&state=st-link",
                        follow_redirects=False)

    location = answer.headers["location"]
    assert "/login" not in location, "a signed-in person never sees the sign-in page"
    assert "error=" in location
    assert "disconnect" in location.lower(), "and the message says what to do"
    assert client.get("/api/v1/auth/me").json()["snowflake_linked"] is False


# --- signing up ---------------------------------------------------------------------

def test_signup_is_refused_unless_open(client, monkeypatch):
    response = client.post("/api/v1/auth/signup",
                           json={"email": "new@example.com", "password": PASSWORD,
                                 "display_name": "New Person"})
    assert response.status_code == 403


def links_sent(monkeypatch) -> list[str]:
    """Catch confirmation emails instead of sending them."""
    from app.accounts import mailer

    sent: list[str] = []
    monkeypatch.setattr(mailer, "verification_email", lambda to, link: sent.append(link))
    return sent


def test_signup_emails_a_link_and_signs_in_only_once_it_is_opened(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    sent = links_sent(monkeypatch)

    response = client.post("/api/v1/auth/signup",
                           json={"email": "new@example.com", "password": PASSWORD,
                                 "display_name": "New Person"})
    assert response.status_code == 202
    assert response.json() == {"status": "verification_sent", "email": "new@example.com"}
    assert "session_id" not in response.cookies
    assert len(sent) == 1 and sent[0].startswith("http://app.test/verify#")

    refused = sign_in(client, "new@example.com")
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "email_not_verified"

    token = sent[0].rsplit("#", 1)[1]
    confirmed = client.post("/api/v1/auth/verify", json={"token": token})
    assert confirmed.status_code == 200
    assert confirmed.json()["email_verified"] is True
    assert client.get("/api/v1/auth/me").json()["email"] == "new@example.com"
    assert client.post("/api/v1/auth/verify", json={"token": token}).status_code == 400


def test_resending_answers_the_same_for_any_email(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    sent = links_sent(monkeypatch)
    client.post("/api/v1/auth/signup", json={"email": "new@example.com", "password": PASSWORD,
                                 "display_name": "New Person"})
    sent.clear()

    known = client.post("/api/v1/auth/verify/resend", json={"email": "new@example.com"})
    unknown = client.post("/api/v1/auth/verify/resend", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(sent) == 1


def test_signup_needs_the_persons_name(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    for body in ({"email": "new@example.com", "password": PASSWORD},
                 {"email": "new@example.com", "password": PASSWORD, "display_name": "   "}):
        response = client.post("/api/v1/auth/signup", json=body)
        assert response.status_code == 400
        assert "name" in response.json()["detail"]
    assert accounts.account_by_email("new@example.com") is None


def test_signup_refuses_an_email_that_already_has_an_account(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    accounts.create_account("taken@example.com", PASSWORD)
    response = client.post("/api/v1/auth/signup", json={
        "email": "TAKEN@example.com", "password": PASSWORD, "display_name": "Someone"})
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]
    assert len(accounts.list_accounts()) == 1


def test_signup_explains_a_weak_password(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    response = client.post("/api/v1/auth/signup",
                           json={"email": "new@example.com", "password": "short",
                                 "display_name": "New Person"})
    assert response.status_code == 400
    assert "at least" in response.json()["detail"]


# --- with no email confirmation (EMAIL_VERIFICATION_REQUIRED off) -----------------------

@pytest.fixture
def plain(client, monkeypatch):
    """A deployment where sign-up is just sign-up: no link to open."""
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_REQUIRED", False)
    return client


def test_without_confirmation_signing_up_signs_you_straight_in(plain, monkeypatch):
    sent = links_sent(monkeypatch)
    response = plain.post("/api/v1/auth/signup", json={
        "email": "new@example.com", "password": PASSWORD, "display_name": "New Person"})
    assert response.status_code == 202
    assert response.json()["status"] == "signed_in"
    assert "session_id" in response.cookies
    assert sent == [], "no confirmation email is sent"
    me = plain.get("/api/v1/auth/me").json()
    assert me["email"] == "new@example.com" and me["display_name"] == "New Person"
    assert me["is_admin"] is False


def test_without_confirmation_an_unconfirmed_account_can_sign_in(plain):
    accounts.create_account("ana@example.com", PASSWORD, email_verified=False)
    response = sign_in(plain, "ana@example.com")
    assert response.status_code == 200
    assert plain.get("/api/v1/auth/me").status_code == 200


def test_with_confirmation_the_same_account_is_still_refused(client):
    accounts.create_account("ana@example.com", PASSWORD, email_verified=False)
    refused = sign_in(client, "ana@example.com")
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "email_not_verified"


def test_capabilities_say_whether_a_confirmation_step_exists(client, monkeypatch):
    assert client.get("/api/v1/auth/capabilities").json()["email_verification"] is True
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_REQUIRED", False)
    assert client.get("/api/v1/auth/capabilities").json()["email_verification"] is False


def test_without_confirmation_admin_emails_does_not_make_an_administrator(plain, monkeypatch):
    """The point of the rule: with nothing proving an address, listing it in
    ADMIN_EMAILS must not let whoever signs up with it take over."""
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["boss@example.com"])
    plain.post("/api/v1/auth/signup", json={
        "email": "boss@example.com", "password": PASSWORD, "display_name": "Not The Boss"})
    me = plain.get("/api/v1/auth/me").json()
    assert me["is_admin"] is False
    assert plain.get("/api/v1/users").status_code == 403


def test_without_confirmation_an_administrator_is_made_from_the_server(plain, monkeypatch):
    """scripts/make_admin.py: access to the server is the proof."""
    import subprocess
    import sys
    from pathlib import Path

    accounts.create_account("boss@example.com", PASSWORD, email_verified=False)
    script = Path(__file__).resolve().parents[1] / "scripts" / "make_admin.py"
    env = {**__import__("os").environ, "APP_DB_PATH": settings.APP_DB_PATH}
    done = subprocess.run([sys.executable, "-I", str(script), "boss@example.com"],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert "now an administrator" in done.stdout
    sign_in(plain, "boss@example.com")
    assert plain.get("/api/v1/auth/me").json()["is_admin"] is True

# --- invitations and administration ---------------------------------------------------

def test_an_administrator_invites_and_the_invitee_signs_in(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["admin@example.com"])
    accounts.create_account("admin@example.com", PASSWORD)
    sign_in(client, "admin@example.com")
    assert client.get("/api/v1/auth/me").json()["is_admin"] is True

    invited = client.post("/api/v1/users/invitations",
                          json={"email": "new@example.com", "is_admin": False})
    assert invited.status_code == 200
    link = invited.json()["link"]
    assert link.startswith("http://app.test/invite#")
    token = link.rsplit("#", 1)[1]

    listed = client.get("/api/v1/users").json()
    assert [i["email"] for i in listed["invitations"]] == ["new@example.com"]
    assert "token" not in str(listed["invitations"][0]).lower()

    other = TestClient(app)
    assert other.post("/api/v1/auth/invitations/check",
                      json={"token": token}).json() == {"email": "new@example.com"}
    accepted = other.post("/api/v1/auth/invitations/accept",
                          json={"token": token, "password": PASSWORD, "display_name": "New"})
    assert accepted.status_code == 200
    assert other.get("/api/v1/auth/me").json()["email"] == "new@example.com"
    assert other.post("/api/v1/auth/invitations/check", json={"token": token}).status_code == 404


def test_the_admin_screens_are_for_administrators_only(client):
    accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    assert client.get("/api/v1/users").status_code == 403
    assert client.post("/api/v1/users/invitations", json={"email": "x@example.com"}).status_code == 403


def test_an_administrator_can_make_someone_else_an_administrator(client):
    accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    ana = accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "admin@example.com")

    response = client.patch(f"/api/v1/users/{ana.user_id}", json={"is_admin": True})
    assert response.status_code == 200 and response.json()["is_admin"] is True


def test_an_administrator_cannot_demote_themselves(client):
    admin = accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    sign_in(client, "admin@example.com")
    response = client.patch(f"/api/v1/users/{admin.user_id}", json={"is_admin": False})
    assert response.status_code == 400


def test_a_snowflake_role_alone_does_not_make_an_administrator(client, monkeypatch):
    """Admin belongs to the account. A session's role is not enough."""
    monkeypatch.setattr(settings, "ADMIN_ROLES", ["DATAALPHA_ADMIN"])
    session = SessionData(session_id="sf-admin", snowflake_user="ALICE",
                          role="DATAALPHA_ADMIN", access_token="t")
    asyncio.run(app.state.session_store.set("sf-admin", session, 600))
    client.cookies.set("session_id", "sf-admin")
    assert client.get("/api/v1/users").status_code == 403


@patch.object(SnowflakeService, "resolve_identity", new_callable=AsyncMock)
@patch("app.api.v1.routes.auth._get_oauth_service")
def test_connecting_snowflake_with_an_admin_role_does_not_make_an_administrator(
    oauth_factory, resolve, client, monkeypatch
):
    """Spec 0007: an ADMIN_ROLES role lets the linked session save Snowflake
    layers, and nothing more. The account's admin flag is the app's to set."""
    monkeypatch.setattr(settings, "ADMIN_ROLES", ["DATAALPHA_ADMIN"])
    resolve.return_value = ("ALICE_SF", "DATAALPHA_ADMIN")
    oauth_factory.return_value = oauth_returning("ALICE_SF")
    alice = accounts.create_account("alice@example.com", PASSWORD)
    sign_in(client, "alice@example.com")

    start = client.get("/api/v1/auth/snowflake/connect", follow_redirects=False)
    state = start.headers["location"].split("state=", 1)[1]
    client.get(f"/api/v1/auth/callback?code=c&state={state}", follow_redirects=False)

    me = client.get("/api/v1/auth/me").json()
    assert me["snowflake_linked"] is True
    assert me["is_admin"] is False
    assert client.get("/api/v1/users").status_code == 403
    assert not accounts.get_account(alice.user_id).administrator


# --- changing a password ----------------------------------------------------------------

def test_a_signed_in_account_changes_its_password(client):
    accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    bad = client.post("/api/v1/auth/account/password",
                      json={"new_password": "a brand new password", "current_password": "nope"})
    assert bad.status_code == 400
    good = client.post("/api/v1/auth/account/password",
                       json={"new_password": "a brand new password",
                             "current_password": PASSWORD})
    assert good.status_code == 200
    assert sign_in(client, "ana@example.com", "a brand new password").status_code == 200


# --- Snowflake is connected, never signed in with ------------------------------------------

def _connect(client, oauth_factory, resolve, snowflake_user="ANA_SF", role="ANALYST"):
    resolve.return_value = (snowflake_user, role)
    oauth_factory.return_value = oauth_returning(snowflake_user)
    start = client.get("/api/v1/auth/snowflake/connect", follow_redirects=False)
    assert start.status_code == 302
    return start.headers["location"].split("state=", 1)[1]


def test_there_is_no_snowflake_sign_in(client):
    assert client.get("/api/v1/auth/login", follow_redirects=False).status_code == 404
    body = client.get("/api/v1/auth/capabilities").json()
    assert "oauth" not in body
    assert body["snowflake_link"] is True and body["password"] is True


@patch.object(SnowflakeService, "resolve_identity", new_callable=AsyncMock)
@patch("app.api.v1.routes.auth._get_oauth_service")
def test_a_callback_that_is_not_a_link_does_nothing(oauth_factory, resolve, client):
    """The old sign-in: a valid OAuth state and code, no link behind it. No
    tokens are fetched, no account is made, no session starts."""
    service = oauth_returning("ALICE")
    oauth_factory.return_value = service
    asyncio.run(app.state.session_store.set_oauth_state("st", 600))

    response = client.get("/api/v1/auth/callback?code=c&state=st", follow_redirects=False)

    assert response.status_code == 302 and "/login?error=" in response.headers["location"]
    assert "session_id" not in response.cookies
    service.exchange_code.assert_not_called()
    resolve.assert_not_called()
    assert accounts.list_accounts() == []
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_callback_with_a_state_nobody_issued_is_refused(client):
    response = client.get("/api/v1/auth/callback?code=c&state=forged", follow_redirects=False)
    assert response.status_code == 400
    assert accounts.list_accounts() == []


@patch.object(SnowflakeService, "resolve_identity", new_callable=AsyncMock)
@patch("app.api.v1.routes.auth._get_oauth_service")
def test_a_password_account_connects_its_snowflake(oauth_factory, resolve, client):
    accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    state = _connect(client, oauth_factory, resolve)

    done = client.get(f"/api/v1/auth/callback?code=c&state={state}", follow_redirects=False)
    assert done.headers["location"] == "http://app.test"

    me = client.get("/api/v1/auth/me").json()
    assert me["username"] == "ana@example.com"
    assert me["snowflake_linked"] is True and me["snowflake_account"] == "ANA_SF"
    assert me["auth_mode"] == "password" and me["role"] == "ANALYST"
    assert len(accounts.list_accounts()) == 1


@patch.object(SnowflakeService, "resolve_identity", new_callable=AsyncMock)
@patch("app.api.v1.routes.auth._get_oauth_service")
def test_a_connect_link_works_once(oauth_factory, resolve, client):
    accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    state = _connect(client, oauth_factory, resolve)
    client.get(f"/api/v1/auth/callback?code=c&state={state}", follow_redirects=False)
    replay = client.get(f"/api/v1/auth/callback?code=c&state={state}", follow_redirects=False)
    assert replay.status_code == 400


@patch.object(SnowflakeService, "resolve_identity", new_callable=AsyncMock)
@patch("app.api.v1.routes.auth._get_oauth_service")
def test_a_link_finished_in_another_browser_links_nothing(oauth_factory, resolve, client):
    ana = accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    state = _connect(client, oauth_factory, resolve, role=None)

    stranger = TestClient(app)
    done = stranger.get(f"/api/v1/auth/callback?code=c&state={state}", follow_redirects=False)
    assert "/login?error=" in done.headers["location"]
    assert accounts.get_account(ana.user_id).snowflake_user is None
    oauth_factory.return_value.exchange_code.assert_not_called()


@patch.object(SnowflakeService, "resolve_identity", new_callable=AsyncMock)
@patch("app.api.v1.routes.auth._get_oauth_service")
def test_connecting_a_snowflake_user_already_on_another_account_is_refused(
    oauth_factory, resolve, client
):
    bob = accounts.create_account("bob@example.com", PASSWORD)
    accounts.link_snowflake(bob.user_id, "SHARED_SF", "enc")
    ana = accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    state = _connect(client, oauth_factory, resolve, snowflake_user="SHARED_SF")

    done = client.get(f"/api/v1/auth/callback?code=c&state={state}", follow_redirects=False)
    assert "error=" in done.headers["location"]
    assert accounts.get_account(ana.user_id).snowflake_user is None
    assert accounts.get_account(bob.user_id).snowflake_user == "SHARED_SF"


def test_connecting_needs_a_signed_in_account(client):
    assert client.get("/api/v1/auth/snowflake/connect",
                      follow_redirects=False).status_code == 401


def test_connecting_says_so_when_it_is_not_set_up(client, monkeypatch):
    accounts.create_account("ana@example.com", PASSWORD)
    sign_in(client, "ana@example.com")
    monkeypatch.setattr(settings, "OAUTH_CLIENT_ID", "")
    assert client.get("/api/v1/auth/snowflake/connect",
                      follow_redirects=False).status_code == 503
    assert client.get("/api/v1/auth/capabilities").json()["snowflake_link"] is False
    assert client.get("/api/v1/auth/me").status_code == 200, "signing in is not affected"

def test_unlinking_leaves_a_password_session_without_snowflake(client):
    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.link_snowflake(ana.user_id, "ANA_SF", "enc")
    sign_in(client, "ana@example.com")
    response = client.post("/api/v1/auth/snowflake/unlink")
    assert response.status_code == 200
    assert response.json()["snowflake_linked"] is False


# --- a password session using its linked Snowflake -------------------------------------------

def test_a_linked_account_gets_a_snowflake_token_on_first_use(fresh_accounts):
    from app.core.security import TokenEncryptor
    from cryptography.fernet import Fernet

    encryptor = TokenEncryptor(Fernet.generate_key().decode())
    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.link_snowflake(ana.user_id, "ANA_SF", encryptor.encrypt("stored-refresh"))

    store = InMemorySessionStore()
    service = SnowflakeService(settings, store, encryptor)
    service._oauth = AsyncMock()
    service._oauth.refresh_tokens.return_value = OAuthTokens(
        access_token="fresh-access", refresh_token="rotated-refresh", expires_in=600,
    )
    session = SessionData(session_id="s1", snowflake_user="ana@example.com",
                          auth_mode="password", user_id=ana.user_id)

    restored = asyncio.run(service.restore_linked(session))
    assert restored.access_token == "fresh-access" and restored.has_snowflake
    service._oauth.refresh_tokens.assert_awaited_once_with("stored-refresh")
    assert asyncio.run(store.get("s1")).access_token == "fresh-access"
    assert encryptor.decrypt(accounts.snowflake_token(ana.user_id)) == "rotated-refresh"


def test_a_page_of_parallel_requests_restores_snowflake_once(fresh_accounts):
    """A page opens with a dozen requests at once. They must share one token
    exchange rather than each making their own."""
    from app.core.security import TokenEncryptor
    from cryptography.fernet import Fernet

    encryptor = TokenEncryptor(Fernet.generate_key().decode())
    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.link_snowflake(ana.user_id, "ANA_SF", encryptor.encrypt("stored-refresh"))

    store = InMemorySessionStore()
    service = SnowflakeService(settings, store, encryptor)

    async def slow_refresh(_token):
        await asyncio.sleep(0.05)
        return OAuthTokens(access_token="fresh-access", refresh_token="", expires_in=600)

    service._oauth = AsyncMock()
    service._oauth.refresh_tokens.side_effect = slow_refresh
    session = SessionData(session_id="s1", snowflake_user="ana@example.com",
                          auth_mode="password", user_id=ana.user_id)

    async def page_load():
        await store.set("s1", session, 600)
        return await asyncio.gather(*(service.restore_linked(session) for _ in range(8)))

    restored = asyncio.run(page_load())
    assert {r.access_token for r in restored} == {"fresh-access"}
    assert service._oauth.refresh_tokens.await_count == 1


def test_an_expired_link_asks_to_connect_again(fresh_accounts):
    from app.core.security import TokenEncryptor
    from cryptography.fernet import Fernet

    encryptor = TokenEncryptor(Fernet.generate_key().decode())
    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.link_snowflake(ana.user_id, "ANA_SF", encryptor.encrypt("dead-refresh"))
    service = SnowflakeService(settings, InMemorySessionStore(), encryptor)
    service._oauth = AsyncMock()
    service._oauth.refresh_tokens.side_effect = RuntimeError("invalid_grant")

    session = SessionData(session_id="s1", snowflake_user="ana@example.com",
                          auth_mode="password", user_id=ana.user_id)
    with pytest.raises(SnowflakeNotLinked, match="expired"):
        asyncio.run(service.restore_linked(session))
