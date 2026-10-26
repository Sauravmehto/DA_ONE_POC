from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel


#: A token that has already expired, for a session holding no Snowflake token.
NO_TOKEN_EXPIRY = datetime(1970, 1, 1, tzinfo=UTC)


class SessionData(BaseModel):
    session_id: str
    #: Who this is, as the rest of the app records it: conversation owners,
    #: created_by, the audit log. The account's username. The name predates
    #: accounts, when the only identity was the Snowflake user; for anyone who
    #: first signed in through Snowflake the two are still the same.
    snowflake_user: str
    role: str | None = None
    #: The Snowflake connection this session carries, if any. Empty for a
    #: password sign-in until the account's linked Snowflake is first used.
    access_token: str = ""
    refresh_token_encrypted: str = ""
    token_expires_at: datetime = NO_TOKEN_EXPIRY
    # How this session signed in. "oauth": through Snowflake. "password": an
    # app account's email and password. "keypair" is the TEMPORARY dev-login
    # shared service account. Defaults to "oauth" so sessions stored before
    # accounts existed read back unchanged.
    auth_mode: Literal["oauth", "keypair", "externalbrowser", "password"] = "oauth"
    #: The app account behind this session. None for a dev-login session and
    #: for sessions stored before accounts existed.
    user_id: str | None = None
    email: str | None = None
    #: The account's users.session_epoch when this session began. Behind the
    #: account's current value means a password reset or change has ended it.
    #: 0 for sessions stored before the field existed, which is every
    #: account's starting value.
    session_epoch: int = 0

    @property
    def has_snowflake(self) -> bool:
        """Whether this session can query Snowflake as its person right now."""
        return self.auth_mode == "keypair" or bool(self.access_token)


class SessionStore(Protocol):
    async def get(self, session_id: str) -> SessionData | None: ...

    async def set(self, session_id: str, data: SessionData, ttl_seconds: int) -> None: ...

    async def delete(self, session_id: str) -> None: ...

    async def set_oauth_state(self, state: str, ttl_seconds: int) -> None: ...

    async def consume_oauth_state(self, state: str) -> bool: ...

    async def close(self) -> None: ...
