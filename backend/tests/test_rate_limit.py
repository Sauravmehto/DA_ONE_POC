"""Sign-in rate limits: what is counted, who the client is, and what happens
when the counter cannot be reached (spec 0007, phase B)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.accounts import service as accounts
from app.core import rate_limit
from app.core.config import settings
from app.main import app

PASSWORD = "correct horse battery"


@pytest.fixture
def client(fresh_accounts, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_SIGN_IN_PER_EMAIL", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_SIGN_IN_PER_IP", 5)
    with TestClient(app) as test_client:
        yield test_client


def _sign_in(client, email, password="wrong password", ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post("/api/v1/auth/password", json={"email": email, "password": password},
                       headers=headers)


def test_one_email_is_limited_and_the_answer_says_when_to_retry(client):
    accounts.create_account("ana@example.com", PASSWORD)
    codes = [_sign_in(client, "ana@example.com").status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    limited = _sign_in(client, "ana@example.com", PASSWORD)
    assert limited.status_code == 429, "the right password is refused while limited too"
    assert int(limited.headers["Retry-After"]) > 0


def test_a_limited_unknown_email_looks_exactly_like_a_limited_real_one(client):
    accounts.create_account("ana@example.com", PASSWORD)
    for _ in range(3):
        _sign_in(client, "ana@example.com")
        _sign_in(client, "nobody@example.com")
    rate_limit.reset_for_tests()     # a fresh window, so only the email counts decide
    for _ in range(3):
        _sign_in(client, "ana@example.com")
        _sign_in(client, "nobody@example.com")
    real, unknown = _sign_in(client, "ana@example.com"), _sign_in(client, "nobody@example.com")
    assert real.status_code == unknown.status_code == 429
    assert real.json() == unknown.json()


def test_one_address_trying_many_people_is_limited(client):
    codes = [_sign_in(client, f"person{i}@example.com").status_code for i in range(6)]
    assert codes[-1] == 429 and codes[:5] == [401] * 5


def test_twenty_wrong_passwords_lock_an_account_and_five_do_not(fresh_accounts):
    made = accounts.create_account("ana@example.com", PASSWORD)
    for _ in range(5):
        with pytest.raises(accounts.SignInFailed):
            accounts.authenticate("ana@example.com", "wrong password")
    assert accounts.authenticate("ana@example.com", PASSWORD).user_id == made.user_id
    assert accounts.MAX_FAILED_LOGINS == 20


def test_an_unreachable_counter_refuses_rather_than_allows(client):
    class Down:
        async def hit(self, key, window):
            raise ConnectionError("redis is down")

    rate_limit.reset_for_tests(Down())
    accounts.create_account("ana@example.com", PASSWORD)
    assert _sign_in(client, "ana@example.com", PASSWORD).status_code == 503
    assert client.post("/api/v1/auth/signup", json={"email": "x@example.com",
                                                    "password": PASSWORD}).status_code == 503


def test_sign_up_resend_and_links_are_limited_too(client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_SIGN_UP_PER_IP", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_EMAIL_PER_EMAIL", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOKEN_PER_IP", 1)
    signup = {"email": "x@example.com", "password": PASSWORD, "display_name": "X Person"}
    assert client.post("/api/v1/auth/signup", json=signup).status_code != 429
    assert client.post("/api/v1/auth/signup", json=signup).status_code == 429
    resend = {"email": "x@example.com"}
    assert client.post("/api/v1/auth/verify/resend", json=resend).status_code == 202
    assert client.post("/api/v1/auth/verify/resend", json=resend).status_code == 429
    assert client.post("/api/v1/auth/verify", json={"token": "nope"}).status_code == 400
    assert client.post("/api/v1/auth/verify", json={"token": "nope"}).status_code == 429


# --- who the client is ----------------------------------------------------------------

def _request(peer: str, forwarded: str | None = None):
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_a_forwarded_header_from_anyone_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", [])
    assert rate_limit.client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_a_trusted_proxy_chain_names_the_real_client(monkeypatch):
    """Traefik -> nginx -> uvicorn: each proxy appends the address it heard from."""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["172.18.0.0/16"])
    request = _request("172.18.0.5", "198.51.100.7, 172.18.0.2")
    assert rate_limit.client_ip(request) == "198.51.100.7"


def test_a_spoofed_entry_left_of_the_real_one_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["172.18.0.0/16"])
    request = _request("172.18.0.5", "10.0.0.1, 198.51.100.7, 172.18.0.2")
    assert rate_limit.client_ip(request) == "198.51.100.7"


def test_an_untrusted_peer_cannot_vouch_for_anyone(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["172.18.0.0/16"])
    assert rate_limit.client_ip(_request("203.0.113.9", "198.51.100.7")) == "203.0.113.9"


def test_a_bad_trusted_proxies_entry_trusts_nothing_extra(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["not-an-ip"])
    assert rate_limit.client_ip(_request("172.18.0.5", "198.51.100.7")) == "172.18.0.5"


def test_the_redis_counter_starts_one_window_and_counts_in_it():
    """Against fakeredis: the window is set once, not pushed back by each hit."""
    import fakeredis.aioredis

    counter = rate_limit._RedisCounter.__new__(rate_limit._RedisCounter)
    counter._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def three():
        return [await counter.hit("k", 60) for _ in range(3)]

    results = asyncio.run(three())
    assert [count for count, _ in results] == [1, 2, 3]
    assert all(0 < retry <= 60 for _, retry in results)
