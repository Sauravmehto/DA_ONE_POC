"""Signing in and out, and connecting Snowflake.

Signing in is email and password, checked against the app's own account file.
It needs no Snowflake at all.

Snowflake is a data source an account CONNECTS, never a way to sign in
(spec 0007). "Connect Snowflake" runs OAuth from inside a signed-in account
and links the Snowflake user who comes back to THAT account, so the account
can query Snowflake as that person, under their own role, afterwards. The
callback finishes nothing else.

The dev key-pair login (DEBUG, its flag and key material) is unchanged and
has no account behind it.
"""

import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.accounts import mailer
from app.accounts import service as accounts
from app.api.deps import (
    can_author_snowflake,
    get_current_session,
    session_is_admin,
    snowflake_session,
)
from app.core import rate_limit
from app.core.config import settings
from app.core.security import TokenEncryptor
from app.services.connection_cache import connection_cache
from app.services.oauth import OAuthService
from app.services.snowflake import SnowflakeNotLinked, SnowflakeService
from app.session.base import NO_TOKEN_EXPIRY, SessionData

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class UserResponse(BaseModel):
    #: The account's username, under the name the frontend has always read.
    snowflake_user: str
    username: str
    email: str | None = None
    display_name: str | None = None
    role: str | None = None
    # Lets the UI hide what this user cannot use. NOT the enforcement point:
    # every admin route checks for itself, because a client is free to ignore
    # this field.
    is_admin: bool = False
    auth_mode: str = "oauth"
    #: Whether Snowflake can be queried as this person: a linked Snowflake user,
    #: or a session signed in through Snowflake.
    snowflake_linked: bool = False
    #: The Snowflake user this account is linked to.
    snowflake_account: str | None = None
    has_password: bool = False
    email_verified: bool = False
    #: Whether the active Snowflake role may save the semantic layer and report
    #: templates. False until the role is known; the builders ask for it.
    can_author_snowflake: bool = False


class SignUpResult(BaseModel):
    status: str = "verification_sent"
    email: str


class ResendRequest(BaseModel):
    email: str = ""


class RolesResponse(BaseModel):
    roles: list[str]
    current: str | None = None


class SetRoleRequest(BaseModel):
    role: str


class PasswordSignIn(BaseModel):
    email: str = ""
    password: str = ""


class SignUpRequest(BaseModel):
    email: str = ""
    password: str = ""
    display_name: str | None = None


class AcceptInvitation(BaseModel):
    token: str = ""
    password: str = ""
    display_name: str | None = None


class ChangePassword(BaseModel):
    new_password: str = ""
    current_password: str | None = None
    #: Required only by an account that has no email yet (one made by a
    #: Snowflake sign-in), since the email is what a password signs in with.
    email: str | None = None


def _get_encryptor(request: Request) -> TokenEncryptor:
    return request.app.state.token_encryptor


def _get_oauth_service() -> OAuthService:
    return OAuthService(settings)


def _get_snowflake_service(request: Request) -> SnowflakeService:
    return SnowflakeService(
        settings,
        request.app.state.session_store,
        request.app.state.token_encryptor,
    )


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.SESSION_TTL_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
    )


def _account_error(exc: accounts.AccountError) -> HTTPException:
    if isinstance(exc, accounts.EmailNotVerified):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": str(exc), "code": "email_not_verified", "email": exc.email},
        )
    if isinstance(exc, accounts.SignInFailed):
        code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(exc, accounts.SnowflakeAlreadyLinked):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


def _redirect_with_error(message: str) -> RedirectResponse:
    """Back to the sign-in page, saying why. For somebody signing IN."""
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/login?error={quote(message)}",
        status_code=status.HTTP_302_FOUND,
    )


def _redirect_signed_in_with_error(message: str) -> RedirectResponse:
    """Back to where the connectors are, saying why.

    A failure while CONNECTING Snowflake belongs here rather than on the
    sign-in page: the person is already signed in, and the sign-in page sends
    anyone who is straight to the app — which swallowed the message and left
    them looking at the same page, no wiser about why nothing happened."""
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/?error={quote(message)}",
        status_code=status.HTTP_302_FOUND,
    )


