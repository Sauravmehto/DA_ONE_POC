"""Tell browsers and proxies never to keep an API response.

Every answer from /api is about one person at one moment: who they are, what
they may see, the rows they asked for. Without a Cache-Control header a browser
is free to guess, and a proxy between the user and the app is free to keep a
copy. Either can hand back "who am I" from before the session ended, and the
app then opens as if signed in and fails on its next request.

Plain ASGI rather than @app.middleware("http"): that wrapper consumes the
request stream, which stops a route from noticing that the browser has gone —
the Stop button on a streamed model call depends on exactly that.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class NoStoreAPIResponses:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]],
                 prefix: str = "/api/") -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not scope.get("path", "").startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                # A route that chose its own policy (a download, a stream) keeps it.
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)
