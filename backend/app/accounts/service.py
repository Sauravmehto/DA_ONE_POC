"""App accounts: who a person is, independent of any data source.

Sign-in methods hang off an account rather than being the account. A person
may sign in with an email and password, or through Snowflake; either way they
land on the same account, and the rest of the app sees one `username`.

Snowflake is a CONNECTION an account links, not the identity itself. Linking
stores the person's Snowflake refresh token (encrypted) against their account,
so a password sign-in can still query Snowflake as that person, under their
own grants, without making them go through Snowflake's consent screen each time.

Everything here is synchronous; routes call it with asyncio.to_thread.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.accounts import passwords, store
from app.core.config import settings

#: Failed password attempts before an account is locked for LOCK_MINUTES.
#: A backstop, not the main defence: the per-email rate limit
#: (app/core/rate_limit.py) does the day-to-day work. At 5, anyone who knew an
#: address could lock its owner out with five guesses (spec 0007).
MAX_FAILED_LOGINS = 20
LOCK_MINUTES = 15

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(ValueError):
    """A request about an account cannot be done; the message says why, and
    is written to be shown to the person."""


class SignInFailed(AccountError):
    """Deliberately one message for a wrong email, a wrong password and a
    locked or disabled account: telling them apart tells a stranger which
    emails have accounts."""

    def __init__(self) -> None:
        super().__init__("That email and password do not match an account.")


class SnowflakeAlreadyLinked(AccountError):
    pass


class EmailNotVerified(AccountError):
    """The password was right, but the email has not been confirmed. Only
    said to someone who knew the password, so it reveals nothing to a
    stranger."""

    def __init__(self, email: str) -> None:
        super().__init__(
            f"Confirm your email first. We sent a link to {email}; "
            "ask for a new one if it has expired."
        )
        self.email = email


@dataclass(frozen=True)
class Account:
    user_id: str
    username: str
    email: str | None
    display_name: str | None
    is_admin: bool
    is_active: bool
    created_at: str
    created_by: str | None
    last_login_at: str | None
    has_password: bool = False
    snowflake_user: str | None = None
    email_verified: bool = False
    admin_set_by: str | None = None
    session_epoch: int = 0

    @property
    def administrator(self) -> bool:
        """The stored flag, or a CONFIRMED email listed in ADMIN_EMAILS. An
        unconfirmed one does not count: otherwise whoever signed up first with
        the administrator's address would become the administrator."""
        return self.is_admin or (self.email_verified and settings.is_admin_email(self.email))

    @property
    def label(self) -> str:
        return self.display_name or self.email or self.username

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "display_name": self.display_name,
            "is_admin": self.administrator,
            "is_admin_by_setting": self.email_verified and settings.is_admin_email(self.email),
            "email_verified": self.email_verified,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "last_login_at": self.last_login_at,
            "has_password": self.has_password,
            "snowflake_user": self.snowflake_user,
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(email: str | None) -> str:
    value = (email or "").strip().lower()
    if not _EMAIL_RE.match(value) or len(value) > 254:
        raise AccountError("Enter a valid email address.")
    return value


def check_password_rules(password: str, email: str | None = None) -> None:
    """NIST SP 800-63B rather than composition rules: long enough, not one of
    the passwords people reuse most, and not built from the email."""
    if len(password or "") < settings.PASSWORD_MIN_LENGTH:
        raise AccountError(
            f"Use at least {settings.PASSWORD_MIN_LENGTH} characters for the password."
        )
    if len(password) > passwords.MAX_LENGTH:
        raise AccountError("That password is too long.")
    if passwords.is_common(password):
        raise AccountError("That password is one of the most commonly used. Choose another.")
    local = (email or "").split("@", 1)[0].strip().lower()
    if len(local) >= 3 and local in password.lower():
        raise AccountError("The password must not contain your email address.")


def _clean_name(display_name: str | None) -> str | None:
    value = (display_name or "").strip()
    return value[:120] or None


# --- reading ------------------------------------------------------------------

