# Backend

FastAPI API server for AiReporting. People sign in with an email and password;
Snowflake is a data source they can connect once signed in.

## Prerequisites

- [Python 3.12](https://www.python.org/downloads/)
- [pipenv](https://pipenv.pypa.io/en/latest/installation.html)
- Redis (production only — local dev uses in-memory sessions by default)
- Optional: a Snowflake OAuth security integration, only if people will connect
  Snowflake as a data source (see below)

## Install dependencies

From the `backend/` directory:

```sh
pipenv install
```

## Run it locally

```sh
cp .env.example .env
```

Then set, in `.env`, what a local run needs (the app refuses to start on a real
deployment without the production settings, but `DEBUG=true` is local
development):

```sh
DEBUG=true
SIGNUP_MODE=open              # so you can register
ADMIN_EMAILS=you@example.com  # you become the administrator once your email is confirmed
EMAIL_LINKS_IN_LOG=true       # confirmation/reset links are written to the backend log
TOKEN_ENCRYPTION_KEY=...      # see "Generate a key" below
```

```sh
pipenv run uvicorn app.main:app --reload
```

The API runs at [http://localhost:8000](http://localhost:8000). Register at the
frontend's `/signup`, then copy the confirmation link from the backend log.

- Health check: `GET /health`
- Sign in: `POST /api/v1/auth/password`; session: `GET /api/v1/auth/me`; sign out:
  `POST /api/v1/auth/logout`
- Accounts: `POST /auth/signup`, `POST /auth/verify`, `POST /auth/password/forgot`,
  `POST /auth/password/reset`, invitations under `/auth/invitations/`
- Connect Snowflake: `GET /api/v1/auth/snowflake/connect` (needs a session),
  returning through `GET /api/v1/auth/callback`

Account records live in a SQLite file (`APP_DB_PATH`, default `data/app.db`).
Back it up, and on a server keep it on a mounted volume.

## Connecting Snowflake (optional, for Snowflake data)

Not needed to sign in or to use SQL Server connections and uploads. If people
should be able to press **Connect Snowflake**, a Snowflake admin creates a
custom OAuth security integration. Everything about it — the SQL, roles,
redirect URIs, and what the app does and does not do with Snowflake roles — is in
[docs/SNOWFLAKE-SETUP.md](../docs/SNOWFLAKE-SETUP.md) and
[docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md#3-connecting-snowflake-oauth).

**The callback must reach the backend.** `OAUTH_REDIRECT_URI` is where Snowflake
sends the browser back, so it is the backend's URL (or whatever proxy fronts it),
not the Vite dev server's.

## Environment variables

Every setting is documented in `.env.example`. The ones that matter most:

| Variable | Local dev | Production |
|----------|-----------|------------|
| `DEBUG` | `true` | `false` |
| `SESSION_BACKEND` | `memory` | `redis` (also holds the rate-limit counts) |
| `REDIS_URL` | unused | `redis://...` |
| `SIGNUP_MODE` | `open` | `invite` until SMTP, rate limits and reset are verified |
| `ADMIN_EMAILS` | your email | the first administrator's email |
| `SMTP_HOST`, `SMTP_FROM`, ... | optional | required for confirmation and reset emails |
| `EMAIL_LINKS_IN_LOG` | `true` | **must be `false`** (the app will not start) |
| `TOKEN_ENCRYPTION_KEY` | Fernet key | strong random key (the app will not start without) |
| `JWT_SECRET` | any dev value | strong random secret |
| `COOKIE_SECURE` | `false` | `true` (the app will not start with an https `FRONTEND_URL` otherwise) |
| `TRUSTED_PROXIES` | empty | the proxies' Docker network (CIDR) |
| `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_REDIRECT_URI`, `SNOWFLAKE_ACCOUNT` | only to try Connect Snowflake | only if people connect Snowflake |
| `FRONTEND_URL` | `http://localhost:5173` | `https://app...` |

Generate a key:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Session storage

Sessions are stored via a pluggable backend controlled by `SESSION_BACKEND`:

- `memory` — in-process dict (default for local development)
- `redis` — Redis-backed store for production multi-worker deployments

Switch by setting `SESSION_BACKEND=redis` and `REDIS_URL` in production `.env`.

## Other commands

```sh
pipenv run pytest          # run tests
pipenv install <package>   # add a dependency
```

Tests set their own fake values (`tests/conftest.py`), so a local `.env` cannot
change a result and nothing can reach a real Snowflake account.