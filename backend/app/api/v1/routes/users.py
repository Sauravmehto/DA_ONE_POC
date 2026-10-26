"""Accounts, for administrators: who has one, invitations, and who is an admin.

An invitation is emailed when SMTP is set up, and its link is always returned
too, so the administrator can pass it on however they like.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.accounts import mailer
from app.accounts import service as accounts
from app.accounts import trial
from app.api.deps import require_admin
from app.api.v1.routes.auth import email_link
from app.core.config import settings
from app.session.base import SessionData

router = APIRouter(prefix="/users", tags=["users"])


class InviteRequest(BaseModel):
    email: str = ""
    is_admin: bool = False


class UpdateAccount(BaseModel):
    is_admin: bool | None = None
    is_active: bool | None = None
    display_name: str | None = None


def _error(exc: accounts.AccountError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("")
async def list_users(_: SessionData = Depends(require_admin)) -> dict:
    people = await asyncio.to_thread(accounts.list_accounts)
    invitations = await asyncio.to_thread(accounts.pending_invitations)
    return {
        "users": [p.public() for p in people],
        "invitations": [i.public() for i in invitations],
        "signup_mode": settings.SIGNUP_MODE,
    }


@router.post("/invitations")
async def invite(body: InviteRequest, session: SessionData = Depends(require_admin)) -> dict:
    try:
        invitation, token = await asyncio.to_thread(
            lambda: accounts.create_invitation(
                body.email, is_admin=body.is_admin, created_by=session.snowflake_user
            )
        )
    except accounts.AccountError as exc:
        raise _error(exc) from exc
    link = email_link("invite", token)
    mailer.invitation_email(invitation.email, link, session.snowflake_user)
    return {"invitation": invitation.public(), "link": link, "emailed": mailer.configured()}


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(invitation_id: str, _: SessionData = Depends(require_admin)) -> None:
    await asyncio.to_thread(accounts.revoke_invitation, invitation_id)


class SignInSetup(BaseModel):
    email: str = ""


@router.post("/{user_id}/sign-in-setup")
async def sign_in_setup(user_id: str, body: SignInSetup,
                        _: SessionData = Depends(require_admin)) -> dict:
    """Give an account that cannot sign in with a password (one made by the
    Snowflake sign-in this app no longer has) an email and a link to choose a
    password, keeping the same account and everything it owns.

    The link is only ever EMAILED, never returned here: handing it to the
    administrator would let them choose the password for someone else's
    account. So it needs mail to be set up."""
    if not (mailer.configured() or settings.EMAIL_LINKS_IN_LOG):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set up outgoing email (SMTP) first: the link goes only to the person.",
        )
    try:
        account, token = await asyncio.to_thread(accounts.start_sign_in_setup, user_id,
                                                 body.email)
    except accounts.AccountError as exc:
        raise _error(exc) from exc
    mailer.sign_in_setup_email(account.email or "", email_link("reset", token))
    return {"user": account.public(), "emailed": True}


@router.patch("/{user_id}")
async def update(user_id: str, body: UpdateAccount,
                 session: SessionData = Depends(require_admin)) -> dict:
    try:
        account = await asyncio.to_thread(
            lambda: accounts.update_account(
                user_id, actor_user_id=session.user_id, is_admin=body.is_admin,
                is_active=body.is_active, display_name=body.display_name,
            )
        )
    except accounts.AccountError as exc:
        raise _error(exc) from exc
    return account.public()


class ExtendTrial(BaseModel):
    days: int = 14


@router.post("/{user_id}/trial/extend")
async def extend_trial(user_id: str, body: ExtendTrial,
                       session: SessionData = Depends(require_admin)) -> dict:
    """Give one person's trial more time, counted from now if it has ended."""
    if not 1 <= body.days <= 365:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Choose between 1 and 365 days.")
    account = await asyncio.to_thread(accounts.get_account, user_id)
    if account is None or not account.email:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such account.")
    current = await asyncio.to_thread(
        trial.extend, account.email, body.days, session.snowflake_user
    )
    if current is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That account is not on a trial.")
    return {"user": account.public()}
