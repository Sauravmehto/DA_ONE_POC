import asyncio

from fastapi import Depends, HTTPException, Request, status

from app.accounts import service as accounts
from app.accounts import trial
from app.core.config import settings
from app.engine import audit
from app.session.base import SessionData


async def get_current_session(request: Request) -> SessionData:
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    session_store = request.app.state.session_store
    session = await session_store.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    # A disabled account stops working on its next request, not when its
    # session happens to expire.
    if session.user_id:
        account = await asyncio.to_thread(accounts.get_account, session.user_id)
        if account is None or not account.is_active:
            await session_store.delete(session_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account has been disabled",
            )
        # Its password was reset or changed after this session began.
        if session.session_epoch != account.session_epoch:
            await session_store.delete(session_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Your password was changed. Sign in again.",
            )

        # A trial that has run out ends the app for this person, except the two
        # calls the page needs to say so and let them go.
        if not account.administrator and not request.url.path.endswith(
            ("/auth/me", "/auth/logout")
        ):
            current = await asyncio.to_thread(trial.status, account.email)
            if current and current["expired"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"message": "Your free trial has ended.", "code": "trial_expired"},
                )

    # Every audit row this request writes names this login and how it signed in.
    audit.bind_session(session.session_id, session.auth_mode)
    return session


async def count_trial_message(session: SessionData) -> None:
    """One chat message against this person's trial allowance. Administrators
    and accounts with no trial are never counted."""
    if not session.user_id:
        return
    account = await asyncio.to_thread(accounts.get_account, session.user_id)
    if account is None or account.administrator:
        return
    if not await asyncio.to_thread(trial.use_message, account.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": f"Your free trial includes {settings.TRIAL_MESSAGE_CAP} "
                           "messages and you have used them all.",
                "code": "trial_message_cap",
            },
        )


async def session_is_admin(session: SessionData) -> bool:
    """An administrator is an ACCOUNT marked as one, or one with a confirmed
    email listed in ADMIN_EMAILS. The same whichever way the person signed in.

    A Snowflake role never makes anyone an administrator (spec 0007); the
    one exception is the dev key-pair login, which has no account."""
    if not session.user_id:
        return session.auth_mode == "keypair" and settings.is_admin_role(session.role)
    account = await asyncio.to_thread(accounts.get_account, session.user_id)
    return bool(account and account.is_active and account.administrator)


async def can_author_snowflake(session: SessionData) -> bool:
    """Whether this session may SAVE Snowflake-side configuration: the
    semantic layer and Excel report templates. That is a Snowflake permission
    rather than an app one: the active Snowflake role must be in ADMIN_ROLES.
    Opening and reading those tools needs only a Snowflake connection."""
    return session.has_snowflake and settings.is_admin_role(session.role)


async def snowflake_session(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> SessionData:
    """This session with its Snowflake connection ready and its active role
    known. A password sign-in has neither until it first uses Snowflake; this
    restores the linked connection (409 when there is none) and asks Snowflake
    which role it is running as, once, keeping the answer on the session."""
    from app.services.snowflake import SnowflakeService

    service = SnowflakeService(
        settings, request.app.state.session_store, request.app.state.token_encryptor
    )
    if not session.has_snowflake:
        session = await service.restore_linked(session)
    if session.role is None and session.auth_mode != "keypair":
        role = await service.current_role(session)
        if role:
            session = session.model_copy(update={"role": role})
            await request.app.state.session_store.set(
                session.session_id, session, settings.SESSION_TTL_SECONDS
            )
    return session


async def has_snowflake_link(session: SessionData) -> bool:
    """Whether this account has connected Snowflake at all.

    A local check — is a link stored — not a live connection, and the two are
    different answers. Somebody who has never connected has no Snowflake
    databases and no Snowflake dashboards: a fact, and an empty list. Somebody
    whose stored token has died is asked to connect again, which is worth
    saying. Telling the first person to connect their Snowflake account, when
    they have no Snowflake and never will, is an error message about somebody
    else's problem.
    """
    if session.has_snowflake:
        return True
    if not session.user_id:
        return False
    return bool(await asyncio.to_thread(accounts.snowflake_token, session.user_id))


async def require_snowflake_author(
    session: SessionData = Depends(snowflake_session),
) -> SessionData:
    """Gate saving Snowflake-side configuration on the active Snowflake role."""
    if not await can_author_snowflake(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Saving this needs a Snowflake role listed in ADMIN_ROLES. "
                f"Your active Snowflake role is {session.role or 'not known'}."
            ),
        )
    return session


async def require_admin(
    session: SessionData = Depends(get_current_session),
) -> SessionData:
    """Gate a route on the signed-in person being an administrator.

    403 rather than 404: the admin surface is not a secret, and telling
    somebody they lack access is more useful than pretending the page does not
    exist. What IS hidden is anything about databases they cannot see — that
    filtering happens per-route.
    """
    if not await session_is_admin(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an administrator.",
        )
    return session
