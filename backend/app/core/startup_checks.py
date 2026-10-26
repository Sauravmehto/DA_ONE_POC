"""What a deployment is about to allow, checked once at startup.

Errors stop the app: each is a setting that leaks something or quietly breaks
sign-in, and finding out at someone's first sign-in is too late. Warnings are
said out loud and the app starts.

Outside DEBUG only, for the errors. Local development runs with DEBUG=true
and is allowed the shortcuts (links in the log, no SMTP) that make sign-up
possible to try.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings


@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check(settings: Settings, *, any_administrator: bool) -> Findings:
    found = Findings()
    deployed = not settings.DEBUG

    if not any_administrator:
        found.warnings.append(
            "No administrator exists. Set ADMIN_EMAILS to the email of the person "
            "who should administer this deployment."
        )

    if settings.EMAIL_LINKS_IN_LOG:
        message = ("EMAIL_LINKS_IN_LOG is on: confirmation and reset links are written "
                   "to the log, so anyone who can read it can take over an account.")
        (found.errors if deployed else found.warnings).append(
            message + (" Turn it off outside local development." if deployed else "")
        )

    mail = bool(settings.SMTP_HOST and settings.SMTP_FROM)
    if settings.SIGNUP_MODE == "open":
        if deployed and not mail:
            found.errors.append(
                "SIGNUP_MODE=open needs SMTP_HOST and SMTP_FROM: a new account must "
                "confirm its email, and without mail nobody ever can."
            )
        elif deployed:
            found.warnings.append(
                "SIGNUP_MODE=open: anyone who can reach this site can create an account."
            )
    if not mail and not settings.EMAIL_LINKS_IN_LOG:
        found.warnings.append(
            "No SMTP_HOST: confirmation, invitation and password-reset emails are not sent."
        )

    if deployed and not settings.TOKEN_ENCRYPTION_KEY:
        found.errors.append(
            "TOKEN_ENCRYPTION_KEY is not set. Each worker would invent its own key, so "
            "saved Snowflake links and SQL Server passwords could not be read back."
        )
    elif not settings.TOKEN_ENCRYPTION_KEY:
        found.warnings.append(
            "TOKEN_ENCRYPTION_KEY is not set: linked Snowflake users will have to "
            "connect again after every restart."
        )

    if deployed and settings.FRONTEND_URL.lower().startswith("https://") \
            and not settings.COOKIE_SECURE:
        found.errors.append(
            "FRONTEND_URL is https but COOKIE_SECURE is false: the session cookie "
            "would also be sent over plain http. Set COOKIE_SECURE=true."
        )

    if deployed and settings.SESSION_BACKEND != "redis":
        found.warnings.append(
            "SESSION_BACKEND is not redis: sessions and sign-in rate limits are kept "
            "per process, which is wrong with more than one worker."
        )

    if deployed and settings.RATE_LIMIT_ENABLED and not settings.TRUSTED_PROXIES:
        found.warnings.append(
            "TRUSTED_PROXIES is empty. Behind a reverse proxy (Traefik, nginx) every request "
            "then appears to come from the proxy, and the per-IP sign-in limits become one "
            "shared bucket for everybody. Set it to the proxies' Docker network (CIDR). "
            "Ignore this only when the app is exposed directly."
        )

    if not settings.EMAIL_VERIFICATION_REQUIRED:
        found.warnings.append(
            "EMAIL_VERIFICATION_REQUIRED is off: nobody proves they own the address they sign "
            "up with, so ADMIN_EMAILS no longer makes an administrator by itself (make one "
            "with scripts/make_admin.py). Fine for local use; turn it on for a real deployment."
        )

    if settings.PASSWORD_HASH_ALGORITHM == "scrypt":
        found.warnings.append(
            "PASSWORD_HASH_ALGORITHM=scrypt: new passwords use the older scheme so the "
            "previous release can still read them. Set it to argon2id once rolling back "
            "is no longer a concern."
        )

    if settings.DEV_LOGIN_ENABLED and deployed:
        found.warnings.append(
            "DEV_LOGIN_ENABLED is set without DEBUG; the dev login stays off."
        )
    return found