async def _describe(session: SessionData) -> UserResponse:
    account = (
        await asyncio.to_thread(accounts.get_account, session.user_id)
        if session.user_id else None
    )
    return UserResponse(
        snowflake_user=session.snowflake_user,
        username=session.snowflake_user,
        email=account.email if account else session.email,
        display_name=account.display_name if account else None,
        role=session.role,
        is_admin=await session_is_admin(session),
        auth_mode=session.auth_mode,
        snowflake_linked=session.has_snowflake or bool(account and account.snowflake_user),
        snowflake_account=(account.snowflake_user if account else None)
        or (session.snowflake_user if session.auth_mode in ("oauth", "keypair") else None),
        has_password=bool(account and account.has_password),
        email_verified=bool(account and account.email_verified),
        can_author_snowflake=await can_author_snowflake(session),
    )


def email_link(page: str, token: str) -> str:
    """A link for an email. The token rides in the FRAGMENT, which a browser
    never sends to a server, so it is in no access log (nginx, uvicorn, a
    proxy) on the way to the page; the page posts it to the API in a body."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/{page}#{token}"


def _verification_link(token: str) -> str:
    return email_link("verify", token)


def _reset_link(token: str) -> str:
    return email_link("reset", token)


class TokenBody(BaseModel):
    token: str = ""


def _send_verification(email: str, token: str) -> None:
    mailer.verification_email(email, _verification_link(token))


async def _start_session(request: Request, response: Response,
                         account: accounts.Account) -> SessionData:
    """A fresh session for an account signed in by password. A new id every
    time, so a session id known before sign-in is worthless after it."""
    session_id = secrets.token_urlsafe(32)
    session = SessionData(
        session_id=session_id,
        snowflake_user=account.username,
        auth_mode="password",
        user_id=account.user_id,
        email=account.email,
        session_epoch=account.session_epoch,
    )
    await request.app.state.session_store.set(session_id, session, settings.SESSION_TTL_SECONDS)
    _set_session_cookie(response, session_id)
    return session


# --- Snowflake ------------------------------------------------------------------

@router.get("/snowflake/connect")
async def connect_snowflake(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> RedirectResponse:
    """Connect Snowflake: hand a signed-in account off to Snowflake, and link
    the Snowflake user who comes back to THIS account. Needs a session, so a
    stranger cannot start a link; the callback refuses to finish it in any
    other browser."""
    if not settings.OAUTH_CLIENT_ID or not settings.SNOWFLAKE_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connecting Snowflake is not configured on this deployment.",
        )
    if not session.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only an app account can connect a Snowflake user.",
        )
    state = secrets.token_urlsafe(32)
    await asyncio.to_thread(
        accounts.start_snowflake_link, session.user_id, state, settings.OAUTH_STATE_TTL_SECONDS,
    )
    await request.app.state.session_store.set_oauth_state(state, settings.OAUTH_STATE_TTL_SECONDS)
    return RedirectResponse(url=_get_oauth_service().build_authorize_url(state),
                            status_code=status.HTTP_302_FOUND)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        return _redirect_with_error(error)

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state",
        )

    if not await request.app.state.session_store.consume_oauth_state(state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )

    # Only a link started from a signed-in account is finished here. Anything
    # else is refused BEFORE Snowflake is asked for tokens: nothing is
    # exchanged, no account is made, nothing is stored (spec 0007). Snowflake
    # is a data source an account connects, never a way to sign in.
    link_to = await asyncio.to_thread(accounts.finish_snowflake_link, state)
    if not link_to:
        return _redirect_with_error(
            "Signing in with Snowflake is not available. Sign in with your email and password."
        )
    # And only in the browser that started it, so a link finished in someone
    # else's browser cannot attach their Snowflake to a stranger's account.
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    session = await request.app.state.session_store.get(session_id) if session_id else None
    if session is None or session.user_id != link_to:
        return _redirect_with_error("Sign in to your account, then connect Snowflake again.")

    oauth_service = _get_oauth_service()
    snowflake_service = _get_snowflake_service(request)
    encryptor = _get_encryptor(request)

    try:
        tokens = await oauth_service.exchange_code(code)
    except Exception:  # noqa: BLE001 - say so; never include the code or tokens
        log.warning("Snowflake refused the connection code", exc_info=False)
        return _redirect_signed_in_with_error(
            "Snowflake did not accept the connection. Try Connect Snowflake again."
        )

    # Ask Snowflake who this is and what role they hold. Non-fatal by design:
    # a session with no role connects without one and Snowflake applies the
    # user's own default role regardless, so a failed probe costs us an
    # accurate label in the audit trail, not correct access control. Failing
    # the login here would be trading a real outage for a cosmetic gap.
    resolved_user: str = ""
    resolved_role: str | None = None
    try:
        resolved_user, resolved_role = await snowflake_service.resolve_identity(
            tokens.access_token
        )
    except Exception:  # noqa: BLE001 - see above; login proceeds either way
        log.warning("Could not resolve Snowflake identity at login", exc_info=True)

    snowflake_user = tokens.username or resolved_user or "unknown"
    refresh_encrypted = encryptor.encrypt(tokens.refresh_token)
    token_fields = {
        # Deliberately NOT falling back to SNOWFLAKE_DEFAULT_ROLE: None means
        # "connect without a role", which lets Snowflake apply this user's own
        # default.
        "role": resolved_role,
        "access_token": tokens.access_token,
        "refresh_token_encrypted": refresh_encrypted,
        "token_expires_at": OAuthService.token_expires_at(tokens.expires_in),
    }

    return await _finish_link(request, session, link_to, snowflake_user, refresh_encrypted,
                              token_fields)


async def _finish_link(request: Request, session: SessionData, user_id: str,
                       snowflake_user: str, refresh_encrypted: str,
                       token_fields: dict) -> RedirectResponse:
    """Attach the returning Snowflake user to the account that asked, and give
    that account's current session the connection straight away. The caller
    has already checked that `session` is the account that started the link."""
    try:
        await asyncio.to_thread(accounts.link_snowflake, user_id, snowflake_user,
                                refresh_encrypted)
    except accounts.AccountError as exc:
        return _redirect_signed_in_with_error(str(exc))
    # A Snowflake role never makes the account an administrator: admin is the
    # app's own decision (ADMIN_EMAILS, the Users page). See spec 0007.

    updated = session.model_copy(update=token_fields)
    await request.app.state.session_store.set(session.session_id, updated,
                                              settings.SESSION_TTL_SECONDS)
    connection_cache.evict(session.session_id)
    return RedirectResponse(url=settings.FRONTEND_URL, status_code=status.HTTP_302_FOUND)


@router.post("/snowflake/unlink", response_model=UserResponse)
async def unlink_snowflake(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> UserResponse:
    if not session.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This session has no app account.")
    try:
        await asyncio.to_thread(accounts.unlink_snowflake, session.user_id)
    except accounts.AccountError as exc:
        raise _account_error(exc) from exc
    updated = session.model_copy(update={
        "access_token": "", "refresh_token_encrypted": "",
        "token_expires_at": NO_TOKEN_EXPIRY, "role": None,
        "auth_mode": "password",
    })
    await request.app.state.session_store.set(session.session_id, updated,
                                              settings.SESSION_TTL_SECONDS)
    connection_cache.evict(session.session_id)
    return await _describe(updated)


# --- email and password ---------------------------------------------------------

@router.get("/capabilities")
async def capabilities() -> dict[str, bool]:
    """What login methods this deployment actually offers, so the UI doesn't
    render a button that cannot work."""
    return {
        "password": True,
        "signup": settings.SIGNUP_MODE == "open",
        "password_reset": True,
        # Whether a new account must confirm its email before signing in.
        "email_verification": settings.EMAIL_VERIFICATION_REQUIRED,
        # Whether "Connect Snowflake" can work here. Not a way to sign in.
        "snowflake_link": bool(settings.OAUTH_CLIENT_ID and settings.SNOWFLAKE_ACCOUNT),
        "dev_login": settings.dev_login_available,
    }


@router.post("/password", response_model=UserResponse)
async def password_sign_in(body: PasswordSignIn, request: Request,
                           response: Response) -> UserResponse:
    await rate_limit.enforce(request, "sign_in", email=body.email)
    try:
        account = await asyncio.to_thread(accounts.authenticate, body.email, body.password)
    except accounts.AccountError as exc:
        raise _account_error(exc) from exc
    session = await _start_session(request, response, account)
    return await _describe(session)


@router.post("/signup", response_model=SignUpResult, status_code=status.HTTP_202_ACCEPTED)
async def sign_up(body: SignUpRequest, request: Request, response: Response) -> SignUpResult:
    """Make the account. By default, email a link that confirms the address and
    start no session: the account signs in once the link has been opened. With
    EMAIL_VERIFICATION_REQUIRED off, sign the new account straight in."""
    await rate_limit.enforce(request, "sign_up")
    try:
        account, token = await asyncio.to_thread(
            accounts.sign_up, body.email, body.password, body.display_name
        )
    except accounts.AccountError as exc:
        if settings.SIGNUP_MODE != "open":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        raise _account_error(exc) from exc
    if token is None:
        await _start_session(request, response, account)
        return SignUpResult(status="signed_in", email=account.email or "")
    _send_verification(account.email or "", token)
    return SignUpResult(email=account.email or "")


@router.post("/verify/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(body: ResendRequest, request: Request) -> dict[str, str]:
    """Send a new confirmation link. Answers the same whether or not the email
    has an unconfirmed account, so it cannot be used to find out which do."""
    await rate_limit.enforce(request, "email", email=body.email)
    found = await asyncio.to_thread(accounts.resend_verification, body.email)
    if found is not None:
        account, token = found
        _send_verification(account.email or "", token)
    return {"status": "sent_if_needed"}


@router.post("/verify", response_model=UserResponse)
async def verify_email(body: TokenBody, request: Request, response: Response) -> UserResponse:
    """Confirm the email the link was sent to, and sign that account in. The
    token is in the body, never the path: see email_link."""
    await rate_limit.enforce(request, "token")
    try:
        account = await asyncio.to_thread(accounts.verify_email, body.token)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session = await _start_session(request, response, account)
    return await _describe(session)


class ForgotRequest(BaseModel):
    email: str = ""


class ResetRequest(BaseModel):
    token: str = ""
    password: str = ""


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(body: ForgotRequest, request: Request) -> dict[str, str]:
    """Email a reset link. Answers the same whether or not the email has an
    account, so it cannot be used to find out which do."""
    await rate_limit.enforce(request, "email", email=body.email)
    found = await asyncio.to_thread(accounts.start_password_reset, body.email)
    if found is not None:
        account, token = found
        mailer.reset_email(account.email or "", _reset_link(token))
    return {"status": "sent_if_known"}


@router.post("/password/reset/check")
async def read_reset(body: TokenBody, request: Request) -> dict[str, str]:
    """Which email a reset link is for, so the page can show it."""
    await rate_limit.enforce(request, "token")
    try:
        email = await asyncio.to_thread(accounts.reset_email, body.token)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"email": email}


@router.post("/password/reset", response_model=UserResponse)
async def reset_password(body: ResetRequest, request: Request,
                         response: Response) -> UserResponse:
    """Choose a new password. Every other session of the account ends; this
    browser is signed in with a new one."""
    await rate_limit.enforce(request, "token")
    try:
        account = await asyncio.to_thread(accounts.reset_password, body.token, body.password)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session = await _start_session(request, response, account)
    return await _describe(session)


@router.post("/invitations/check")
async def read_invitation(body: TokenBody, request: Request) -> dict[str, str]:
    """Which email an invitation link is for, so the page can show it."""
    await rate_limit.enforce(request, "token")
    try:
        email = await asyncio.to_thread(accounts.invitation_email, body.token)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"email": email}


@router.post("/invitations/accept", response_model=UserResponse)
async def accept_invitation(body: AcceptInvitation, request: Request,
                            response: Response) -> UserResponse:
    await rate_limit.enforce(request, "token")
    try:
        account = await asyncio.to_thread(
            accounts.accept_invitation, body.token, body.password, body.display_name
        )
    except accounts.AccountError as exc:
        raise _account_error(exc) from exc
    session = await _start_session(request, response, account)
    return await _describe(session)


@router.post("/account/password", response_model=UserResponse)
async def change_password(
    body: ChangePassword,
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> UserResponse:
    if not session.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This session has no app account.")
    # Guessing the current password from a session left open is still guessing.
    await rate_limit.enforce(request, "sign_in", email=session.email or session.user_id)
    try:
        account = await asyncio.to_thread(
            lambda: accounts.change_password(
                session.user_id, body.new_password,
                current_password=body.current_password, email=body.email,
            )
        )
    except accounts.AccountError as exc:
        raise _account_error(exc) from exc
    # Changing the password ended every session of this account; this one,
    # which made the change, carries on.
    session = session.model_copy(update={"email": account.email,
                                         "session_epoch": account.session_epoch})
    await request.app.state.session_store.set(session.session_id, session,
                                              settings.SESSION_TTL_SECONDS)
    if account.email and not account.email_verified:
        token = await asyncio.to_thread(accounts.start_verification, account.user_id)
        _send_verification(account.email, token)
    return await _describe(session)


@router.post("/account/verify", status_code=status.HTTP_202_ACCEPTED)
async def send_my_verification(
    session: SessionData = Depends(get_current_session),
) -> dict[str, str]:
    """Send the signed-in account a new link confirming its email."""
    account = await asyncio.to_thread(accounts.get_account, session.user_id) if session.user_id else None
    if account is None or not account.email or account.email_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="There is no email waiting to be confirmed.")
    token = await asyncio.to_thread(accounts.start_verification, account.user_id)
    _send_verification(account.email, token)
    return {"status": "sent"}


@router.get("/snowflake/author")
async def snowflake_author(
    session: SessionData = Depends(snowflake_session),
) -> dict[str, object]:
    """Whether this person may save the semantic layer and report templates:
    their active Snowflake role, asked of Snowflake if not yet known."""
    return {"can_author": await can_author_snowflake(session), "role": session.role}


# --- dev login, the session, roles ------------------------------------------------

@router.post("/dev-login")
async def dev_login(request: Request, response: Response) -> UserResponse:
    """TEMPORARY dev-only login — DELETE once Snowflake OAuth is live.

    Mints a session backed by the RSA key-pair service account instead of a
    per-user OAuth token. Snowflake authentication is still real; what is
    bypassed is the WEB APP's login, so anyone able to reach this port gets a
    session. It therefore 404s (rather than 403) unless DEBUG *and*
    DEV_LOGIN_ENABLED are both set and the key material is configured —
    absent in any normal deployment, the route does not exist at all.
    """
    if not settings.dev_login_available:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    snowflake_service = _get_snowflake_service(request)
    snowflake_user, role = await snowflake_service.resolve_keypair_identity()

    session_id = secrets.token_urlsafe(32)
    session_data = SessionData(
        session_id=session_id,
        snowflake_user=snowflake_user,
        role=role or settings.SNOWFLAKE_DEFAULT_ROLE or None,
        # No OAuth token to expire; bounded by the session TTL instead.
        token_expires_at=datetime.now(UTC) + timedelta(seconds=settings.SESSION_TTL_SECONDS),
        auth_mode="keypair",
    )
    await request.app.state.session_store.set(
        session_id, session_data, settings.SESSION_TTL_SECONDS
    )

    _set_session_cookie(response, session_id)
    return await _describe(session_data)


@router.get("/me", response_model=UserResponse)
async def me(session: SessionData = Depends(get_current_session)) -> UserResponse:
    return await _describe(session)


@router.get("/roles", response_model=RolesResponse)
async def list_roles(
    session: SessionData = Depends(get_current_session),
) -> RolesResponse:
    """Allowlist for the in-app role picker (from SNOWFLAKE_AVAILABLE_ROLES)."""
    return RolesResponse(
        roles=settings.available_roles(),
        current=session.role,
    )


@router.post("/role", response_model=UserResponse)
async def set_role(
    body: SetRoleRequest,
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> UserResponse:
    """Switch the session's active Snowflake role.

    The role must be on SNOWFLAKE_AVAILABLE_ROLES, and Snowflake must accept a
    connection under it for this user. On success the session is updated and
    any cached Snowflake connection for this session is dropped.
    """
    requested = body.role.strip().upper()
    if not requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role is required",
        )
    if not settings.is_available_role(requested):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{requested}' is not in SNOWFLAKE_AVAILABLE_ROLES",
        )

    snowflake_service = _get_snowflake_service(request)
    try:
        confirmed = await snowflake_service.verify_role(session, requested)
    except SnowflakeNotLinked:
        raise
    except Exception as exc:  # noqa: BLE001 - surface connector errors to the client
        log.warning(
            "Role switch rejected by Snowflake user=%s role=%s",
            session.snowflake_user,
            requested,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Snowflake refused role '{requested}'. "
                "Confirm the role is granted to your user."
            ),
        ) from exc

    # Re-read: checking the role may have given this session its Snowflake
    # token, and writing back the copy from before would throw that away.
    current = await request.app.state.session_store.get(session.session_id) or session
    active_role = confirmed.strip().upper() or requested
    updated = current.model_copy(update={"role": active_role})
    await request.app.state.session_store.set(
        session.session_id,
        updated,
        settings.SESSION_TTL_SECONDS,
    )
    connection_cache.evict(session.session_id)

    return await _describe(updated)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_id:
        await request.app.state.session_store.delete(session_id)
        connection_cache.evict(session_id)

    _clear_session_cookie(response)
