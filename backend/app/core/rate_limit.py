"""Rate limits on the public sign-in routes.

Fixed windows, counted per route and client IP and, where the request names
one, per email. The per-email count is what stops guessing one person's
password from many addresses; the per-IP count is what stops one address
trying many people. Neither says whether an email has an account: a limited
request gets the same 429 either way.

WHERE THE COUNTS LIVE. Redis when sessions are in Redis, so the four
production workers share one count; otherwise process memory, which is only
right for local development (startup_checks warns). If Redis cannot be
reached the routes answer 503: an outage must not turn into unlimited
guessing.

WHO THE CLIENT IS. The address that connected, unless that address is one of
TRUSTED_PROXIES. Then X-Forwarded-For is read from the right, skipping
trusted proxies, and the first address that is not one is the client. Each
proxy appends the address it heard from, so the rightmost untrusted entry is
the last one a proxy we trust vouched for; anything to its left was written
by the client and is ignored.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import math
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.config import settings

log = logging.getLogger(__name__)

PREFIX = "ratelimit:"


@dataclass(frozen=True)
class Limit:
    name: str
    per_ip: int
    per_email: int | None
    window: int


def limits() -> dict[str, Limit]:
    """Read from settings each time, so a test or an operator can change them."""
    s = settings
    return {
        "sign_in": Limit("sign_in", s.RATE_LIMIT_SIGN_IN_PER_IP,
                         s.RATE_LIMIT_SIGN_IN_PER_EMAIL, s.RATE_LIMIT_SIGN_IN_WINDOW),
        "sign_up": Limit("sign_up", s.RATE_LIMIT_SIGN_UP_PER_IP, None,
                         s.RATE_LIMIT_SIGN_UP_WINDOW),
        "email": Limit("email", s.RATE_LIMIT_EMAIL_PER_IP, s.RATE_LIMIT_EMAIL_PER_EMAIL,
                       s.RATE_LIMIT_EMAIL_WINDOW),
        "token": Limit("token", s.RATE_LIMIT_TOKEN_PER_IP, None, s.RATE_LIMIT_TOKEN_WINDOW),
    }


# --- who is asking ------------------------------------------------------------------

def _networks() -> list[ipaddress._BaseNetwork]:
    out = []
    for entry in settings.TRUSTED_PROXIES:
        try:
            out.append(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            log.error("TRUSTED_PROXIES entry %r is not an IP or CIDR; ignored", entry)
    return out


def _trusted(address: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    return any(ip in network for network in networks)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    networks = _networks()
    if not networks or not _trusted(peer, networks):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _trusted(hop, networks):
            return hop
    # Every hop is a proxy of ours: the request started inside our network.
    return hops[0] if hops else peer


# --- counting -----------------------------------------------------------------------

class _MemoryCounter:
    def __init__(self) -> None:
        self._counts: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    async def hit(self, key: str, window: int) -> tuple[int, int]:
        now = time.monotonic()
        async with self._lock:
            count, ends = self._counts.get(key, (0, 0.0))
            if ends <= now:
                count, ends = 0, now + window
            count += 1
            self._counts[key] = (count, ends)
            if len(self._counts) > 50_000:       # forget windows that are over
                self._counts = {k: v for k, v in self._counts.items() if v[1] > now}
        return count, max(1, math.ceil(ends - now))

    def reset(self) -> None:
        self._counts.clear()


class _RedisCounter:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)

    async def hit(self, key: str, window: int) -> tuple[int, int]:
        # SET NX EX starts the window once; INCR counts in it. (EXPIRE NX would
        # do the same, but needs Redis 7.)
        pipe = self._redis.pipeline()
        pipe.set(key, 0, ex=window, nx=True)
        pipe.incr(key)
        pipe.ttl(key)
        _, count, ttl = await pipe.execute()
        return int(count), max(1, int(ttl) if ttl and int(ttl) > 0 else window)


_counter: _MemoryCounter | _RedisCounter | None = None


def _get_counter():
    global _counter
    if _counter is None:
        _counter = (_RedisCounter(settings.REDIS_URL) if settings.SESSION_BACKEND == "redis"
                    else _MemoryCounter())
    return _counter


def reset_for_tests(counter=None) -> None:
    global _counter
    _counter = counter


def _hash(value: str) -> str:
    # Emails are not kept in the counter store, only a digest of them.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _too_many(retry_after: int) -> HTTPException:
    minutes = max(1, math.ceil(retry_after / 60))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(retry_after)},
    )


async def enforce(request: Request, which: str, *, email: str | None = None) -> None:
    """Count this request against `which`, and refuse it with 429 if that
    goes over. Call it before the work, so a refused request costs nothing."""
    if not settings.RATE_LIMIT_ENABLED:
        return
    limit = limits()[which]
    keys = [(f"{PREFIX}{limit.name}:ip:{client_ip(request)}", limit.per_ip)]
    address = (email or "").strip().lower()
    if limit.per_email is not None and address:
        keys.append((f"{PREFIX}{limit.name}:email:{_hash(address)}", limit.per_email))
    counter = _get_counter()
    try:
        results = [(await counter.hit(key, limit.window), allowed) for key, allowed in keys]
    except Exception as exc:  # noqa: BLE001 - fail closed, never open
        log.error("Rate limit store unreachable; refusing %s: %s", which, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signing in is unavailable for a moment. Try again shortly.",
        ) from exc
    over = [retry for (count, retry), allowed in results if count > allowed]
    if over:
        raise _too_many(max(over))