_ACCOUNT_SELECT = """
    SELECT u.*,
           p.user_id IS NOT NULL AS has_password,
           s.subject AS snowflake_user
    FROM users u
    LEFT JOIN credentials p ON p.user_id = u.user_id AND p.kind = 'password'
    LEFT JOIN credentials s ON s.user_id = u.user_id AND s.kind = 'snowflake'
"""


def _account(row: Any) -> Account:
    return Account(
        user_id=row["user_id"],
        username=row["username"],
        email=row["email"],
        display_name=row["display_name"],
        is_admin=bool(row["is_admin"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        created_by=row["created_by"],
        last_login_at=row["last_login_at"],
        has_password=bool(row["has_password"]),
        snowflake_user=row["snowflake_user"],
        email_verified=bool(row["email_verified_at"]),
        admin_set_by=row["admin_set_by"],
        session_epoch=int(row["session_epoch"] or 0),
    )


def get_account(user_id: str) -> Account | None:
    with store.read() as conn:
        row = conn.execute(f"{_ACCOUNT_SELECT} WHERE u.user_id = ?", (user_id,)).fetchone()
    return _account(row) if row else None


def account_by_email(email: str) -> Account | None:
    with store.read() as conn:
        row = conn.execute(f"{_ACCOUNT_SELECT} WHERE u.email = ?", (email,)).fetchone()
    return _account(row) if row else None


def list_accounts() -> list[Account]:
    with store.read() as conn:
        rows = conn.execute(f"{_ACCOUNT_SELECT} ORDER BY u.created_at").fetchall()
    return [_account(r) for r in rows]


def any_administrator() -> bool:
    """Whether anyone can administer this deployment: a stored admin, or an
    ADMIN_EMAILS entry (which applies as soon as that person signs up)."""
    if settings.ADMIN_EMAILS:
        return True
    with store.read() as conn:
        return conn.execute(
            "SELECT 1 FROM users WHERE is_admin = 1 AND is_active = 1 LIMIT 1"
        ).fetchone() is not None


# --- creating -----------------------------------------------------------------

def _insert_user(conn: Any, *, username: str, email: str | None,
                 display_name: str | None, is_admin: bool,
                 created_by: str | None, email_verified: bool = False) -> str:
    user_id = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO users (user_id, username, email, display_name, is_admin, "
            "created_at, created_by, email_verified_at, admin_set_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, email, display_name, int(is_admin), _stamp(), created_by,
             _stamp() if email_verified else None, "admin" if is_admin else None),
        )
    except Exception as exc:  # sqlite3.IntegrityError: a unique column clashed
        if "UNIQUE" in str(exc).upper():
            raise AccountError("An account with that email already exists.") from exc
        raise
    return user_id


def _set_password(conn: Any, user_id: str, password: str) -> None:
    conn.execute(
        "INSERT INTO credentials (user_id, kind, subject, secret, updated_at) "
        "VALUES (?, 'password', ?, ?, ?) "
        "ON CONFLICT (user_id, kind) DO UPDATE SET secret = excluded.secret, "
        "updated_at = excluded.updated_at",
        (user_id, user_id, passwords.hash_password(password), _stamp()),
    )


def create_account(email: str, password: str, display_name: str | None = None, *,
                   is_admin: bool = False, created_by: str | None = None,
                   email_verified: bool = True) -> Account:
    """An account that signs in with email and password. Its username is the
    email, which is unique and means something to whoever reads the audit log.

    A caller that has not proven the address belongs to this person passes
    email_verified=False; sign_up does."""
    address = normalise_email(email)
    check_password_rules(password, email=address)
    with store.write() as conn:
        user_id = _insert_user(
            conn, username=address, email=address, display_name=_clean_name(display_name),
            is_admin=is_admin, created_by=created_by, email_verified=email_verified,
        )
        _set_password(conn, user_id, password)
    account = get_account(user_id)
    assert account is not None
    return account


def sign_up(email: str, password: str, display_name: str | None = None
            ) -> tuple[Account, str | None]:
    """A new account, and the token that confirms its email. By default it
    cannot sign in with its password until that link has been opened. With
    EMAIL_VERIFICATION_REQUIRED off there is nothing to open: the token is None,
    and the account can sign in at once (its email stays unconfirmed)."""
    if settings.SIGNUP_MODE != "open":
        raise AccountError("Accounts on this deployment are created by invitation.")
    # Someone signing themselves up gives their name; an invitation does not
    # ask, because the administrator who sent it knows who it is for.
    if not _clean_name(display_name):
        raise AccountError("Enter your full name.")
    account = create_account(email, password, display_name, email_verified=False)
    if not settings.EMAIL_VERIFICATION_REQUIRED:
        return account, None
    return account, start_verification(account.user_id)


# --- confirming an email ----------------------------------------------------------

def start_verification(user_id: str) -> str:
    """A single-use token confirming this account's current email. Any earlier
    unused one stops working, so only the newest link does."""
    account = get_account(user_id)
    if account is None or not account.email:
        raise AccountError("This account has no email to confirm.")
    token = secrets.token_urlsafe(32)
    with store.write() as conn:
        conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = 'verify' "
                     "AND used_at IS NULL", (user_id,))
        conn.execute(
            "INSERT INTO email_tokens (token_hash, user_id, purpose, email, created_at, "
            "expires_at) VALUES (?, ?, 'verify', ?, ?, ?)",
            (_hash_token(token), user_id, account.email, _stamp(),
             _stamp(_now() + timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS))),
        )
    return token


