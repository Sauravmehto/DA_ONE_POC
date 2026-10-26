"""Outgoing email: verification and invitation links, over SMTP.

Sent on a background thread. A mail server that is slow or down must not hold
up the request that asked for the email, and a failure is logged rather than
shown: the person can ask for the link again.

Without SMTP_HOST nothing is sent. With EMAIL_LINKS_IN_LOG (local development
only) the message, link included, is written to the log so sign-up can be
tried; otherwise only the fact that it was not sent is logged, because a
verification link in a log file lets anyone who can read the log confirm an
address that is not theirs.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

from app.core.config import settings

log = logging.getLogger(__name__)

PRODUCT = "DataAlpha One"


def configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


def _deliver(message: EmailMessage) -> None:
    try:
        if settings.SMTP_SECURITY == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=20,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
        with client:
            if settings.SMTP_SECURITY == "starttls":
                client.starttls(context=ssl.create_default_context())
            if settings.SMTP_USERNAME:
                client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            client.send_message(message)
    except Exception:  # noqa: BLE001 - logged; the person can ask again
        log.warning("Could not send email to %s", message["To"], exc_info=True)


def send(to: str, subject: str, body: str) -> None:
    """Send a plain-text email, or say in the log why it was not sent."""
    if not configured():
        if settings.EMAIL_LINKS_IN_LOG:
            log.warning("SMTP is not set up, so this email was not sent.\nTo: %s\n"
                        "Subject: %s\n\n%s", to, subject, body)
        else:
            log.warning("SMTP is not set up: an email to %s was not sent (%s).", to, subject)
        return
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    threading.Thread(target=_deliver, args=(message,), daemon=True).start()


def verification_email(to: str, link: str) -> None:
    hours = settings.EMAIL_VERIFICATION_TTL_HOURS
    send(to, f"Confirm your email for {PRODUCT}", (
        f"Confirm this is your email address to finish setting up {PRODUCT}:\n\n"
        f"{link}\n\n"
        f"The link works once and expires in {hours} hours. If you did not ask "
        "for this, ignore this email and nothing will happen."
    ))


def reset_email(to: str, link: str) -> None:
    minutes = settings.PASSWORD_RESET_TTL_MINUTES
    send(to, f"Reset your {PRODUCT} password", (
        f"Someone asked to reset the password for this email on {PRODUCT}. "
        "Choose a new one here:\n\n"
        f"{link}\n\n"
        f"The link works once and expires in {minutes} minutes. Setting a new "
        "password signs you out everywhere else. If you did not ask for this, "
        "ignore this email: your password has not changed."
    ))


def sign_in_setup_email(to: str, link: str) -> None:
    hours = settings.INVITATION_TTL_HOURS
    send(to, f"Set a password for {PRODUCT}", (
        f"{PRODUCT} no longer signs people in through Snowflake. Your account, "
        "with everything in it, is unchanged; choose a password to keep using "
        "it, and sign in with this email from now on:\n\n"
        f"{link}\n\n"
        f"The link works once and expires in {hours} hours. You can still "
        "connect Snowflake as a data source once you are signed in."
    ))


def invitation_email(to: str, link: str, invited_by: str | None) -> None:
    hours = settings.INVITATION_TTL_HOURS
    who = f"{invited_by} has invited you" if invited_by else "You have been invited"
    send(to, f"You're invited to {PRODUCT}", (
        f"{who} to {PRODUCT}. Choose a password to finish:\n\n"
        f"{link}\n\n"
        f"The link works once and expires in {hours} hours."
    ))
