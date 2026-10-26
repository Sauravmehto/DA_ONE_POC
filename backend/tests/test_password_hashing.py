"""Argon2id for new passwords, scrypt still read, and the password policy
(spec 0007, phases D and G)."""

from __future__ import annotations

import base64
import hashlib
import secrets

import pytest

from app.accounts import passwords, store
from app.accounts import service as accounts

PASSWORD = "correct horse battery"


def _scrypt(password: str) -> str:
    """A hash exactly as the code before Argon2 made them."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32,
                            maxmem=64 * 1024 * 1024)
    return (f"scrypt${2**15}$8$1${base64.b64encode(salt).decode()}$"
            f"{base64.b64encode(digest).decode()}")


def _stored_hash(user_id: str) -> str:
    with store.read() as conn:
        return conn.execute("SELECT secret FROM credentials WHERE user_id = ? AND "
                            "kind = 'password'", (user_id,)).fetchone()["secret"]


def test_a_new_password_is_stored_as_argon2id_and_never_as_itself(fresh_accounts):
    made = accounts.create_account("ana@example.com", PASSWORD)
    stored = _stored_hash(made.user_id)
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored
    with store.read() as conn:
        for row in conn.execute("SELECT * FROM credentials"):
            assert all(PASSWORD not in str(value) for value in tuple(row))


def test_an_old_scrypt_hash_still_signs_in_and_is_upgraded(fresh_accounts):
    made = accounts.create_account("ana@example.com", PASSWORD)
    with store.write() as conn:
        conn.execute("UPDATE credentials SET secret = ? WHERE user_id = ?",
                     (_scrypt(PASSWORD), made.user_id))
    assert accounts.authenticate("ana@example.com", PASSWORD).user_id == made.user_id
    assert _stored_hash(made.user_id).startswith("$argon2id$")
    assert accounts.authenticate("ana@example.com", PASSWORD).user_id == made.user_id


def test_a_wrong_password_against_an_old_hash_fails_and_upgrades_nothing(fresh_accounts):
    made = accounts.create_account("ana@example.com", PASSWORD)
    old = _scrypt(PASSWORD)
    with store.write() as conn:
        conn.execute("UPDATE credentials SET secret = ? WHERE user_id = ?", (old, made.user_id))
    with pytest.raises(accounts.SignInFailed):
        accounts.authenticate("ana@example.com", "not the password")
    assert _stored_hash(made.user_id) == old


@pytest.mark.parametrize("stored", ["", "garbage", "scrypt$x$y", "$argon2id$broken",
                                    "md5$abc", None])
def test_a_malformed_hash_fails_and_never_raises(stored):
    assert passwords.verify_password(PASSWORD, stored) is False


def test_rehash_is_asked_for_old_and_weaker_hashes_only():
    assert passwords.needs_rehash(_scrypt(PASSWORD))
    assert not passwords.needs_rehash(passwords.hash_password(PASSWORD))


def test_an_overlong_password_is_refused_before_it_is_hashed():
    with pytest.raises(ValueError):
        passwords.hash_password("x" * (passwords.MAX_LENGTH + 1))
    assert passwords.verify_password("x" * (passwords.MAX_LENGTH + 1),
                                     passwords.hash_password(PASSWORD)) is False


@pytest.mark.parametrize("weak", ["password123", "Password123", "qwertyuiop", "1234567890"])
def test_common_passwords_are_refused_whatever_their_case(fresh_accounts, weak):
    with pytest.raises(accounts.AccountError, match="commonly used"):
        accounts.create_account("ana@example.com", weak)


def test_a_password_built_from_the_email_is_refused(fresh_accounts):
    with pytest.raises(accounts.AccountError, match="email"):
        accounts.create_account("margaret@example.com", "margaret-is-great")


def test_short_passwords_are_refused(fresh_accounts):
    with pytest.raises(accounts.AccountError, match="at least"):
        accounts.create_account("ana@example.com", "short")

# --- the rollout's first stage: new code, old hash format -----------------------------

@pytest.fixture
def scrypt_stage(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PASSWORD_HASH_ALGORITHM", "scrypt")
    passwords._decoy.cache_clear()
    yield
    passwords._decoy.cache_clear()


def test_in_the_first_stage_new_passwords_are_in_the_format_the_old_release_reads(
    fresh_accounts, scrypt_stage
):
    made = accounts.create_account("ana@example.com", PASSWORD)
    stored = _stored_hash(made.user_id)
    scheme, n, r, p, salt, digest = stored.split("$")
    assert (scheme, int(n), int(r), int(p)) == ("scrypt", 2**15, 8, 1)
    assert len(base64.b64decode(salt)) == 16 and len(base64.b64decode(digest)) == 32
    assert passwords.verify_password(PASSWORD, stored)


def test_in_the_first_stage_nothing_is_upgraded_and_argon2_is_still_read(
    fresh_accounts, scrypt_stage, monkeypatch
):
    from app.core.config import settings

    made = accounts.create_account("ana@example.com", PASSWORD)
    before = _stored_hash(made.user_id)
    assert accounts.authenticate("ana@example.com", PASSWORD).user_id == made.user_id
    assert _stored_hash(made.user_id) == before

    monkeypatch.setattr(settings, "PASSWORD_HASH_ALGORITHM", "argon2id")
    argon = passwords.hash_password(PASSWORD)
    monkeypatch.setattr(settings, "PASSWORD_HASH_ALGORITHM", "scrypt")
    assert passwords.verify_password(PASSWORD, argon), "a hash from the second stage is still read"
    assert passwords.needs_rehash(argon) is False, "and is never downgraded"


def test_the_startup_check_says_when_the_first_stage_is_still_on():
    from app.core.config import Settings
    from app.core.startup_checks import check

    found = check(Settings(DEBUG=True, PASSWORD_HASH_ALGORITHM="scrypt"), any_administrator=True)
    assert any("scrypt" in w for w in found.warnings)
