# Deployment notes

Written for whoever takes this from the repo to a server. It covers the things
that are **not** obvious from the code and that will break a deployment if
missed. Everything here was verified against the current tree, not assumed.

---

## 1. Redis is mandatory, not optional

`docker/backend.Dockerfile` starts the production target with:

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

Four worker processes. The default `SESSION_BACKEND=memory` keeps sessions in
one process's memory, so a session created by worker 2 does not exist for
workers 1, 3 and 4. Requests round-robin, so a signed-in user gets a 401 on
roughly three requests in four — login appears to succeed and then drops out at
random. It is not intermittent flakiness; it is the expected behaviour of that
combination.

Set `SESSION_BACKEND=redis` and provide `REDIS_URL`. The Redis store is already
implemented; `docker-compose.prod.yml` simply has no Redis service defined yet.

The same applies to `CONVERSATION_STORE` if the app ever runs as more than one
container: the file store is fine across workers on one host (they share a
filesystem) but not across hosts.

## 2. State that must survive a redeploy

`docker-compose.prod.yml` currently mounts no volumes, so everything the app
writes lives inside the container and is destroyed on every deploy:

| Path                       | Contents                               | Consequence if lost                                                                                                                                              |
| -------------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/config/metadata/` | published semantic layers              | reverts to whatever shipped in the image; admin edits gone                                                                                                       |
| `backend/excel_storage/`   | approved templates + generated reports | **saved reports break** — their metadata is in Snowflake and survives, but the template workbook does not, and runs fail with "stored template workbook missing" |
| `backend/.conversations/`  | chat history                           | users lose their threads                                                                                                                                         |

Mount volumes for all three, or move them to shared storage. `app/semantic/`
already has a `MetadataRepository` seam intended for S3; the Excel storage does
not have one yet.

## 3. Connecting Snowflake (OAuth)

People sign in with an app account (§4), not with Snowflake. OAuth is how a
signed-in person **connects** their own Snowflake user as a data source, so
their Snowflake queries run as them, under their own role, and their
row-access policies apply. Needed only where people will use Snowflake data.
The Snowflake side of it is in [SNOWFLAKE-SETUP.md](./SNOWFLAKE-SETUP.md).

> There is no "Sign in with Snowflake". It was removed (spec
> [0007](./specs/0007-sign-in-is-the-apps-own.md)); the callback below finishes
> only a **Connect Snowflake** started from a signed-in account, and refuses
> anything else before asking Snowflake for tokens. Leave `OAUTH_CLIENT_ID` and
> `OAUTH_CLIENT_SECRET` empty and the app runs without it.

Create a SECURITY INTEGRATION as `ACCOUNTADMIN`:

```sql
CREATE SECURITY INTEGRATION DATAALPHAONE_OAUTH
  TYPE = OAUTH
  ENABLED = TRUE
  OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  OAUTH_REDIRECT_URI = 'https://<host>/api/v1/auth/callback'
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  OAUTH_REFRESH_TOKEN_VALIDITY = 86400
  BLOCKED_ROLES_LIST = ('ACCOUNTADMIN','SECURITYADMIN')
  COMMENT = 'DataAlphaOne web app';

SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('DATAALPHAONE_OAUTH');
```

Then set `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` in the environment.

Four things that catch people out:

- **Primary vs alternate redirect URIs.** Snowflake custom OAuth has one
  `OAUTH_REDIRECT_URI` plus optional `OAUTH_ALTERNATE_REDIRECT_URIS`. Localhost,
  prod (`one.${DOMAIN}`), and hosted dev (`one-dev.${DOMAIN}`) can share one
  security integration if every callback is listed. Example:

  ```sql
  ALTER SECURITY INTEGRATION DATAALPHAONE_OAUTH SET
    OAUTH_ALTERNATE_REDIRECT_URIS = (
      'https://one-dev.<DOMAIN>/api/v1/auth/callback',
      'http://localhost:8000/api/v1/auth/callback'
    );
  ```

  Set each environment’s `OAUTH_REDIRECT_URI` to the URI that environment
  actually uses. Separate integrations are fine too, but not required when
  alternates are configured.
- **HTTPS is required.** Snowflake accepts `http://` only for `localhost`.
- **`COOKIE_SECURE=true`** wherever the app is served over https, or the browser
  discards the session cookie and login silently fails.
- **`BLOCKED_ROLES_LIST`.** Snowflake blocks `ACCOUNTADMIN` and `SECURITYADMIN`
  from OAuth by default. A user whose *default* role is one of those cannot
  connect, and the error does not say why.