def verify_email(token: str) -> Account:
    """Mark the email the token was sent to as confirmed. Refused if the
    account's email has changed since, so an old link cannot confirm a new
    address."""
    with store.write() as conn:
        row = conn.execute(
            "SELECT t.user_id, t.email, t.expires_at, t.used_at, u.email AS current, "
            "u.is_active FROM email_tokens t JOIN users u USING (user_id) "
            "WHERE t.token_hash = ? AND t.purpose = 'verify'",
            (_hash_token(token or ""),),
        ).fetchone()
        if (row is None or row["used_at"] or not row["is_active"]
                or datetime.fromisoformat(row["expires_at"]) <= _now()
                or (row["current"] or "").lower() != row["email"].lower()):
            raise AccountError("This confirmation link is not valid any more. Ask for a new one.")
        conn.execute("UPDATE users SET email_verified_at = ?, last_login_at = ? "
                     "WHERE user_id = ?", (_stamp(), _stamp(), row["user_id"]))
        conn.execute("UPDATE email_tokens SET used_at = ? WHERE token_hash = ?",
                     (_stamp(), _hash_token(token)))
    account = get_account(row["user_id"])
    assert account is not None
    return account


def resend_verification(email: str) -> tuple[Account, str] | None:
    """A new link for an unconfirmed account, or None when there is nothing to
    send. The caller answers the same either way, so asking reveals nothing
    about which emails have accounts."""
    try:
        address = normalise_email(email)
    except AccountError:
        return None
    account = account_by_email(address)
    if account is None or account.email_verified or not account.is_active:
        return None
    return account, start_verification(account.user_id)


# --- signing in ---------------------------------------------------------------

