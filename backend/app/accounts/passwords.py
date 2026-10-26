"""Password hashing, and the list of passwords nobody may choose.

Argon2id (argon2-cffi), RFC 9106's recommended low-memory profile by default:
three passes over 64 MiB, four lanes. A few tens of milliseconds per check is
the point: cheap enough for a sign-in, expensive enough that guessing offline
against a stolen file is slow. The parameters are settings, and every hash
carries its own, so raising them later leaves existing hashes readable and
`needs_rehash` says which to upgrade at their next sign-in.

Hashes made before Argon2 are scrypt (`scrypt$N$r$p$salt$digest`). They are
still verified, so nobody is locked out by the change, and the next successful
sign-in replaces each with an Argon2id hash.

ROLLING BACK. The release before this one cannot read Argon2. So the first
stage of a rollout sets PASSWORD_HASH_ALGORITHM=scrypt: this code is live, but
everything it writes is still scrypt, in the old format, and nothing is
upgraded. The second stage, once going back is no longer a worry, sets it to
argon2id and each hash is upgraded at its owner's next sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

#: Longer inputs are refused rather than hashed: nobody types a password this
#: long, and an unbounded one is a cheap way to make the server work hard.
MAX_LENGTH = 1024


@lru_cache(maxsize=1)
def _hasher() -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.PASSWORD_HASH_TIME_COST,
        memory_cost=settings.PASSWORD_HASH_MEMORY_KIB,
        parallelism=settings.PASSWORD_HASH_PARALLELISM,
    )


def hash_password(password: str) -> str:
    if len(password) > MAX_LENGTH:
        raise ValueError("Password is too long.")
    if settings.PASSWORD_HASH_ALGORITHM == "scrypt":
        return _hash_scrypt(password)
    return _hasher().hash(password)


def verify_password(password: str, stored: str | None) -> bool:
    """Whether `password` matches `stored`, in constant time. False for
    anything malformed, never an exception: a damaged row must fail the
    sign-in, not crash it."""
    if not stored or len(password) > MAX_LENGTH:
        return False
    if stored.startswith("$argon2"):
        try:
            return _hasher().verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
            return False
    if stored.startswith("scrypt$"):
        return _verify_scrypt(password, stored)
    return False


def needs_rehash(stored: str | None) -> bool:
    """Whether a stored hash should be replaced at the next sign-in: every
    scrypt hash, and an Argon2 hash made with other parameters than today's."""
    if not stored:
        return False
    if settings.PASSWORD_HASH_ALGORITHM == "scrypt":
        return False        # the rollout's first stage: nothing is rewritten
    if not stored.startswith("$argon2"):
        return True
    try:
        return _hasher().check_needs_rehash(stored)
    except (InvalidHashError, ValueError):
        return True


# --- the scrypt hashes made before Argon2 -------------------------------------------

_SCRYPT_MAXMEM = 128 * 1024 * 1024

#: Exactly what the release before Argon2 wrote, so that release can read it.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


def _hash_scrypt(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=32, maxmem=_SCRYPT_MAXMEM)
    return (f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
            f"{base64.b64encode(salt).decode('ascii')}${base64.b64encode(digest).decode('ascii')}")


def _verify_scrypt(password: str, stored: str) -> bool:
    try:
        _scheme, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected),
                                maxmem=_SCRYPT_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- timing ------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _decoy() -> str:
    return hash_password(secrets.token_urlsafe(16))


def spend_equal_time(password: str) -> None:
    """Checked when an email has no account, so a wrong email takes as long
    as a wrong password and the response time does not say which exist."""
    verify_password(password, _decoy())


# --- passwords nobody may choose -----------------------------------------------------

_COMMON = Path(__file__).with_name("common_passwords.txt")


@lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    """The bundled list (its source and version are in the file's header)."""
    with _COMMON.open(encoding="utf-8") as fh:
        return frozenset(line.rstrip("\n") for line in fh if line and not line.startswith("#"))


def is_common(password: str) -> bool:
    """Whether this is one of the passwords most often used and most often
    found in breaches. Case does not rescue it: "Password123" is "password123"."""
    return password.strip().lower() in _common()
