#!/usr/bin/env python3
"""Check a deployed stack's sign-in, accounts, rate limits and Snowflake rules.

Run this against the HOSTED DEV STACK before production (spec 0007). It talks to
the stack the way a browser does, through whatever sits in front of it (Traefik,
nginx), so it finds what unit tests cannot: whether the proxies pass the client
address, whether cookies are set securely, whether mail is configured.

Standard library only: run it from any machine that has Python 3.9+.

    # credentials come from the environment, never the command line
    $env:DEV_ADMIN_EMAIL    = "you@company.com"          # PowerShell
    $env:DEV_ADMIN_PASSWORD = "..."
    python verify_dev_stack.py --base-url https://one-dev.example.com

    export DEV_ADMIN_EMAIL=you@company.com               # bash
    export DEV_ADMIN_PASSWORD=...
    python verify_dev_stack.py --base-url https://one-dev.example.com

DEFAULT RUN (safe to repeat, needs an administrator account on that stack):
  - the stack answers, offers password sign-in and NO Snowflake sign-in;
  - cookies are HttpOnly / SameSite / Secure; a cross-site write is refused (403);
  - a throwaway account is made from an invitation (no inbox needed) and checked:
    not an administrator, can use everything that is not Snowflake, Connect
    Snowflake hands off and its callback refuses what is not a Connect request,
    changing a password signs its other sessions out, logout ends a session;
  - repeated wrong passwords end in 429 with Retry-After, the same for an
    unknown email as for a real one;
  - the throwaway accounts are disabled again at the end.
  It leaves disabled accounts behind (accounts cannot be deleted), named
  verify-<time>@<--test-domain>. It sends one invitation email per account made
  to --test-domain, which will not be delivered; pass --test-domain with a domain
  you control to avoid the bounce.

  If a previous run (or anyone) used up this machine's sign-in attempts, the run
  stops and tells you how many seconds to wait. Wait that long between runs.

OPTIONAL
  --email-address you@company.com   also tests real mail: the invitation arrives,
        forgot-password -> reset link (you paste it in) -> new password works ->
        the old session is signed out. Needs a terminal. It makes a real account
        for that address (disabled afterwards, never deleted), so use a NEW
        address each run: name+verify1@company.com, name+verify2@company.com, ...
  --probe-ip-exhaust   (machine A) tries 40 sign-ins as different people with
        forged X-Forwarded-For values. Expect a 429 around the per-IP limit: that
        shows the stack ignores forged client addresses. THIS LOCKS THIS
        MACHINE'S ADDRESS OUT OF SIGN-IN FOR THE WINDOW (15 minutes by default).
  --probe-ip-fresh     (machine B, a DIFFERENT network, right after) one
        sign-in attempt. Expect 401, not 429: a 429 means everybody shares one
        bucket (TRUSTED_PROXIES is wrong or empty).

RATE LIMITS APPLY TO THIS SCRIPT TOO. A full run uses most of this machine's
sign-in allowance (about 27 of 30 per 15 minutes by default), so run it ONCE per
15 minutes. If the stack limits you, the run stops and says how long to wait.

Exit status: 0 = nothing failed (warnings are listed), 1 = something failed,
2 = bad usage, 3 = inconclusive (cut short by a rate limit; wait and re-run).
Output never contains passwords or link tokens.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

USER_AGENT = "dev-stack-verify/1"
SECTIONS: list[str] = []


# --- reporting ------------------------------------------------------------------------

@dataclass
class Tally:
    passed: int = 0
    failed: int = 0
    warned: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


T = Tally()


def section(title: str) -> None:
    print(f"\n== {title}")


def check(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        T.passed += 1
        print(f"  PASS  {name}")
    else:
        T.failed += 1
        T.failures.append(name)
        print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))
    return bool(ok)


def warn(name: str, detail: str = "") -> None:
    T.warned += 1
    T.warnings.append(name)
    print(f"  WARN  {name}" + (f"\n          {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    T.skipped += 1
    print(f"  SKIP  {name}  ({why})")


def info(text: str) -> None:
    print(f"        {text}")


# --- a browser-like client -----------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None


@dataclass
class Resp:
    status: int
    headers: object
    text: str

    def json(self) -> dict:
        try:
            value = json.loads(self.text)
            return value if isinstance(value, dict) else {"_": value}
        except ValueError:
            return {}

    def detail(self) -> str:
        d = self.json().get("detail", "")
        return d if isinstance(d, str) else json.dumps(d)

    def cookies_set(self) -> list[str]:
        return self.headers.get_all("Set-Cookie") or []


class RateLimited(Exception):
    """A 429 where the script did not ask for one: the stack is limiting THIS
    machine (probably from an earlier run), which says nothing about whether
    the thing being checked works. The run stops as inconclusive."""

    def __init__(self, path: str, resp: "Resp") -> None:
        super().__init__(path)
        self.path, self.resp = path, resp


class Browser:
    """One browser: its own cookie jar, the Origin a page of the app would send,
    redirects NOT followed (so a hand-off to Snowflake can be inspected)."""

    def __init__(self, base: str, origin: str, timeout: float) -> None:
        self.base, self.origin, self.timeout = base.rstrip("/"), origin, timeout
        self.allow_429 = False          # only the checks ABOUT rate limits expect one
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )

    def request(self, method: str, path: str, body: object | None = None,
                headers: dict[str, str] | None = None) -> Resp:
        data = json.dumps(body).encode() if body is not None else None
        head = {"User-Agent": USER_AGENT, "Origin": self.origin, "Accept": "application/json"}
        if data is not None:
            head["Content-Type"] = "application/json"
        head.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=head, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                resp = Resp(r.status, r.headers, r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:      # 3xx (not followed), 4xx, 5xx
            resp = Resp(e.code, e.headers, e.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SystemExit(f"\nCannot reach {self.base}{path}: {e}") from e
        if resp.status == 429 and not self.allow_429:
            raise RateLimited(path, resp)
        return resp

    def get(self, path: str, **kw) -> Resp:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: object | None = None, **kw) -> Resp:
        return self.request("POST", path, body, **kw)

    def patch(self, path: str, body: object, **kw) -> Resp:
        return self.request("PATCH", path, body, **kw)


def token_of(link: str) -> str:
    """The token in a pasted link: the fragment, or (links sent before the
    fragment form) the last path segment, or the bare token itself."""
    link = link.strip()
    if "#" in link:
        return link.rsplit("#", 1)[1]
    if "/" in link:
        return link.rstrip("/").rsplit("/", 1)[1]
    return link


def wait_note(r: Resp) -> str:
    after = r.headers.get("Retry-After")
    return f"wait about {after} seconds and run again" if after else "wait 15 minutes and run again"


def new_password() -> str:
    return "Vfy-" + secrets.token_urlsafe(18)


# --- the checks ------------------------------------------------------------------------------

class Run:
    def __init__(self, args: argparse.Namespace) -> None:
        self.a = args
        self.base = args.base_url.rstrip("/")
        self.origin = (args.origin or self.base).rstrip("/")
        self.https = self.base.startswith("https://")
        self.tag = time.strftime("%Y%m%d%H%M%S")
        self.made: list[tuple[str, str]] = []          # (user_id, email) to disable at the end

    def browser(self) -> Browser:
        return Browser(self.base, self.origin, self.a.timeout)

    # ---- 1
    def reach_and_capabilities(self) -> dict | None:
        section("1. The stack answers, and offers password sign-in only")
        b = self.browser()
        h = b.get("/health")
        if not check("GET /health answers 200", h.status == 200, f"got {h.status}"):
            return None
        caps = b.get("/api/v1/auth/capabilities")
        c = caps.json()
        check("capabilities answers", caps.status == 200, f"got {caps.status}")
        check("password sign-in is offered", c.get("password") is True, str(c))
        check("password reset is offered", c.get("password_reset") is True, str(c))
        check("there is NO Snowflake sign-in method", "oauth" not in c, str(c))
        check("the old sign-in route is gone (GET /auth/login is 404)",
              b.get("/api/v1/auth/login").status == 404)
        info(f"sign-up is {'OPEN' if c.get('signup') else 'closed (invitation only)'} on this stack; "
             f"Connect Snowflake is {'configured' if c.get('snowflake_link') else 'NOT configured'}; "
             f"dev login is {'ON' if c.get('dev_login') else 'off'}")
        if c.get("dev_login"):
            warn("the developer sign-in is on", "it signs anyone in as a shared service account; "
                 "it must be off on any shared host (DEV_LOGIN_ENABLED=false)")
        return c

    # ---- 2
    def admin_session(self) -> Browser | None:
        section("2. Administrator sign-in, cookies, and the cross-site write check")
        email = self.a.admin_email
        password = os.environ.get("DEV_ADMIN_PASSWORD") or (
            getpass.getpass("Administrator password (not shown): ") if sys.stdin.isatty() else "")
        if not email or not password:
            print("  Set DEV_ADMIN_EMAIL and DEV_ADMIN_PASSWORD (or pass --admin-email and run in a terminal).")
            raise SystemExit(2)
        b = self.browser()
        r = b.post("/api/v1/auth/password", {"email": email, "password": password})
        if r.status == 403 and "confirm" in r.detail().lower():
            check("administrator can sign in", False, "that account's email is not confirmed yet")
            return None
        if not check("administrator can sign in", r.status == 200, f"got {r.status}: {r.detail()}"):
            return None
        cookies = [c for c in r.cookies_set() if c.lower().startswith("session_id=")]
        flags = cookies[0].lower() if cookies else ""
        check("the session cookie is set", bool(cookies))
        check("...HttpOnly", "httponly" in flags)
        check("...SameSite", "samesite=" in flags, flags)
        if self.https:
            check("...Secure (the site is https)", "secure" in flags)
        else:
            warn("the site is not https, so the Secure cookie flag was not checked")
        me = b.get("/api/v1/auth/me")
        check("GET /auth/me restores the session", me.status == 200 and me.json().get("email", "").lower()
              == email.lower(), f"got {me.status}")
        check("...and is an administrator", me.json().get("is_admin") is True)
        check("API answers are not cacheable (Cache-Control: no-store)",
              "no-store" in (me.headers.get("Cache-Control") or "").lower(),
              str(me.headers.get("Cache-Control")))

        evil = self.browser()
        evil.origin = "https://evil.example"
        evil.jar = b.jar
        evil.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(b.jar), _NoRedirect())
        check("a write from another site is refused (403)",
              evil.post("/api/v1/auth/logout").status == 403)
        check("...and the session is still alive", b.get("/api/v1/auth/me").status == 200)
        return b

    # ---- 3
    def accounts_report(self, admin: Browser) -> None:
        section("3. Accounts that cannot sign in with a password yet")
        r = admin.get("/api/v1/users")
        if not check("the Users list loads", r.status == 200, f"got {r.status}"):
            return
        users = r.json().get("users", [])
        orphans = [u for u in users if not u.get("has_password") and u.get("is_active")]
        info(f"{len(users)} accounts; {len(orphans)} active with no password")
        if orphans:
            warn(f"{len(orphans)} account(s) have no password",
                 "These were made by the Snowflake sign-in that no longer exists. Each needs "
                 "'Send sign-in link' on the Users page before its session expires:\n          "
                 + ", ".join(u.get("username", "?") for u in orphans[:15])
                 + (" ..." if len(orphans) > 15 else ""))
        else:
            check("no active account is without a password", True)

    # ---- 4
    def invite_and_accept(self, admin: Browser, address: str, label: str,
                          look_first: bool = True) -> tuple[Browser, str, str] | None:
        # look_first=False spends one fewer request from the stack's small
        # per-address allowance for confirm/reset/accept links (10 per 15 min).
        password = new_password()
        inv = admin.post("/api/v1/users/invitations", {"email": address, "is_admin": False})
        hint = ""
        if inv.status == 400 and "already exists" in inv.detail().lower():
            hint = (f"\n          An account for {address} already exists, probably from an earlier run "
                    "(throwaway accounts are disabled, not deleted). Use a different address each "
                    "time, e.g. name+verify2@yourdomain.")
        if not check(f"{label}: an administrator can create an invitation", inv.status == 200,
                     f"got {inv.status}: {inv.detail()}{hint}"):
            return None
        body = inv.json()
        link = body.get("link", "")
        check(f"{label}: the link keeps its token in the fragment (#), not the path", "#" in link)
        emailed = body.get("emailed")
        self.mail_configured = bool(emailed)
        token = token_of(link)
        b = self.browser()
        if look_first:
            seen = b.post("/api/v1/auth/invitations/check", {"token": token})
            check(f"{label}: the invitation names the address",
                  seen.status == 200 and seen.json().get("email", "").lower() == address.lower(),
                  f"got {seen.status}")
        done = b.post("/api/v1/auth/invitations/accept",
                      {"token": token, "password": password, "display_name": "Verify Script"})
        if not check(f"{label}: accepting it makes the account and signs in", done.status == 200,
                     f"got {done.status}: {done.detail()}"):
            return None
        check(f"{label}: the invitation works once",
              self.browser().post("/api/v1/auth/invitations/accept",
                                  {"token": token, "password": new_password()}).status in (400, 404))
        users = admin.get("/api/v1/users").json().get("users", [])
        mine = next((u for u in users if u.get("email", "").lower() == address.lower()), None)
        if mine:
            self.made.append((mine["user_id"], address))
        return b, password, address

    def throwaway_account(self, admin: Browser, caps: dict) -> tuple[Browser, str, str] | None:
        section("4. A throwaway account, made from an invitation")
        address = f"verify-{self.tag}@{self.a.test_domain}"
        made = self.invite_and_accept(admin, address, "account A")
        if not made:
            return None
        b, password, _ = made
        me = b.get("/api/v1/auth/me").json()
        check("a newly made account is not an administrator", me.get("is_admin") is False)
        check("its email is confirmed (the invitation proved it)", me.get("email_verified") is True)
        check("it has no Snowflake linked", me.get("snowflake_linked") is False)
        check("a non-administrator is refused the Users screen (403)",
              b.get("/api/v1/users").status == 403)
        if not getattr(self, "mail_configured", False):
            warn("outgoing mail does not look configured (the invitation was not emailed)",
                 "Without SMTP no one can confirm an email or reset a password. Set SMTP_HOST / SMTP_FROM.")
        else:
            info("the stack reports it sent the invitation email, so SMTP is configured")
        return b, password, address

    # ---- 5
    def without_snowflake(self, b: Browser) -> None:
        section("5. Someone who has not connected Snowflake can use everything else")
        for path in ("/api/v1/databases", "/api/v1/dashboards", "/api/v1/connections",
                     "/api/v1/excel-sources", "/api/v1/excel/reports",
                     "/api/v1/agent/conversations", "/api/v1/sources"):
            r = b.get(path)
            check(f"GET {path} is not an error", r.status == 200,
                  f"got {r.status} {r.detail()[:100]}  (409 here means a route still demands Snowflake)")

    # ---- 6
    def connect_snowflake(self, b: Browser, caps: dict) -> None:
        section("6. Connect Snowflake, and nothing else, uses the Snowflake callback")
        check("Connect Snowflake needs a signed-in account",
              self.browser().get("/api/v1/auth/snowflake/connect").status == 401)
        forged = self.browser().get("/api/v1/auth/callback?code=x&state=forged")
        check("a callback with a state nobody issued is refused (400)", forged.status == 400,
              f"got {forged.status}")
        if not caps.get("snowflake_link"):
            skip("Connect Snowflake hand-off and callback checks",
                 "OAuth is not configured on this stack (OAUTH_CLIENT_ID / SNOWFLAKE_ACCOUNT)")
            return
        r = b.get("/api/v1/auth/snowflake/connect")
        loc = r.headers.get("Location") or ""
        ok = r.status == 302 and "snowflakecomputing.com" in loc and "state=" in loc
        check("Connect Snowflake redirects to Snowflake's authorize page", ok,
              f"got {r.status} {urllib.parse.urlsplit(loc).netloc}")
        if not ok:
            return
        check("...the redirect URI is this stack's callback",
              urllib.parse.quote(f"{self.base}/api/v1/auth/callback", safe="") in loc
              or f"{self.base}/api/v1/auth/callback" in urllib.parse.unquote(loc),
              "OAUTH_REDIRECT_URI must be this stack's own /api/v1/auth/callback")
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query).get("state", [""])[0]
        stranger = self.browser()
        cb = stranger.get(f"/api/v1/auth/callback?code=not-a-real-code&state={state}")
        loc2 = cb.headers.get("Location") or ""
        check("the callback in ANOTHER browser links nothing and signs nobody in",
              cb.status == 302 and "/login" in loc2 and not any("session_id" in c.lower()
                                                               for c in cb.cookies_set()),
              f"got {cb.status} {loc2[:80]}")
        check("...that browser has no session", stranger.get("/api/v1/auth/me").status == 401)
        info("Nothing was sent to Snowflake: the callback refused before exchanging the code. "
             "Completing a real Connect Snowflake needs a person; see the manual list at the end.")

    # ---- 7
    def sessions(self, b: Browser, address: str, password: str) -> tuple[str, str]:
        section("7. Changing a password, and signing out")
        other = self.browser()
        s = other.post("/api/v1/auth/password", {"email": address, "password": password})
        if not check("a second session can sign in", s.status == 200,
                     f"got {s.status} {s.detail()}  {wait_note(s) if s.status == 429 else ''}"):
            return password, ""
        updated = new_password()
        ch = b.post("/api/v1/auth/account/password",
                    {"current_password": password, "new_password": updated})
        check("changing the password works", ch.status == 200, f"got {ch.status}: {ch.detail()}")
        if ch.status != 200:
            return password, ""
        check("...this session carries on", b.get("/api/v1/auth/me").status == 200)
        check("...the other session is signed out", other.get("/api/v1/auth/me").status == 401)
        bad = self.browser().post("/api/v1/auth/password", {"email": address, "password": password})
        check("...the old password no longer works", bad.status == 401, f"got {bad.status}")
        res = b.post("/api/v1/auth/logout")
        check("logout answers 204", res.status == 204, f"got {res.status}")
        check("...and the session is over", b.get("/api/v1/auth/me").status == 401)
        return updated, address

    # ---- 8
    def rate_limits(self, address: str) -> None:
        section("8. Repeated wrong passwords are limited, through the real proxies")
        browser = self.browser()
        browser.allow_429 = True        # this section is ABOUT the 429

        def until_limited(email: str) -> tuple[int, Resp | None]:
            for n in range(1, 41):
                r = browser.post("/api/v1/auth/password",
                                 {"email": email, "password": "wrong-" + secrets.token_hex(4)})
                if r.status == 429:
                    return n, r
                if r.status != 401:
                    check("sign-in answers 401 to a wrong password", False, f"got {r.status}")
                    return n, None
            return 40, None

        n, limited = until_limited(address)
        if not check("wrong passwords for one email end in 429", limited is not None,
                     "no 429 after 40 attempts: are limits off (RATE_LIMIT_ENABLED), or is Redis not "
                     "shared between workers (counts are per process)?"):
            return
        info(f"limited on attempt {n}")
        check("...with a Retry-After header", int(limited.headers.get("Retry-After") or 0) > 0)
        again = browser.post("/api/v1/auth/password", {"email": address, "password": "wrong-again"})
        check("...and it stays limited", again.status == 429, f"got {again.status}")
        unknown = f"nobody-{self.tag}@{self.a.test_domain}"
        n2, limited_unknown = until_limited(unknown)
        if limited_unknown is not None:
            check("an UNKNOWN email is limited the same way, with the same message",
                  limited_unknown.detail() == limited.detail(),
                  f"{limited.detail()!r} vs {limited_unknown.detail()!r}")
        else:
            warn("the unknown email was not limited within 40 attempts")
        info("This machine's address has now used part of its sign-in allowance; "
             "wait ~15 minutes before running this again.")

    # ---- 9 (opt-in, real mail)
    def real_mail(self, admin: Browser) -> None:
        section("9. Real email: invitation delivery, forgot password, reset")
        address = self.a.email_address
        # The prompts need a person at a terminal. (VERIFY_STDIN_OK lets a test
        # harness feed the answers through a pipe instead.)
        if not sys.stdin.isatty() and not os.environ.get("VERIFY_STDIN_OK"):
            skip("real-mail checks", "needs a terminal to paste links into")
            return
        made = self.invite_and_accept(admin, address, "account B", look_first=False)
        if not made:
            return
        b, password, _ = made
        arrived = input(f"\n  Did an invitation email arrive at {address}? [y/N] ").strip().lower()
        check("the invitation email was delivered", arrived.startswith("y"),
              "SMTP is not delivering: check SMTP_HOST/PORT/SECURITY/FROM and the backend log")
        before = self.browser()
        s = before.post("/api/v1/auth/password", {"email": address, "password": password})
        if s.status != 200:
            check("account B can sign in before the reset", False, f"got {s.status}")
            return
        f1 = self.browser().post("/api/v1/auth/password/forgot", {"email": address})
        f2 = self.browser().post("/api/v1/auth/password/forgot", {"email": f"nobody-{self.tag}@{self.a.test_domain}"})
        check("forgot-password answers the same for a real and an unknown email",
              f1.status == f2.status == 202 and f1.json() == f2.json(), f"{f1.status} {f2.status}")
        link = input(f"\n  A reset email was sent to {address}. Paste its link (or just the token): ").strip()
        token = token_of(link)
        if not token:
            skip("reset", "no link pasted")
            return
        check("the emailed reset link keeps its token in the fragment (#)", "#" in link or "/" not in link)
        fresh = self.browser()
        weak = fresh.post("/api/v1/auth/password/reset", {"token": token, "password": "password1234"})
        check("reset refuses a common password", weak.status == 400, f"got {weak.status}")
        chosen = new_password()
        done = fresh.post("/api/v1/auth/password/reset", {"token": token, "password": chosen})
        check("reset sets the password and signs this browser in", done.status == 200
              and fresh.get("/api/v1/auth/me").status == 200, f"got {done.status}: {done.detail()}")
        check("reset signs out the account's other sessions", before.get("/api/v1/auth/me").status == 401)
        check("a reset link works once",
              self.browser().post("/api/v1/auth/password/reset",
                                  {"token": token, "password": new_password()}).status == 400)
        check("the old password no longer works",
              self.browser().post("/api/v1/auth/password", {"email": address, "password": password}).status == 401)

    # ---- cleanup
    def cleanup(self, admin: Browser | None) -> None:
        section("Cleaning up")
        if admin is None or not self.made:
            info("nothing to clean up")
            return
        # a fresh sign-in is not needed; the admin session is still valid
        for user_id, email in self.made:
            r = admin.patch(f"/api/v1/users/{user_id}", {"is_active": False})
            if r.status == 200:
                info(f"disabled {email}")
            else:
                warn(f"could not disable {email}", f"got {r.status}; disable it on the Users page")


# --- the two address probes --------------------------------------------------------------------------

def probe_exhaust(args: argparse.Namespace) -> None:
    section("Probe A: use up this machine's per-address allowance, with forged X-Forwarded-For")
    run = Run(args)
    b = run.browser()
    b.allow_429 = True
    limited_at = None
    for n in range(1, 41):
        forged = f"203.0.113.{n}, 198.51.100.{n}"
        r = b.post("/api/v1/auth/password",
                   {"email": f"probe-{secrets.token_hex(5)}@{args.test_domain}", "password": "wrong-" + secrets.token_hex(3)},
                   headers={"X-Forwarded-For": forged})
        if r.status == 429:
            limited_at = (n, r)
            break
        if r.status not in (401,):
            check("sign-in answers 401 to a wrong password", False, f"got {r.status}")
            return
    if not check("the per-address limit is reached even though every request claims a different "
                 "client address", limited_at is not None,
                 "no 429 in 40 attempts. Either the limits are off (RATE_LIMIT_ENABLED), or something "
                 "believes the client's own X-Forwarded-For. Only the proxies' should be believed: "
                 "check TRUSTED_PROXIES, and that uvicorn runs with --no-proxy-headers (the production "
                 "Dockerfile does; a plain `uvicorn` trusts 127.0.0.1 by default and rewrites the address)."):
        return
    n, r = limited_at
    info(f"limited on attempt {n}; {wait_note(r)}")
    print("\n  This machine's address is now locked out of sign-in for the window.\n"
          "  NOW, from a machine on a DIFFERENT network, run:\n"
          f"      python verify_dev_stack.py --base-url {run.base} --probe-ip-fresh")


def probe_fresh(args: argparse.Namespace) -> None:
    section("Probe B: a different network must not share that allowance")
    run = Run(args)
    b = run.browser()
    b.allow_429 = True
    r = b.post("/api/v1/auth/password",
               {"email": f"probe-{secrets.token_hex(5)}@{args.test_domain}", "password": "wrong-" + secrets.token_hex(3)})
    if r.status == 429:
        check("a different network is NOT limited by the first one's attempts", False,
              "everyone is sharing one bucket: the backend sees the proxy's address for every user. "
              "Set TRUSTED_PROXIES to the proxies' Docker network (docker network inspect web).")
    else:
        check("a different network is NOT limited by the first one's attempts", r.status == 401,
              f"got {r.status}")


# --- entry point ----------------------------------------------------------------------------------------

MANUAL = """
What this script cannot do (do these by hand on the dev stack)
  [ ] Open the site in a browser: /login shows an email and password form and NO
      Snowflake button; "Forgot password?" is there; /signup asks for a full name
      (if sign-up is open).
  [ ] Connect Snowflake from the account dialog with a real Snowflake user; its
      queries run under THEIR role (GET /api/v1/auth/me shows it), and Disconnect
      leaves the rest of the app working.
  [ ] Sign in as an old Snowflake-made account's owner after an administrator
      clicked "Send sign-in link": same name in the sidebar, same dashboards and
      chats as before.
  [ ] A person with no Snowflake link opens a real SQL Server dashboard and an
      uploaded file, and can chat on both. Someone outside the group cannot.
  [ ] If DATA_GROUPS_ENFORCED is on there: the same, as a non-administrator.