def authenticate(email: str, password: str) -> Account:
    """The account these credentials belong to, or SignInFailed.

    Five wrong passwords lock the account for fifteen minutes. The count lives
    in the file rather than in memory so all four workers share it; otherwise
    each worker would allow five guesses of its own."""
    try:
        address = normalise_email(email)
    except AccountError:
        passwords.spend_equal_time(password)
        raise SignInFailed() from None

    with store.read() as conn:
        row = conn.execute(
            "SELECT u.user_id, u.is_active, u.locked_until, c.secret FROM users u "
            "LEFT JOIN credentials c ON c.user_id = u.user_id AND c.kind = 'password' "
            "WHERE u.email = ?",
            (address,),
        ).fetchone()

    if row is None or not row["secret"]:
        passwords.spend_equal_time(password)
        raise SignInFailed()

    locked_until = row["locked_until"]
    if locked_until and datetime.fromisoformat(locked_until) > _now():
        passwords.spend_equal_time(password)
        raise SignInFailed()

    if not passwords.verify_password(password, row["secret"]):
        with store.write() as conn:
            conn.execute(
                "UPDATE users SET failed_logins = failed_logins + 1, "
                "locked_until = CASE WHEN failed_logins + 1 >= ? THEN ? ELSE locked_until END "
                "WHERE user_id = ?",
                (MAX_FAILED_LOGINS, _stamp(_now() + timedelta(minutes=LOCK_MINUTES)),
                 row["user_id"]),
            )
        raise SignInFailed()

    if not row["is_active"]:
        raise SignInFailed()

    with store.read() as conn:
        verified = conn.execute("SELECT email_verified_at FROM users WHERE user_id = ?",
                                (row["user_id"],)).fetchone()["email_verified_at"]
    if not verified and settings.EMAIL_VERIFICATION_REQUIRED:
        raise EmailNotVerified(address)

    with store.write() as conn:
        if passwords.needs_rehash(row["secret"]):
            _set_password(conn, row["user_id"], password)
        conn.execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ? "
            "WHERE user_id = ?",
            (_stamp(), row["user_id"]),
        )
    account = get_account(row["user_id"])
    assert account is not None
    return account


def record_login(user_id: str) -> None:
    with store.write() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE user_id = ?",
                     (_stamp(), user_id))


def change_password(user_id: str, new_password: str, *, current_password: str | None = None,
                    email: str | None = None) -> Account:
    """Set or change this account's password.

    An account that already has one must give it, so a session left open on a
    shared machine cannot be turned into a permanent takeover. An account made
    by Snowflake sign-in has neither password nor email; it adds both here, and
    from then on can sign in either way."""
    account = get_account(user_id)
    if account is None or not account.is_active:
        raise AccountError("This account is not available.")
    check_password_rules(new_password, email=account.email or email)

    if account.has_password:
        with store.read() as conn:
            row = conn.execute(
                "SELECT secret FROM credentials WHERE user_id = ? AND kind = 'password'",
                (user_id,),
            ).fetchone()
        if not passwords.verify_password(current_password or "", row["secret"] if row else None):
            raise AccountError("The current password is not right.")

    with store.write() as conn:
        if not account.email:
            address = normalise_email(email)
            try:
                # New, so not yet confirmed: the caller sends the link.
                conn.execute("UPDATE users SET email = ?, email_verified_at = NULL "
                             "WHERE user_id = ?", (address, user_id))
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise AccountError("Another account already uses that email.") from exc
                raise
        _set_password(conn, user_id, new_password)
        _end_sessions(conn, user_id)
    updated = get_account(user_id)
    assert updated is not None
    return updated


def _end_sessions(conn: Any, user_id: str) -> None:
    """Every session this account has is over from its next request; see
    users.session_epoch. Unused reset links stop working too: the password
    they would have replaced is gone."""
    conn.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE user_id = ?",
                 (user_id,))
    conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = 'reset' "
                 "AND used_at IS NULL", (user_id,))


# --- password reset -------------------------------------------------------------

def _new_reset_token(conn: Any, user_id: str, email: str,
                     lifetime: timedelta | None = None) -> str:
    """A single-use reset token; only its hash is kept, and any earlier unused
    one stops working, so only the newest link does."""
    lifetime = lifetime or timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES)
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = 'reset' "
                 "AND used_at IS NULL", (user_id,))
    conn.execute(
        "INSERT INTO email_tokens (token_hash, user_id, purpose, email, created_at, "
        "expires_at) VALUES (?, ?, 'reset', ?, ?, ?)",
        (_hash_token(token), user_id, email, _stamp(), _stamp(_now() + lifetime)),
    )
    return token


def start_password_reset(email: str) -> tuple[Account, str] | None:
    """A reset link for an active account with this email, or None. The caller
    answers the same either way, so asking reveals nothing about which emails
    have accounts."""
    try:
        address = normalise_email(email)
    except AccountError:
        return None
    account = account_by_email(address)
    if account is None or not account.is_active:
        return None
    with store.write() as conn:
        token = _new_reset_token(conn, account.user_id, address)
    return account, token


