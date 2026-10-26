"""What stops a deployment from starting, and what it is only warned about."""

from __future__ import annotations

from app.core.config import Settings
from app.core.startup_checks import check


def _settings(**overrides) -> Settings:
    base = dict(DEBUG=False, TOKEN_ENCRYPTION_KEY="k", SESSION_BACKEND="redis",
                REDIS_URL="redis://r:6379/0", EMAIL_LINKS_IN_LOG=False,
                SIGNUP_MODE="invite", SMTP_HOST="", SMTP_FROM="",
                FRONTEND_URL="https://one.example", COOKIE_SECURE=True)
    base.update(overrides)
    return Settings(**base)


def test_a_sound_deployment_starts():
    assert check(_settings(), any_administrator=True).errors == []


def test_links_in_the_log_stop_a_deployment_but_not_local_development():
    assert check(_settings(EMAIL_LINKS_IN_LOG=True), any_administrator=True).errors
    local = check(_settings(DEBUG=True, EMAIL_LINKS_IN_LOG=True), any_administrator=True)
    assert local.errors == [] and local.warnings


def test_open_sign_up_needs_mail():
    assert check(_settings(SIGNUP_MODE="open"), any_administrator=True).errors
    with_mail = check(_settings(SIGNUP_MODE="open", SMTP_HOST="smtp.example",
                                SMTP_FROM="x@example.com"), any_administrator=True)
    assert with_mail.errors == []


def test_a_deployment_needs_its_encryption_key():
    assert check(_settings(TOKEN_ENCRYPTION_KEY=""), any_administrator=True).errors


def test_https_needs_secure_cookies():
    assert check(_settings(COOKIE_SECURE=False), any_administrator=True).errors
    plain = _settings(COOKIE_SECURE=False, FRONTEND_URL="http://localhost:5173")
    assert check(plain, any_administrator=True).errors == []


def test_no_administrator_is_a_warning():
    found = check(_settings(), any_administrator=False)
    assert found.errors == [] and any("ADMIN_EMAILS" in w for w in found.warnings)

def test_an_empty_trusted_proxies_list_is_warned_about_not_fatal():
    found = check(_settings(TRUSTED_PROXIES=[]), any_administrator=True)
    assert found.errors == [] and any("TRUSTED_PROXIES" in w for w in found.warnings)
    quiet = check(_settings(TRUSTED_PROXIES=["172.18.0.0/16"]), any_administrator=True)
    assert not any("TRUSTED_PROXIES" in w for w in quiet.warnings)
    local = check(_settings(DEBUG=True, TRUSTED_PROXIES=[]), any_administrator=True)
    assert not any("TRUSTED_PROXIES" in w for w in local.warnings)
