"""Refuse a request that changes something when it comes from another site.

The session is a cookie, and a browser attaches a cookie to a request whatever
page sent it. SameSite=lax already keeps it off cross-site POSTs, and FastAPI
will not read a form post as JSON; this is the check behind both, on the one
thing a forged request cannot hide: browsers state where a request came from.

For POST, PUT, PATCH and DELETE under /api, the Origin header (or, with none,
the origin of Referer) must be one the deployment lists: CORS_ORIGINS and the
origin of FRONTEND_URL. A different one is 403 before the route runs.

- A request with NEITHER header is let through. Browsers always send Origin on
  a cross-site request that changes things, so only a script, a test or a
  non-browser client sends none, and a website cannot make a visitor's browser
  do that.
- `Origin: null` (a sandboxed frame, a file:// page) is not an origin we list.
- Host and X-Forwarded-* are never consulted: anyone can send them.
- GET is not checked. The Snowflake callback is a GET redirected back from
  Snowflake, and it is protected by its OAuth state, which is unchanged.

Plain ASGI for the same reason as no_store.py.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

CHANGES = {"POST", "PUT", "PATCH", "DELETE"}


def origin_of(url: str) -> str | None:
    """scheme://host[:port], lower-cased, or None if `url` is not one."""
    parts = urlsplit((url or "").strip())
    if not parts.scheme or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme.lower()}://{parts.hostname.lower()}{port}"


def allowed_origins(cors_origins: list[str], frontend_url: str) -> frozenset[str]:
    found = {origin_of(o) for o in [*cors_origins, frontend_url]}
    return frozenset(o for o in found if o)


class OriginGuard:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]],
                 allowed: Callable[[], frozenset[str]], prefix: str = "/api/") -> None:
        self.app = app
        self.allowed = allowed          # read per request, so settings changes apply
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope.get("type") != "http" or scope.get("method") not in CHANGES
                or not scope.get("path", "").startswith(self.prefix)):
            await self.app(scope, receive, send)
            return

        headers = {name.lower(): value for name, value in scope.get("headers") or []}
        claimed = headers.get(b"origin")
        if claimed is None and headers.get(b"referer"):
            claimed = headers[b"referer"]
        if claimed is None:
            await self.app(scope, receive, send)
            return

        origin = origin_of(claimed.decode("latin-1"))
        if origin is not None and origin in self.allowed():
            await self.app(scope, receive, send)
            return

        body = json.dumps({"detail": "This request came from a site that is not allowed."}).encode()
        await send({"type": "http.response.start", "status": 403, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": body})