def _reset_row(conn: Any, token: str) -> Any:
    row = conn.execute(
        "SELECT t.user_id, t.email, t.expires_at, t.used_at, u.email AS current, "
        "u.is_active FROM email_tokens t JOIN users u USING (user_id) "
        "WHERE t.token_hash = ? AND t.purpose = 'reset'",
        (_hash_token(token or ""),),
    ).fetchone()
    if (row is None or row["used_at"] or not row["is_active"]
            or datetime.fromisoformat(row["expires_at"]) <= _now()
            or (row["current"] or "").lower() != row["email"].lower()):
        raise AccountError("This reset link is not valid any more. Ask for a new one.")
    return row


def reset_email(token: str) -> str:
    """Which email a reset link is for, so the page can show it."""
    with store.read() as conn:
        return _reset_row(conn, token)["email"]


def reset_password(token: str, new_password: str) -> Account:
    """Set the password a reset link is for. Opening the link proves the
    inbox, so the email is confirmed too; any lock is lifted; and every
    session the account had is ended, since whoever asked for a reset may be
    doing it because someone else is signed in."""
    check_password_rules(new_password, email=None)
    with store.write() as conn:
        row = _reset_row(conn, token)
        check_password_rules(new_password, email=row["email"])
        _set_password(conn, row["user_id"], new_password)
        conn.execute(
            "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), "
            "failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE user_id = ?",
            (_stamp(), _stamp(), row["user_id"]),
        )
        conn.execute("UPDATE email_tokens SET used_at = ? WHERE token_hash = ?",
                     (_stamp(), _hash_token(token)))
        _end_sessions(conn, row["user_id"])
    account = get_account(row["user_id"])
    assert account is not None
    return account


def start_sign_in_setup(user_id: str, email: str) -> tuple[Account, str]:
    """For an administrator: give an account that has no way to sign in with a
    password one, keeping the SAME account — its id, its username, and with
    them every dashboard, chat, report, audit row and group membership.

    For accounts made by the Snowflake sign-in that spec 0007 removed. The
    email is stored unconfirmed and only a reset link is sent to it: nothing
    signs in until its owner opens that link and chooses a password, which is
    what proves the address is theirs. Refused for an account that already
    has a password, so an administrator cannot use this to take one over; it
    may be repeated (to resend, or to correct a mistyped address) until the
    link has been used."""
    account = get_account(user_id)
    if account is None:
        raise AccountError("No such account.")
    if not account.is_active:
        raise AccountError("This account is disabled. Enable it first.")
    if account.has_password:
        raise AccountError("This account already signs in with a password. "
                           "Its owner can reset it from the sign-in page.")
    if account.email and account.email_verified:
        raise AccountError("This account's email is already confirmed.")
    address = normalise_email(email)
    with store.write() as conn:
        try:
            conn.execute("UPDATE users SET email = ?, email_verified_at = NULL "
                         "WHERE user_id = ?", (address, user_id))
        except Exception as exc:  # sqlite3.IntegrityError
            if "UNIQUE" in str(exc).upper():
                raise AccountError("Another account already uses that email.") from exc
            raise
        # As long as an invitation: the person was not expecting this email.
        token = _new_reset_token(conn, user_id, address,
                                 timedelta(hours=settings.INVITATION_TTL_HOURS))
    updated = get_account(user_id)
    assert updated is not None
    return updated, token


# --- invitations --------------------------------------------------------------

