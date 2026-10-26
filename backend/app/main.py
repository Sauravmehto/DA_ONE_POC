import logging
from contextlib import asynccontextmanager

import snowflake.connector.errors as snowflake_errors
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routes import router as api_router
from app.conversation import get_conversation_store
from app.core.config import settings
from app.core.no_store import NoStoreAPIResponses
from app.core.origin_guard import OriginGuard, allowed_origins
from app.core.security import TokenEncryptor
from app.engine.connectors.snowflake import client as engine_client
from app.services.connection_cache import connection_cache
from app.services.snowflake import SnowflakeNotLinked, is_session_gone
from app.session import get_session_store


log = logging.getLogger(__name__)


def _check_deployment() -> None:
    """Open the account file now, so a bad APP_DB_PATH stops the app at start
    rather than at someone's first sign-in, and say out loud what a
    deployment is about to allow. A setting that leaks or breaks sign-in
    stops the app (see app/core/startup_checks.py)."""
    from app.accounts import service as accounts
    from app.core import startup_checks

    found = startup_checks.check(settings, any_administrator=accounts.any_administrator())
    for warning in found.warnings:
        log.warning(warning)
    if found.errors:
        for error in found.errors:
            log.error(error)
        raise RuntimeError("Refusing to start: " + " ".join(found.errors))


@asynccontextmanager
async def lifespan(app: FastAPI):
    encryption_key = settings.TOKEN_ENCRYPTION_KEY
    if not encryption_key:
        encryption_key = Fernet.generate_key().decode()

    _check_deployment()

    # Every Snowflake query the web app makes runs as the signed-in person, on
    # their own connection, or not at all. The engine's shared service-account
    # connection is for the standalone engine and scripts; here, reaching for
    # it raises instead (spec 0007).
    engine_client.refuse_shared_connection()

    app.state.session_store = get_session_store(settings)
    app.state.conversation_store = get_conversation_store(settings)
    app.state.token_encryptor = TokenEncryptor(encryption_key)

    try:
        yield
    finally:
        engine_client.allow_shared_connection()
        await app.state.session_store.close()
        await app.state.conversation_store.close()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(NoStoreAPIResponses)
# Added after CORS so it runs first: a cross-site request that changes
# something is refused before CORS or any route sees it.
app.add_middleware(
    OriginGuard,
    allowed=lambda: allowed_origins(settings.CORS_ORIGINS, settings.FRONTEND_URL),
)

app.include_router(api_router, prefix="/api/v1")


@app.exception_handler(SnowflakeNotLinked)
async def snowflake_not_linked(_request: Request, exc: SnowflakeNotLinked) -> JSONResponse:
    """Anything that needs Snowflake, asked by someone who has not connected
    it. A distinct code lets the app offer the connect button instead of an
    error."""
    return JSONResponse(
        status_code=409,
        content={"detail": exc.message, "code": "snowflake_not_linked"},
    )


@app.exception_handler(engine_client.WarehouseNotBound)
async def warehouse_not_bound(_request: Request, exc: engine_client.WarehouseNotBound
                              ) -> JSONResponse:
    """Something asked for Snowflake in a context that has none: the person
    has not connected it. Answered like any other "connect Snowflake" case,
    and never by falling back to a shared identity."""
    return JSONResponse(
        status_code=409,
        content={"detail": "Connect your Snowflake account to use Snowflake data.",
                 "code": "snowflake_not_linked"},
    )


@app.exception_handler(snowflake_errors.Error)
async def snowflake_error(request: Request, exc: snowflake_errors.Error) -> JSONResponse:
    """A Snowflake error that no route handled.

    When it means the session behind this person's connection has ended, the
    dead connection is dropped so the next request opens a fresh one, and the
    answer says so with a code the app can act on. Anything else is still a
    server error, but one that says what Snowflake said rather than a bare
    "Internal Server Error"."""
    if is_session_gone(exc):
        session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if session_id:
            connection_cache.evict(session_id)
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Your Snowflake session had ended and has been reopened. Try again.",
                "code": "snowflake_session_expired",
            },
        )
    log.warning("Unhandled Snowflake error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": f"Snowflake reported an error: {exc}"})


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.VERSION}
