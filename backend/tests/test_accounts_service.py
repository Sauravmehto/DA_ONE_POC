"""App accounts: passwords, sign-in, invitations, Snowflake links, admin rules."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.accounts import passwords, store
from app.accounts import service as accounts
from app.core.config import settings

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _fresh(fresh_accounts):
    yield


# --- passwords ------------------------------------------------------------------

def test_a_hash_verifies_its_own_password_only():
    stored = passwords.hash_password(PASSWORD)
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored
    assert passwords.verify_password(PASSWORD, stored)
    assert not passwords.verify_password("correct horse battery!", stored)


def test_two_hashes_of_one_password_differ():
    assert passwords.hash_password(PASSWORD) != passwords.hash_password(PASSWORD)


@pytest.mark.parametrize("stored", [None, "", "plain", "scrypt$1$2", "md5$a$b$c$d$e"])
def test_a_damaged_hash_fails_rather_than_crashing(stored):
    assert passwords.verify_password(PASSWORD, stored) is False


def test_weaker_settings_are_flagged_for_rehash():
    weak = "scrypt$1024$8$1$c2FsdA==$ZGlnZXN0"
    assert passwords.needs_rehash(weak)
    assert not passwords.needs_rehash(passwords.hash_password(PASSWORD))


# --- the file ---------------------------------------------------------------------

def test_the_schema_is_created_once_and_versioned():
    with store.read() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version == len(store.MIGRATIONS)
    assert {"users", "credentials", "invitations", "snowflake_link_requests"} <= tables


def test_a_failed_write_leaves_nothing_behind():
    with pytest.raises(RuntimeError):
        with store.write() as conn:
            conn.execute(
                "INSERT INTO users (user_id, username, created_at) VALUES ('x', 'x', 'now')"
            )
            raise RuntimeError("boom")
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


# --- creating and signing in ------------------------------------------------------

def test_an_account_signs_in_with_its_email_and_password():
    made = accounts.create_account("Ana@Example.com", PASSWORD, "Ana")
    assert made.username == "ana@example.com"
    assert made.email == "ana@example.com"
    assert made.has_password and not made.administrator

    signed_in = accounts.authenticate("ANA@example.com ", PASSWORD)
    assert signed_in.user_id == made.user_id
    assert signed_in.last_login_at


def test_wrong_email_and_wrong_password_say_the_same_thing():
    accounts.create_account("ana@example.com", PASSWORD)
    with pytest.raises(accounts.SignInFailed) as wrong_password:
        accounts.authenticate("ana@example.com", "nope nope nope")
    with pytest.raises(accounts.SignInFailed) as no_account:
        accounts.authenticate("bob@example.com", PASSWORD)
    assert str(wrong_password.value) == str(no_account.value)


def test_five_wrong_passwords_lock_the_account_even_to_the_right_one():
    accounts.create_account("ana@example.com", PASSWORD)
    for _ in range(accounts.MAX_FAILED_LOGINS):
        with pytest.raises(accounts.SignInFailed):
            accounts.authenticate("ana@example.com", "wrong password here")
    with pytest.raises(accounts.SignInFailed):
        accounts.authenticate("ana@example.com", PASSWORD)


def test_the_lock_lifts_and_a_right_password_clears_the_count():
    made = accounts.create_account("ana@example.com", PASSWORD)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with store.write() as conn:
        conn.execute("UPDATE users SET failed_logins = 5, locked_until = ? WHERE user_id = ?",
                     (past, made.user_id))
    accounts.authenticate("ana@example.com", PASSWORD)
    with store.read() as conn:
        row = conn.execute("SELECT failed_logins, locked_until FROM users").fetchone()
    assert row["failed_logins"] == 0 and row["locked_until"] is None


def test_a_disabled_account_cannot_sign_in():
    admin = accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.update_account(ana.user_id, actor_user_id=admin.user_id, is_active=False)
    with pytest.raises(accounts.SignInFailed):
        accounts.authenticate("ana@example.com", PASSWORD)


@pytest.mark.parametrize("email", ["", "ana", "ana@", "@example.com", "a b@example.com"])
def test_an_invalid_email_is_refused(email):
    with pytest.raises(accounts.AccountError):
        accounts.create_account(email, PASSWORD)


def test_a_short_password_is_refused():
    with pytest.raises(accounts.AccountError, match="at least"):
        accounts.create_account("ana@example.com", "short")


def test_one_email_one_account():
    accounts.create_account("ana@example.com", PASSWORD)
    with pytest.raises(accounts.AccountError, match="already exists"):
        accounts.create_account("ANA@example.com", PASSWORD)


def test_signup_follows_the_signup_mode(monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "invite")
    with pytest.raises(accounts.AccountError, match="invitation"):
        accounts.sign_up("ana@example.com", PASSWORD, "Test Person")
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    account, token = accounts.sign_up("ana@example.com", PASSWORD, "Test Person")
    assert account.email == "ana@example.com" and not account.email_verified
    assert token


# --- confirming an email ---------------------------------------------------------

def test_a_signed_up_account_signs_in_only_after_confirming(monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    _account, token = accounts.sign_up("ana@example.com", PASSWORD, "Test Person")
    with pytest.raises(accounts.EmailNotVerified):
        accounts.authenticate("ana@example.com", PASSWORD)

    confirmed = accounts.verify_email(token)
    assert confirmed.email_verified
    assert accounts.authenticate("ana@example.com", PASSWORD).user_id == confirmed.user_id


def test_the_unconfirmed_message_needs_the_right_password(monkeypatch):
    """A wrong password gets the usual answer, so the unconfirmed message tells
    a stranger nothing about which emails have accounts."""
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    accounts.sign_up("ana@example.com", PASSWORD, "Test Person")
    with pytest.raises(accounts.SignInFailed):
        accounts.authenticate("ana@example.com", "wrong password here")


def test_a_confirmation_link_works_once_and_only_the_newest_works(monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    account, first = accounts.sign_up("ana@example.com", PASSWORD, "Test Person")
    second = accounts.start_verification(account.user_id)
    with pytest.raises(accounts.AccountError, match="not valid"):
        accounts.verify_email(first)
    accounts.verify_email(second)
    with pytest.raises(accounts.AccountError, match="not valid"):
        accounts.verify_email(second)


def test_an_expired_confirmation_link_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    _account, token = accounts.sign_up("ana@example.com", PASSWORD, "Test Person")
    with store.write() as conn:
        conn.execute("UPDATE email_tokens SET expires_at = ?",
                     ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),))
    with pytest.raises(accounts.AccountError):
        accounts.verify_email(token)


def test_resending_reveals_nothing_for_unknown_or_confirmed_emails(monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    assert accounts.resend_verification("nobody@example.com") is None
    accounts.create_account("done@example.com", PASSWORD)  # already confirmed
    assert accounts.resend_verification("done@example.com") is None
    accounts.sign_up("ana@example.com", PASSWORD, "Test Person")
    found = accounts.resend_verification("ANA@example.com")
    assert found is not None and found[0].email == "ana@example.com"


def test_an_invited_account_starts_confirmed():
    _invitation, token = accounts.create_invitation("new@example.com")
    made = accounts.accept_invitation(token, PASSWORD)
    assert made.email_verified
    assert accounts.authenticate("new@example.com", PASSWORD).user_id == made.user_id


def test_an_unconfirmed_admin_email_is_not_an_administrator(monkeypatch):
    """Otherwise whoever signed up first with the administrator's address
    would become the administrator."""
    monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["boss@example.com"])
    account, token = accounts.sign_up("boss@example.com", PASSWORD, "Test Person")
    assert not account.administrator
    assert accounts.verify_email(token).administrator


# --- a Snowflake role is not app administration ------------------------------------

def test_linking_snowflake_never_makes_an_administrator(monkeypatch):
    """Spec 0007: admin is the app's own decision. Linking a Snowflake user,
    whatever its role, leaves the flag exactly as it was."""
    monkeypatch.setattr(settings, "ADMIN_ROLES", ["DATAALPHA_ADMIN"])
    alice = accounts.create_account("alice@example.com", PASSWORD)
    linked = accounts.link_snowflake(alice.user_id, "ALICE_SF", "enc")
    assert not linked.administrator
    assert not hasattr(accounts, "promote_from_role")


def test_admin_emails_make_an_administrator_without_a_stored_flag(monkeypatch):
    made = accounts.create_account("boss@example.com", PASSWORD)
    assert not made.administrator
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["BOSS@example.com"])
    assert accounts.get_account(made.user_id).administrator


# --- changing a password ------------------------------------------------------------

def test_changing_a_password_needs_the_current_one():
    made = accounts.create_account("ana@example.com", PASSWORD)
    with pytest.raises(accounts.AccountError, match="current password"):
        accounts.change_password(made.user_id, "a brand new password", current_password="wrong")
    accounts.change_password(made.user_id, "a brand new password", current_password=PASSWORD)
    accounts.authenticate("ana@example.com", "a brand new password")


def legacy_snowflake_account(user: str, token: str = "enc") -> accounts.Account:
    """An account as the removed Snowflake sign-in left it: username the
    Snowflake user, no email, no password, a linked Snowflake credential."""
    from datetime import UTC, datetime

    with store.write() as conn:
        user_id = accounts._insert_user(conn, username=user.upper(), email=None,
                                        display_name=None, is_admin=False,
                                        created_by="snowflake-sign-in")
        conn.execute("INSERT INTO credentials (user_id, kind, subject, secret, updated_at) "
                     "VALUES (?, 'snowflake', ?, ?, ?)",
                     (user_id, user.upper(), token, datetime.now(UTC).isoformat()))
    return accounts.get_account(user_id)


def test_a_snowflake_account_adds_an_email_and_password():
    made = legacy_snowflake_account("ALICE", "enc-token")
    assert made.email is None and not made.has_password
    with pytest.raises(accounts.AccountError, match="valid email"):
        accounts.change_password(made.user_id, "a brand new password")

    updated = accounts.change_password(made.user_id, "a brand new password",
                                       email="alice@example.com")
    assert updated.email == "alice@example.com" and updated.has_password
    assert updated.username == "ALICE" and not updated.email_verified

    # The new address must be confirmed before it signs in.
    with pytest.raises(accounts.EmailNotVerified):
        accounts.authenticate("alice@example.com", "a brand new password")
    accounts.verify_email(accounts.start_verification(made.user_id))
    assert accounts.authenticate("alice@example.com", "a brand new password").user_id == made.user_id


# --- invitations --------------------------------------------------------------------

def test_an_invitation_makes_one_account_and_then_stops_working():
    invitation, token = accounts.create_invitation("new@example.com", is_admin=True,
                                                   created_by="admin")
    assert accounts.invitation_email(token) == "new@example.com"

    made = accounts.accept_invitation(token, PASSWORD, "New Person")
    assert made.email == "new@example.com" and made.administrator
    assert made.display_name == "New Person"

    with pytest.raises(accounts.AccountError, match="not valid"):
        accounts.accept_invitation(token, PASSWORD)
    assert accounts.pending_invitations() == []


def test_only_a_hash_of_the_invitation_token_is_stored():
    _invitation, token = accounts.create_invitation("new@example.com")
    with store.read() as conn:
        stored = conn.execute("SELECT token_hash FROM invitations").fetchone()[0]
    assert token not in stored


def test_an_expired_invitation_is_refused():
    _invitation, token = accounts.create_invitation("new@example.com")
    with store.write() as conn:
        conn.execute("UPDATE invitations SET expires_at = ?",
                     ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),))
    with pytest.raises(accounts.AccountError):
        accounts.invitation_email(token)


def test_a_new_invitation_replaces_the_old_link():
    _first, old = accounts.create_invitation("new@example.com")
    _second, new = accounts.create_invitation("new@example.com")
    with pytest.raises(accounts.AccountError):
        accounts.invitation_email(old)
    assert accounts.invitation_email(new) == "new@example.com"


def test_no_invitation_for_an_email_that_has_an_account():
    accounts.create_account("ana@example.com", PASSWORD)
    with pytest.raises(accounts.AccountError, match="already exists"):
        accounts.create_invitation("ana@example.com")


def test_a_revoked_invitation_is_gone():
    invitation, token = accounts.create_invitation("new@example.com")
    accounts.revoke_invitation(invitation.invitation_id)
    with pytest.raises(accounts.AccountError):
        accounts.invitation_email(token)


# --- Snowflake ------------------------------------------------------------------------

def test_nothing_makes_an_account_from_a_snowflake_identity():
    """Snowflake is a connection, not a way in (spec 0007). The accounts it
    made before keep their ids and usernames; nothing makes new ones."""
    assert not hasattr(accounts, "provision_from_snowflake")
    before = len(accounts.list_accounts())
    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.link_snowflake(ana.user_id, "SOMEONE_ELSE", "enc")
    assert len(accounts.list_accounts()) == before + 1


def test_an_old_snowflake_account_keeps_its_snowflake_link():
    old = legacy_snowflake_account("alice", "enc-1")
    assert old.username == "ALICE" and old.snowflake_user == "ALICE"
    assert accounts.account_for_snowflake("alice").user_id == old.user_id
    accounts.store_snowflake_token(old.user_id, "enc-2")
    assert accounts.snowflake_token(old.user_id) == "enc-2"


def test_a_password_account_links_snowflake_and_keeps_its_username():
    made = accounts.create_account("ana@example.com", PASSWORD)
    linked = accounts.link_snowflake(made.user_id, "ana_sf", "enc")
    assert linked.snowflake_user == "ANA_SF" and linked.username == "ana@example.com"
    assert accounts.account_for_snowflake("ANA_SF").user_id == made.user_id


def test_one_snowflake_user_cannot_link_to_two_accounts():
    ana = accounts.create_account("ana@example.com", PASSWORD)
    bob = accounts.create_account("bob@example.com", PASSWORD)
    accounts.link_snowflake(ana.user_id, "SHARED", "enc")
    with pytest.raises(accounts.SnowflakeAlreadyLinked):
        accounts.link_snowflake(bob.user_id, "shared", "enc")


def test_unlinking_needs_another_way_to_sign_in():
    only_snowflake = legacy_snowflake_account("ALICE")
    with pytest.raises(accounts.AccountError, match="password"):
        accounts.unlink_snowflake(only_snowflake.user_id)

    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.link_snowflake(ana.user_id, "ANA_SF", "enc")
    accounts.unlink_snowflake(ana.user_id)
    assert accounts.snowflake_token(ana.user_id) is None


def test_a_link_request_is_single_use_and_expires():
    ana = accounts.create_account("ana@example.com", PASSWORD)
    accounts.start_snowflake_link(ana.user_id, "state-1", 600)
    assert accounts.finish_snowflake_link("state-1") == ana.user_id
    assert accounts.finish_snowflake_link("state-1") is None

    accounts.start_snowflake_link(ana.user_id, "state-2", -1)
    assert accounts.finish_snowflake_link("state-2") is None
    assert accounts.finish_snowflake_link("never-issued") is None


# --- administration ---------------------------------------------------------------------

def test_you_cannot_remove_your_own_access():
    admin = accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    with pytest.raises(accounts.AccountError, match="own access"):
        accounts.update_account(admin.user_id, actor_user_id=admin.user_id, is_admin=False)


def test_the_last_administrator_stays_one():
    admin = accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    other = accounts.create_account("other@example.com", PASSWORD)
    with pytest.raises(accounts.AccountError, match="only administrator"):
        accounts.update_account(admin.user_id, actor_user_id=other.user_id, is_admin=False)

    accounts.update_account(other.user_id, actor_user_id=admin.user_id, is_admin=True)
    accounts.update_account(admin.user_id, actor_user_id=other.user_id, is_admin=False)
    assert not accounts.get_account(admin.user_id).administrator


def test_reenabling_an_account_clears_its_lock():
    admin = accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    ana = accounts.create_account("ana@example.com", PASSWORD)
    for _ in range(accounts.MAX_FAILED_LOGINS):
        with pytest.raises(accounts.SignInFailed):
            accounts.authenticate("ana@example.com", "wrong password here")
    accounts.update_account(ana.user_id, actor_user_id=admin.user_id, is_active=False)
    accounts.update_account(ana.user_id, actor_user_id=admin.user_id, is_active=True)
    accounts.authenticate("ana@example.com", PASSWORD)


def test_two_connections_see_each_others_writes():
    """Four workers share one file: a write through one connection must be
    visible to the next connection that opens."""
    accounts.create_account("ana@example.com", PASSWORD)
    raw = sqlite3.connect(settings.APP_DB_PATH)
    try:
        assert raw.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        raw.close()