@dataclass(frozen=True)
class Invitation:
    invitation_id: str
    email: str
    is_admin: bool
    created_by: str | None
    created_at: str
    expires_at: str

    def public(self) -> dict[str, Any]:
        return {
            "invitation_id": self.invitation_id,
            "email": self.email,
            "is_admin": self.is_admin,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


def create_invitation(email: str, *, is_admin: bool = False,
                      created_by: str | None = None) -> tuple[Invitation, str]:
    """A single-use invitation and its token. Only a hash of the token is
    kept, so the link cannot be recovered from the file; an administrator who
    loses it makes a new one."""
    address = normalise_email(email)
    if account_by_email(address) is not None:
        raise AccountError("An account with that email already exists.")
    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        invitation_id=uuid.uuid4().hex,
        email=address,
        is_admin=is_admin,
        created_by=created_by,
        created_at=_stamp(),
        expires_at=_stamp(_now() + timedelta(hours=settings.INVITATION_TTL_HOURS)),
    )
    with store.write() as conn:
        # A newer invitation for the same email replaces any unused one, so
        # only the most recent link works.
        conn.execute("DELETE FROM invitations WHERE email = ? AND used_at IS NULL", (address,))
        conn.execute(
            "INSERT INTO invitations (invitation_id, token_hash, email, is_admin, "
            "created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (invitation.invitation_id, _hash_token(token), address, int(is_admin),
             created_by, invitation.created_at, invitation.expires_at),
        )
    return invitation, token


def _invitation_row(conn: Any, token: str) -> Any:
    row = conn.execute(
        "SELECT * FROM invitations WHERE token_hash = ?", (_hash_token(token or ""),)
    ).fetchone()
    if row is None or row["used_at"] or datetime.fromisoformat(row["expires_at"]) <= _now():
        raise AccountError("This invitation link is not valid any more. Ask for a new one.")
    return row


def invitation_email(token: str) -> str:
    with store.read() as conn:
        return _invitation_row(conn, token)["email"]


def accept_invitation(token: str, password: str, display_name: str | None = None) -> Account:
    check_password_rules(password)
    with store.write() as conn:
        row = _invitation_row(conn, token)
        check_password_rules(password, email=row["email"])
        # Opening the invitation link proves the email, so it starts confirmed.
        user_id = _insert_user(
            conn, username=row["email"], email=row["email"],
            display_name=_clean_name(display_name), is_admin=bool(row["is_admin"]),
            created_by=row["created_by"], email_verified=True,
        )
        _set_password(conn, user_id, password)
        conn.execute("UPDATE users SET last_login_at = ? WHERE user_id = ?", (_stamp(), user_id))
        conn.execute("UPDATE invitations SET used_at = ? WHERE invitation_id = ?",
                     (_stamp(), row["invitation_id"]))
    account = get_account(user_id)
    assert account is not None
    return account


def pending_invitations() -> list[Invitation]:
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM invitations WHERE used_at IS NULL AND expires_at > ? "
            "ORDER BY created_at DESC",
            (_stamp(),),
        ).fetchall()
    return [
        Invitation(r["invitation_id"], r["email"], bool(r["is_admin"]), r["created_by"],
                   r["created_at"], r["expires_at"])
        for r in rows
    ]


def revoke_invitation(invitation_id: str) -> None:
    with store.write() as conn:
        conn.execute("DELETE FROM invitations WHERE invitation_id = ? AND used_at IS NULL",
                     (invitation_id,))


# --- Snowflake ----------------------------------------------------------------

def account_for_snowflake(snowflake_user: str) -> Account | None:
    subject = (snowflake_user or "").strip().upper()
    with store.read() as conn:
        row = conn.execute(
            f"{_ACCOUNT_SELECT} WHERE s.subject = ?", (subject,)
        ).fetchone()
    return _account(row) if row else None


# There is deliberately no function that makes an account from a Snowflake
# identity. Accounts made that way before spec 0007 still exist, with their
# ids, usernames and history; start_sign_in_setup gives them a password.

def link_snowflake(user_id: str, snowflake_user: str, refresh_token_encrypted: str) -> Account:
    subject = (snowflake_user or "").strip().upper()
    if not subject or subject == "UNKNOWN":
        raise AccountError("Snowflake did not say who signed in.")
    owner = account_for_snowflake(subject)
    if owner is not None and owner.user_id != user_id:
        raise SnowflakeAlreadyLinked(
            f"Snowflake user {subject} is already connected to a different account "
            "here. Sign in to that one and disconnect Snowflake there first, or "
            "sign in with Snowflake instead of connecting it."
        )
    with store.write() as conn:
        conn.execute(
            "INSERT INTO credentials (user_id, kind, subject, secret, updated_at) "
            "VALUES (?, 'snowflake', ?, ?, ?) "
            "ON CONFLICT (user_id, kind) DO UPDATE SET subject = excluded.subject, "
            "secret = excluded.secret, updated_at = excluded.updated_at",
            (user_id, subject, refresh_token_encrypted, _stamp()),
        )
    account = get_account(user_id)
    assert account is not None
    return account