"""


def main() -> int:
    p = argparse.ArgumentParser(description="Check a deployed dev stack (spec 0007).",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("DEFAULT RUN")[0].split("\n\n", 1)[-1])
    p.add_argument("--base-url", required=True, help="the stack's address, e.g. https://one-dev.example.com")
    p.add_argument("--origin", help="the Origin to send (default: --base-url). Needed only when "
                   "running against a backend directly, not through the site's own address.")
    p.add_argument("--admin-email", default=os.environ.get("DEV_ADMIN_EMAIL", ""),
                   help="an administrator on that stack (or DEV_ADMIN_EMAIL)")
    p.add_argument("--test-domain", default="example.invalid",
                   help="domain for the throwaway accounts' addresses (default example.invalid)")
    p.add_argument("--email-address", help="an inbox you control, to test real mail (needs a terminal)")
    p.add_argument("--skip-rate-limit", action="store_true", help="skip section 8")
    p.add_argument("--probe-ip-exhaust", action="store_true", help="probe A (locks this machine's address out)")
    p.add_argument("--probe-ip-fresh", action="store_true", help="probe B (run from a different network)")
    p.add_argument("--timeout", type=float, default=30.0)
    args = p.parse_args()

    print(f"Verifying {args.base_url}")
    inconclusive: RateLimited | None = None
    if args.probe_ip_exhaust:
        probe_exhaust(args)
    elif args.probe_ip_fresh:
        probe_fresh(args)
    else:
        run = Run(args)
        admin: Browser | None = None
        try:  # noqa: SIM105 - the except clause below reports an inconclusive run
            caps = run.reach_and_capabilities()
            if caps is not None:
                admin = run.admin_session()
            if admin is None:
                skip("everything that needs an administrator", "no administrator session")
            else:
                run.accounts_report(admin)
                made = run.throwaway_account(admin, caps)
                if made is None:
                    skip("checks on the throwaway account", "it could not be made")
                else:
                    b, password, address = made
                    run.without_snowflake(b)
                    run.connect_snowflake(b, caps)
                    updated, _ = run.sessions(b, address, password)
                    if args.skip_rate_limit:
                        skip("rate limiting", "--skip-rate-limit")
                    else:
                        run.rate_limits(address)
                if args.email_address:
                    run.real_mail(admin)
        except RateLimited as stop:
            inconclusive = stop
            print(f"\n  STOPPED  {stop.path} answered 429: this machine is being rate limited by the "
                  f"stack,\n           most likely by an earlier run. {wait_note(stop.resp)}.\n"
                  "           Nothing after this point was checked.")
        finally:
            run.cleanup(admin)
        if not inconclusive:
            print(MANUAL)

    section("Result")
    print(f"  {T.passed} passed, {T.failed} failed, {T.warned} warnings, {T.skipped} skipped")
    for name in T.failures:
        print(f"    FAILED: {name}")
    for name in T.warnings:
        print(f"    warning: {name}")
    if inconclusive:
        print("  INCONCLUSIVE: the run was cut short by a rate limit (exit status 3).")
        return 3
    return 1 if T.failed else 0


if __name__ == "__main__":
    sys.exit(main())