If the Snowflake account has SAML SSO configured, the authorize step hands off
to that identity provider automatically, so people connect with their normal
company login.

## 4. App accounts and sign-in

Accounts live in the SQLite file at `APP_DB_PATH`. **It must be on a mounted
volume** (`dataalpha_app_data_prod` / `_dev`). Otherwise a redeploy deletes
every account, along with every dashboard, connection and data group in the
same file.

| Variable | What it does |
|---|---|
| `ADMIN_EMAILS` | Accounts with these emails are administrators, **once the email is confirmed**. How the first administrator of a new deployment is made: list your email, sign up or accept an invitation, confirm, and you are an administrator. Afterwards, make others administrators on the Users page and remove the entry if you like. Signing up never makes anyone an administrator by itself. |
| `SIGNUP_MODE` | `open`: anyone who can reach the site may create an account. `invite`: only through an administrator's invitation link. `closed`: no new password accounts. Default `invite`. |
| `EMAIL_VERIFICATION_REQUIRED` | Default `true`: a new account must open an emailed link before it can sign in, which proves it owns the address. `false` gives a plain sign-up and sign-in with no email step (local use, or a deployment with no mail). **With it off nothing proves an address, so `ADMIN_EMAILS` can no longer make an administrator** (anyone could sign up with that address); make the first one on the server with `pipenv run python scripts/make_admin.py you@company.com`. Keep it `true` anywhere people sign themselves up. The app warns at startup while it is off. |
| `PASSWORD_MIN_LENGTH` | Default 10. Passwords are also checked against a bundled list of common and breached passwords (NCSC's 100k list, from Have I Been Pwned; source and commit are in the file header) and may not contain the email's name. No outside service is called. |
| `PASSWORD_HASH_ALGORITHM` | `argon2id` (default) or `scrypt`. **Deploy first with `scrypt`**: the new code is live but still writes the old hash format, so the previous release can read every password while rolling back is possible. Switch to `argon2id` afterwards (see *Rolling this out*). Both are always verified. |
| `PASSWORD_HASH_TIME_COST`, `PASSWORD_HASH_MEMORY_KIB`, `PASSWORD_HASH_PARALLELISM` | Argon2id cost. Defaults 3, 64 MiB, 4 (RFC 9106's low-memory profile). Each worker needs the memory while hashing. Raising them upgrades every hash at its owner's next sign-in. |
| `PASSWORD_RESET_TTL_MINUTES` | How long a reset link works. Default 30. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` | Outgoing mail for confirmation, invitation and reset links. Any SMTP server works. **Prod's compose file did not pass these through until now.** |
| `EMAIL_LINKS_IN_LOG` | **Local development only.** Writes each email, link included, to the backend log so sign-up can be tried without a mail server. The app **refuses to start** with it on outside `DEBUG`, because anyone who can read the log could take over an account. |
| `EMAIL_VERIFICATION_TTL_HOURS`, `INVITATION_TTL_HOURS` | How long those links work. Defaults 24 and 72. |
| `RATE_LIMIT_*` | Attempts per window for sign-in, sign-up, resend/forgot, and confirm/reset/accept. Defaults are in `backend/.env.example`. |
| `TRUSTED_PROXIES` | See *Rate limiting and the client's address*. |

A new password account must confirm its email before it can sign in.
**Without SMTP, nothing is sent**: sign-up appears to work, and nobody can
ever confirm or reset a password.

### What stops the app starting

`app/core/startup_checks.py` runs at every start. Outside `DEBUG`, the backend
**refuses to start** (and says why in the log) when:

- `EMAIL_LINKS_IN_LOG` is on;
- `SIGNUP_MODE=open` and there is no `SMTP_HOST`/`SMTP_FROM`;
- `TOKEN_ENCRYPTION_KEY` is empty (each worker would invent its own);
- `FRONTEND_URL` is `https://` and `COOKIE_SECURE` is false.

It only **warns** when no administrator exists, when `SIGNUP_MODE=open`, when no
SMTP is set, when `SESSION_BACKEND` is not `redis`, and when `TRUSTED_PROXIES` is empty.

### Rate limiting and the client's address

Sign-in, sign-up, resend, forgot-password, reset and accept-invitation are
limited per client address and, where the request names one, per email. A
refused request is **429 with `Retry-After`**, the same for a real and an
unknown email. Counts live in **Redis** when `SESSION_BACKEND=redis`, so the
four workers share one count; if Redis cannot be reached these routes answer
**503** rather than allow unlimited guessing. In process memory (local
development) each worker would count alone.

**The client address.** Production is Traefik → nginx → uvicorn. The backend
believes an `X-Forwarded-For` only from an address in `TRUSTED_PROXIES`, and
then takes the rightmost entry that is not itself a trusted proxy (anything to
its left was written by the client). Set `TRUSTED_PROXIES` to the CIDR of the
Docker network the proxies are on (`docker network inspect web`). Left empty,
**every user shares the proxy's address and therefore one per-IP bucket.**
`docker/backend.Dockerfile` starts uvicorn with `--no-proxy-headers` so only
this setting decides. Traefik's own `ratelimit@docker` middleware stays as a
coarse outer limit.

**Not yet verified on a deployed stack.** The unit tests cover the address
logic and the limits themselves, and the local dev proxy returns 429. What
cannot be tested without the stack is whether the proxies pass the header and
whether two real clients get separate buckets. Check on the dev stack after
deploying: sign in wrongly from two networks and confirm each counts alone.

### Request origin

A `POST`, `PUT`, `PATCH` or `DELETE` under `/api` that carries an `Origin` (or,
without one, a `Referer`) naming a site other than `CORS_ORIGINS` and the origin
of `FRONTEND_URL` is refused with 403 before the route runs. Requests with
neither header (scripts, tests) pass: browsers always send `Origin` on
cross-site writes. `Host` and `X-Forwarded-*` are never consulted. **Every
origin the UI is served from must be in `CORS_ORIGINS` or be `FRONTEND_URL`**,
including a second dev port. `GET`s are not checked, so the Snowflake callback
(protected by its OAuth state) is unaffected.

### Emailed links

A link's one-time token is in the URL **fragment** (`/reset#...`), which
browsers never send to a server, so it is in no access log; the page posts it in
a request body and clears it from the address bar. Links emailed before this
change had the token in the path (`/invite/<token>`); those routes remain so an
invitation already sent still works.

### Signing out everywhere

Resetting or changing a password increments the account's `session_epoch`;
every session of that account carrying an older value is rejected on its next
request. The session that changed the password carries on.

## 4b. Rolling this out, and rolling it back

**Before the deploy**

1. Back up the `app_data` volume (`app.db`). The migration adds one column
   (`users.session_epoch`, default 0) and nothing else; it deletes and rewrites
   nothing, and an older backend ignores the column.
2. Make sure `ADMIN_EMAILS` and the SMTP settings are in the stack's environment
   (the compose files now pass them through), and set `TRUSTED_PROXIES`.
3. Keep `SIGNUP_MODE=invite` in production. Move it to `open` only after SMTP,
   rate limiting and reset have been checked **on the dev stack**.
4. Set `PASSWORD_HASH_ALGORITHM=scrypt` for the first deploy. The previous
   release cannot read Argon2id hashes, so until you are sure you will not go
   back, nothing may write one (see *Rolling back*). The backend logs a warning
   for as long as it is set.
5. Find the accounts that cannot sign in with a password: on the Users page,
   those whose "Signs in with" shows only *Snowflake connected*. Each was made
   by the sign-in that no longer exists.

**Deploy**

6. Deploy to the **dev stack** first and run the checks below there.
7. Deploying changes nothing for anyone already signed in: sessions stored
   before this change keep working (a missing `session_epoch` reads as 0, which
   is every account's starting value). Sessions begun through Snowflake keep
   working until they expire (`SESSION_TTL_SECONDS`, 24 hours by default).
   After that, **a Snowflake-only account cannot get back in** until step 8.
8. For each account from step 5, an administrator uses **Send sign-in link** on
   the Users page, with the person's real email. The link goes only to that
   person (the administrator never sees it); opening it confirms the email and
   lets them choose a password. It is the **same account**: same user id and
   username, so their dashboards, reports, chats, audit history, group
   memberships and Snowflake link are unchanged. People can also add an email
   and password themselves from the account dialog while their session lasts.
   The step can be repeated to fix a mistyped address; it is refused for an
   account that already has a password.

**Checks (dev stack)** — run
[`backend/scripts/verify_dev_stack.py`](../backend/scripts/verify_dev_stack.py).
It uses only the Python standard library, so it runs from any machine with
Python 3.9+, and it talks to the stack through Traefik and nginx exactly as a
browser does.

```powershell
$env:DEV_ADMIN_EMAIL    = "you@company.com"     # an administrator on the dev stack
$env:DEV_ADMIN_PASSWORD = "..."                 # or leave unset and it asks
python backend/scripts/verify_dev_stack.py --base-url https://one-dev.<DOMAIN>
```

It prints PASS / FAIL / WARN / SKIP per check and exits **0** (nothing failed),
**1** (something failed), **2** (bad usage) or **3** (inconclusive: cut short by
a rate limit). What it covers: capabilities offer password sign-in and no
Snowflake sign-in; `GET /auth/login` is 404; the session cookie is HttpOnly,
SameSite and (on https) Secure; a cross-site write is 403; a throwaway account
made from an invitation is not an administrator, can use everything that is not
Snowflake, can change its password (signing its other sessions out) and log out;
Connect Snowflake redirects to Snowflake and its callback refuses anything that
is not a Connect request; repeated wrong passwords end in 429 with `Retry-After`,
the same for an unknown email; and it lists accounts that still have no
password. It disables its throwaway accounts when done (accounts cannot be
deleted, so they remain, disabled, named `verify-<time>@example.invalid`; use
`--test-domain` to change the domain).

Three things to know before running it:

- **It is rate limited too.** A full run uses about 27 of this machine's 30
  sign-in attempts per 15 minutes. Run it **once per 15 minutes**; if the stack
  limits it, it stops, says how long to wait, and exits 3.
- **Email steps are opt-in** and need a terminal: add
  `--email-address you+verify1@company.com`. It then checks that the invitation
  really arrives, and walks forgot-password → you paste the emailed link →
  reset → the old session ends. Use a **new address each run** (the account it
  makes is disabled, not deleted).
- **Two probes check the client address through the real proxies**, and need
  two machines on different networks. On machine A run
  `--probe-ip-exhaust` (sends 40 sign-ins with forged `X-Forwarded-For`; it
  must reach a 429, proving forged addresses are ignored; this locks A out of
  sign-in for 15 minutes). Straight after, on machine B run
  `--probe-ip-fresh`: one attempt, which must be **401, not 429**. A 429 there
  means every user shares one bucket and `TRUSTED_PROXIES` is wrong.

The script ends with the checks it cannot do, to tick by hand: the sign-in page
in a browser (no Snowflake button, "Forgot password?" present), a real Connect
Snowflake with a real Snowflake user, an old Snowflake-made account's owner
signing in after "Send sign-in link", and opening a real SQL Server dashboard
and an uploaded file without Snowflake.

> **uvicorn must run with `--no-proxy-headers`.** `docker/backend.Dockerfile`
> does. A plain `uvicorn` believes `X-Forwarded-For` from 127.0.0.1 by default
> and rewrites the client address before the app sees it, so anyone could
> dodge the per-address limit by forging the header. `--probe-ip-exhaust`
> exists partly to catch this.
**Second stage: Argon2id**

Once the checks have passed and going back is no longer a worry (days, not
minutes), set `PASSWORD_HASH_ALGORITHM=argon2id` (or remove it) and redeploy.
Each password is upgraded at its owner's next successful sign-in; nobody is
asked to do anything. From here, rolling back to the previous release locks out
anyone whose hash was upgraded, and that release has no password reset to get
them back in. So the second stage is the point of no return for rollback, and
the reason for the first.

**Rolling back**

Redeploy the previous image tag. No data needs restoring: the new column is
ignored by the old code, and accounts, dashboards and history are untouched.
While `PASSWORD_HASH_ALGORITHM=scrypt` (the first stage) every password is
still in a format the previous release reads, so this is safe. What a rollback
gives up: password reset, rate limits, the Origin check, and (because the old
code has the sign-in) Snowflake sign-in comes back.

After the second stage it is not safe: anyone whose password was upgraded to
Argon2id cannot sign in on the previous release. The way out is to restore
`app.db` from the step 1 backup, which loses any account or change made since,
or to redeploy the new release with `PASSWORD_HASH_ALGORITHM=scrypt` and have
the affected people use *Forgot password* (which re-writes their hash in the
old format).

## 5. Secrets

`TOKEN_ENCRYPTION_KEY` **must be set explicitly in production.** When it is
empty, `app/main.py` generates a Fernet key at startup — which with 4 workers
means four different keys, and a refresh token encrypted by one worker cannot be
decrypted by another. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`JWT_SECRET` must likewise be set and stable across instances and restarts.

`.env` is gitignored (verified), as are `*.p8`, `*.pem`, `rsa_key*`, `*.key`,
`excel_storage/` and `.conversations/`. Only `.env.example` is committed.

## 6. Remove the development sign-in

`DEV_LOGIN_ENABLED=true` authenticates as a shared key-pair service account and
bypasses the web app's login entirely — anyone who can reach the port gets a
session. It requires `DEBUG` *and* the flag *and* the key material, so it cannot
enable itself accidentally, but it must be off in any deployed environment.

Password sign-in (§4) has replaced what it was a stand-in for. The code can be
deleted outright: the `/auth/dev-login` route, the key-pair branch in
`SnowflakeService`, and the config block in `app/core/config.py` (which carries
a comment marking all three).

## 7. Ports

The backend listens on **8000** inside its container. Local development uses
**8001** to avoid colliding with a legacy application on 8000 — hence
`OAUTH_REDIRECT_URI=http://localhost:8001/...` in the development example. A
deployed environment should use the public URL, and the container's internal
port is a detail of the compose file.

---

## 8. Hosted dev stack (parallel to prod)

Production stays on [docker-compose.prod.yml](../docker-compose.prod.yml)
(`one.${DOMAIN}`, ECR `:latest`). Hosted **dev** is a second Portainer stack
using [docker-compose.dev.yml](../docker-compose.dev.yml)
(`one-dev.${DOMAIN}`, ECR `:dev`). Same Traefik `web` network and `DOMAIN`
stack variable; unique Traefik router names (`one-dev` / `one-dev-api`).

Images use the **prod** Dockerfile targets (nginx + uvicorn workers), not local
`--reload` stages.

### Build and push `:dev` (does not overwrite `:latest`)

`VITE_SHOW_REPORT_BUILDER` is a **frontend build-time** flag (Vite inlines it).
Setting it in Portainer `environment:` does nothing — pass it when building,
then redeploy/pull the new `:dev` frontend image.

PowerShell:

```powershell
$env:VITE_SHOW_REPORT_BUILDER = "true"
docker compose -f docker-compose.yml build backend frontend

docker tag 629143592903.dkr.ecr.us-east-1.amazonaws.com/ai-reporting-be:latest 629143592903.dkr.ecr.us-east-1.amazonaws.com/ai-reporting-be:dev
docker tag 629143592903.dkr.ecr.us-east-1.amazonaws.com/ai-reporting-fe:latest 629143592903.dkr.ecr.us-east-1.amazonaws.com/ai-reporting-fe:dev

docker push 629143592903.dkr.ecr.us-east-1.amazonaws.com/ai-reporting-be:dev
docker push 629143592903.dkr.ecr.us-east-1.amazonaws.com/ai-reporting-fe:dev
```

Bash:

```bash
VITE_SHOW_REPORT_BUILDER=true docker compose -f docker-compose.yml build backend frontend
# then tag + push :dev as above
```

### Portainer

1. Create a **new** stack from `docker-compose.dev.yml` (do not replace the prod stack).
2. Set `DOMAIN` the same as prod (e.g. `dataalpha.in`).
3. Ensure containers join the external Docker network `web`.
4. Use a **separate** Environment from prod. Required differences:

| Variable                              | Dev value                                                   |
| ------------------------------------- | ----------------------------------------------------------- |
| `FRONTEND_URL` / `WEBAPP_BASE_URL`    | `https://one-dev.${DOMAIN}`                                 |
| `CORS_ORIGINS`                        | include that origin                                         |
| `OAUTH_REDIRECT_URI`                  | `https://one-dev.${DOMAIN}/api/v1/auth/callback`            |
| `JWT_SECRET` / `TOKEN_ENCRYPTION_KEY` | **new** values — do not reuse prod                          |
| `REDIS_URL`                           | separate Redis DB index (e.g. `/1`) or a dedicated instance |
| `DEV_LOGIN_ENABLED`                   | `false` on the shared host                                  |
| `ADMIN_EMAILS` / `SIGNUP_MODE`        | set per environment (§4); SMTP before `SIGNUP_MODE=open`    |
| `APP_DB_PATH` volume                  | `dataalpha_app_data_dev`, never the prod volume             |

### DNS and Snowflake

- Point `one-dev.${DOMAIN}` at the same Traefik host as prod (Let’s Encrypt via
  existing `certresolver=le`).
- Add the dev callback to `OAUTH_ALTERNATE_REDIRECT_URIS` (see §3).

### Verify after deploying

Run [ackend/scripts/verify_dev_stack.py](../backend/scripts/verify_dev_stack.py) first (see *Checks (dev stack)* above); the list below is what it does not cover.


- [ ] `https://one-dev.${DOMAIN}/` serves the UI; `/api` reaches the backend
- [ ] Password sign-in works; a new account receives its confirmation email
- [ ] Connect Snowflake completes with the dev redirect URI
- [ ] Prod `https://one.${DOMAIN}` still works with both stacks up
- [ ] Dev and prod sessions do not cross (separate JWT / encryption keys)