def unlink_snowflake(user_id: str) -> None:
    account = get_account(user_id)
    if account is not None and not account.has_password:
        raise AccountError(
            "Set a password first. Snowflake is this account's only way to sign in."
        )
    with store.write() as conn:
        conn.execute("DELETE FROM credentials WHERE user_id = ? AND kind = 'snowflake'",
                     (user_id,))


def snowflake_token(user_id: str) -> str | None:
    """The encrypted refresh token of this account's linked Snowflake user."""
    with store.read() as conn:
        row = conn.execute(
            "SELECT secret FROM credentials WHERE user_id = ? AND kind = 'snowflake'",
            (user_id,),
        ).fetchone()
    return row["secret"] if row and row["secret"] else None


def store_snowflake_token(user_id: str, refresh_token_encrypted: str) -> None:
    """Keep the newest refresh token after a sign-in or a refresh."""
    with store.write() as conn:
        conn.execute(
            "UPDATE credentials SET secret = ?, updated_at = ? "
            "WHERE user_id = ? AND kind = 'snowflake'",
            (refresh_token_encrypted, _stamp(), user_id),
        )


def start_snowflake_link(user_id: str, state: str, ttl_seconds: int) -> None:
    with store.write() as conn:
        conn.execute("DELETE FROM snowflake_link_requests WHERE expires_at <= ?", (_stamp(),))
        conn.execute(
            "INSERT INTO snowflake_link_requests (state_hash, user_id, expires_at) "
            "VALUES (?, ?, ?)",
            (_hash_token(state), user_id, _stamp(_now() + timedelta(seconds=ttl_seconds))),
        )


def finish_snowflake_link(state: str) -> str | None:
    """Whose account a returning Snowflake sign-in should be linked to, or None
    when it was an ordinary sign-in. Single use."""
    with store.write() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM snowflake_link_requests WHERE state_hash = ?",
            (_hash_token(state or ""),),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM snowflake_link_requests WHERE state_hash = ?",
                     (_hash_token(state),))
    if datetime.fromisoformat(row["expires_at"]) <= _now():
        return None
    return row["user_id"]


# --- administration -------------------------------------------------------------

def _other_active_admin(conn: Any, user_id: str) -> bool:
    for row in conn.execute(
        "SELECT is_admin, email, email_verified_at FROM users "
        "WHERE is_active = 1 AND user_id <> ?", (user_id,),
    ):
        if row["is_admin"] or (row["email_verified_at"] and settings.is_admin_email(row["email"])):
            return True
    return False


def update_account(user_id: str, *, actor_user_id: str | None,
                   is_admin: bool | None = None, is_active: bool | None = None,
                   display_name: str | None = None) -> Account:
    account = get_account(user_id)
    if account is None:
        raise AccountError("No such account.")
    if user_id == actor_user_id and (is_admin is False or is_active is False):
        raise AccountError("You cannot remove your own access. Ask another administrator.")
    with store.write() as conn:
        removing_admin = (is_admin is False and account.is_admin) or (
            is_active is False and account.is_admin
        )
        if removing_admin and not _other_active_admin(conn, user_id):
            raise AccountError("This is the only administrator. Make someone else one first.")
        if is_admin is not None:
            conn.execute("UPDATE users SET is_admin = ?, admin_set_by = 'admin' WHERE user_id = ?",
                         (int(is_admin), user_id))
        if is_active is not None:
            conn.execute(
                "UPDATE users SET is_active = ?, failed_logins = 0, locked_until = NULL "
                "WHERE user_id = ?",
                (int(is_active), user_id),
            )
        if display_name is not None:
            conn.execute("UPDATE users SET display_name = ? WHERE user_id = ?",
                         (_clean_name(display_name), user_id))
    updated = get_account(user_id)
    assert updated is not None
    return updated
