# 0007 — Sign-in is the app's own; Snowflake is only a connection

**Status:** built (2026-10-23), not deployed. Amended after the owner chose to
REMOVE Snowflake sign-in rather than switch it off behind a setting; see *Notes*.
**Touches:** sign-in, sign-up, password reset, sessions, the data-group access
check, Snowflake linking, the login and account pages, the Users admin page,
configuration and deployment docs

## Why

A person should be able to create an account and use DataAlphaOne with an
email and password, and never need a Snowflake login to get in. Most of that
already exists: app accounts, password sign-in, sign-up with email
verification, invitations and linking Snowflake to an account were all built
before this spec. What is missing is what stops it being the *only* way in:

- **Snowflake sign-in cannot be turned off without also turning off Connect
  Snowflake.** Both ride on the same `OAUTH_CLIENT_ID`. A deployment that
  configures OAuth so people can link Snowflake as a data source also shows
  "Sign in with Snowflake", and any Snowflake user who completes it gets an
  account made for them.
- **A password user without Snowflake can be refused things that have nothing
  to do with Snowflake.** With `DATA_GROUPS_ENFORCED=true`, every data-group
  check on a dashboard, report, upload or chat runs through `run_as_user`
  (`app/api/access.py`), which always opens the person's Snowflake connection
  first. A non-administrator who has never linked Snowflake is answered 409
  "Connect your Snowflake account" when opening a SQL Server dashboard or an
  upload. Found by reading the code, not yet reproduced; the first acceptance
  check reproduces it.
- **The rest of password sign-in is not strong enough to be the only way in.**
  There is no password reset, no rate limiting beyond a per-account lock (which
  anyone can trigger for a known email with five guesses), no Origin check on
  requests that change things, scrypt at a cost below current guidance, and no
  way to sign a person out everywhere when their password changes.
- **Accounts made by Snowflake sign-in have no email and no password.** Turning
  Snowflake sign-in off would lock them out, unless they are given another way
  in that keeps the same account.

## Not in scope

- **A shared or service Snowflake identity.** Querying Snowflake stays per
  person, through the account's own linked OAuth token and that person's own
  role. Ruled out, not deferred.
- **Administrator-managed Snowflake connections** (one connection many people
  use, as SQL Server works today). A real option with real trade-offs. It would
  replace Snowflake's per-person grants with data groups, and it needs a new
  rule for who may write a Snowflake semantic layer. Its own spec, if wanted.
- **Who may write a Snowflake semantic layer.** Still a linked session whose
  active Snowflake role is in `ADMIN_ROLES` (DECISIONS, 2026-09-23). An app
  administrator without a linked Snowflake account can manage users, groups,
  connections and every non-Snowflake layer, but not a Snowflake layer.
- **Deleting the Snowflake sign-in code.** It is switched off, not removed.
  Removing it is a later cleanup, once every deployment has run with it off.
- **The dev key-pair login.** Unchanged: off unless `DEBUG`, its flag and its
  key material are all present.
- **Renaming `snowflake_user`.** The session and audit field still holds the
  app username. A rename with no behaviour in it, left to the cleanup spec
  (as in 0006).
- **Multi-factor authentication, single sign-on, and social login.**

## The checklist

| | |
|---|---|
| **Reach** — how does someone get to this, from every workspace and every connector? | Sign-in, sign-up, verification and reset are pages outside the app shell, the same for every deployment. Connect Snowflake stays where it is today: the Connectors page, the Snowflake workspace and the account dialog. The access-check fix reaches every workspace. Its whole point is that a SQL Server or files-only person is never sent through Snowflake. |
| **Permission** — who may do it, and what does everyone else see instead? | Anyone may sign up when `SIGNUP_MODE=open`; otherwise only by invitation. A new account is never an administrator. Administrators come from a confirmed email in `ADMIN_EMAILS` or from the Users page, never from signing up. Data access is unchanged: data groups decide what a person sees in the app, and Snowflake itself decides what their linked role can query. Nobody gains access to any database from this spec. |
| **Failure** — what happens when the source is slow, down, or refuses? | No SMTP configured: sign-up and reset still answer "check your email", nothing is sent, and the startup log says so. `EMAIL_LINKS_IN_LOG` is for local development only. Redis unreachable: rate limiting fails *closed* for sign-in and sign-up (503) rather than allowing unlimited guessing. Snowflake down or a token dead: unchanged, Snowflake things say "Connect Snowflake again" and everything else keeps working, which the access fix makes true. |
| **Isolation** — what is scoped per workspace, per connection, per deployment, and what survives a redeploy? | Accounts, reset tokens and the session counter are per deployment, in `APP_DB_PATH` on its pinned volume. Rate-limit counters are per deployment: in Redis where sessions are, in process memory in local development. Each environment has its own `SNOWFLAKE_SIGN_IN_ENABLED`, so it can be turned off in dev first. |
| **The model** — what does the AI get, and when? | n/a. Nothing about sign-in reaches the model. The chat keeps running as the person, filtered by the same `Access`. |
| **Cost** | Sign-in costs one Argon2id hash (about 64 MiB and tens of milliseconds) instead of one scrypt hash. Each request already reads the account; the session counter is one more column in that read. Rate limiting adds one Redis round trip to the five public auth routes only. The access fix removes a Snowflake connection (and on a cold connection, a login) from every guarded request by a person whose resources are local. |
| **Migration** | One additive SQLite migration (`users.session_epoch`). No account, dashboard, report, chat, audit row, group or connection is rewritten. User ids and usernames are kept: an account made by Snowflake sign-in keeps its Snowflake username, so its history and group memberships stay its own. Existing hashes are upgraded to Argon2id at each person's next sign-in. Snowflake sign-in is turned off per environment, only after its Snowflake-only accounts have been given a password (see *Turning Snowflake sign-in off*). |

## Behaviour

The build runs in this order. Each phase ships and is tested on its own.

### Phase A — the access check stops needing Snowflake

Amended: as built, pp/api/warehouse.py gives every route one rule. A route binds
the person's own Snowflake connection if they have connected one, 
o_warehouse()`nif not, and a Snowflake-only route asks for the connection and answers 409 without
one. The web app also refuses the engine's shared connection outright at startup
(efuse_shared_connection), so a route that forgot to bind raises rather than
running as a service account. The audit trail's Snowflake copy is written only
over a bound connection. The app store's audit trail is unaffected.

`run_as_user` in `app/api/access.py` is used for two different jobs: running
engine work as the person, and *looking up* which chain a resource belongs to
for a data-group decision. The lookups read the app store (`dashboard_store`,
`report_store`, `excel_datasets`) and fall back to the old Snowflake tables
only for records made before those moved.

- The chain lookups (`dashboard_chain`, `report_chain`, `dataset_folder`,
  `conversation_chain`, and the folder lookup in `routes/agent.py`) run through
  a new helper.
  - **If the account has a Snowflake link**, it binds that connection as today.
  - **If not**, it binds an explicit *no warehouse*. Under it,
    `engine_client.get_connection()` raises instead of falling back to the
    shared connection, and `get_connection_or_none()` answers None.
- **Fail closed.** For a person with no link, a resource the app store does
  not know is refused with the same 404 a missing id gets, never let through.
  That person cannot open an old Snowflake-stored record anyway:
  `_require_warehouse` already refuses it.
- **Why the explicit binding matters.** `engine_client.get_connection()`
  with nothing bound falls back to a process-wide shared connection,
  configured from `SNOWFLAKE_USER` and the key material where present. Running
  the lookups "unbound" would quietly query Snowflake as a service account.
  That is exactly what this spec rules out, so the no-warehouse binding is a
  requirement, not a detail.

### Phase B — rate limiting

Amended: emailed links carry their token in the URL fragment (/reset#...), and
the verify, reset and invitation endpoints take it in a request body
(POST /auth/verify, /auth/password/reset, /auth/invitations/accept), so it is
in no access log. Paths from links already sent still open the pages.

- A small limiter in `app/core/rate_limit.py`. It uses Redis when
  `SESSION_BACKEND=redis` and process memory otherwise.
- It is a fixed-window counter keyed by route, client IP and (where there is
  one) the normalised email.
- Applied to `POST /auth/password`, `POST /auth/signup`,
  `POST /auth/verify/resend`, `POST /auth/password/forgot`,
  `POST /auth/password/reset/{token}` and `POST /auth/invitations/{token}`.
- Over the limit, the answer is 429 with `Retry-After` and a message that says
  nothing about whether the account exists.

| Route | Per IP | Per email |
|---|---|---|
| sign-in | 30 / 15 min | 10 / 15 min |
| sign-up | 5 / hour | — |
| resend verification, forgot password | 10 / hour | 3 / hour |
| reset, accept invitation | 10 / 15 min | — |

All are settings, with these as defaults.

- **The per-account lock** stays as the backstop, raised from 5 to 20 failures.
  The per-email limit now does the day-to-day work. A stranger who knows
  someone's email can no longer lock them out with five guesses.
- **The client IP must be the real one.** Production runs Traefik → nginx →
  uvicorn. Without trusted-proxy configuration, uvicorn sees nginx's address
  for every request and the per-IP limit becomes one bucket for everyone.
  - uvicorn runs with `--proxy-headers` and `FORWARDED_ALLOW_IPS` set to the
    Docker network the proxies are on.
  - The Traefik `ratelimit@docker` middleware already on the API router is
    left as it is: a coarse outer limit, not a replacement.

### Phase C — password reset, and signing out everywhere

- `POST /auth/password/forgot {email}`
  - Always answers 202 with the same body.
  - For an active account with that email, it makes a single-use `email_tokens`
    row with `purpose = 'reset'`. Only the newest is valid, it expires after
    `PASSWORD_RESET_TTL_MINUTES` (default 30), and only its hash is stored.
  - It emails the link `/reset/{token}`.
- `GET /auth/password/reset/{token}` returns the email it is for, so the page
  can show it.
- `POST /auth/password/reset/{token} {password}`
  - Checks the password rules, sets the password and marks the email confirmed
    (opening the link proves the inbox).
  - Clears any lock, uses up the token, and **signs out every other session of
    that account**.
  - Then signs this browser in with a new session id.
- **Signing out everywhere.**
  - `users.session_epoch` is copied into the session at sign-in.
  - `get_current_session` already reads the account on every request. A
    session whose counter is behind the account's is deleted and answered 401.
  - The counter goes up on password reset and password change. On a change,
    the session that made it is re-issued with the new counter, so the person
    stays signed in there.
- **Admin: give an account a way in.** On the Users page, an account with no
  email (made by Snowflake sign-in) gets "Set email and send sign-in link".
  - It sets the email (unconfirmed) and sends a reset link to it.
  - Opening that link confirms the email and sets the password **on the same
    account**, so the same user id and the same username are kept.
  - Only offered for an account with no email: an administrator cannot point
    someone else's existing email somewhere new.
- **UI**
  - "Forgot password?" on the sign-in page.
  - Pages `/forgot-password` and `/reset/:token`, built from the existing
    `account-form` and auth page layout.

### Phase D — Argon2id

- New hashes are Argon2id via `argon2-cffi`, using its RFC 9106 low-memory
  defaults (t=3, m=64 MiB, p=4).
- `verify_password` reads both formats: an `$argon2id$` prefix goes to
  Argon2, and `scrypt$` goes to the existing code.
- `needs_rehash` is true for every scrypt hash and for any Argon2 hash with
  older parameters. So each person is upgraded silently at their next
  successful sign-in, as the code already does for scrypt parameter changes.
  Nobody is asked to reset.
- The timing decoy becomes an Argon2id hash, so an unknown email still costs
  what a wrong password does.

### Phase E — Origin check

- For `POST`, `PUT`, `PATCH` and `DELETE` under `/api`, a middleware compares
  the `Origin` header (or, without one, the origin of `Referer`) against the
  allowed origins: `CORS_ORIGINS` plus the origin of `FRONTEND_URL`.
  - A mismatch is answered 403 before the route runs.
  - A request with neither header is allowed. Browsers always send `Origin`
    on cross-site requests that change things, so that case is a script or a
    test, which a cookie forged by a website cannot be.
- This is defence in depth behind what already holds: `SameSite=lax` cookies,
  and FastAPI refusing to read a non-JSON body as JSON.

### Phase F — Snowflake sign-in is removed; Connect Snowflake stays

Amended: this was first specified as a setting (`SNOWFLAKE_SIGN_IN_ENABLED`,
default on). The owner decided to remove it outright, so there is no setting.

- `GET /auth/login` is gone. `GET /auth/snowflake/connect` replaces its
  `?link=true` half: it needs a signed-in session, records a link request for
  that account, and redirects to Snowflake. It is 503 when OAuth is not
  configured, which does not touch signing in.
- `GET /auth/callback` checks its OAuth state, then looks up the link request
  **before** asking Snowflake for anything. A callback with no link request
  (what used to sign a Snowflake user in) exchanges no code, makes no account,
  stores no token and starts no session; it redirects to `/login` saying so. A
  link request finished in another browser than the one that began it is
  refused the same way. Only then is the code exchanged and the Snowflake user
  linked to the account that asked.
- `accounts.provision_from_snowflake` is deleted: nothing can make an account
  from a Snowflake identity. `promote_from_role` is deleted: linking, or a
  session's role, never makes an administrator. Accounts promoted before keep
  their flag.
- `GET /auth/capabilities`: `oauth` is gone; `snowflake_link` says whether
  Connect Snowflake can work here; `password_reset` is true.
- Sessions that signed in through Snowflake before the deploy keep working until
  they expire (`SESSION_TTL_SECONDS`).
- **UI.** The sign-in page lists only what `capabilities` reports, so it shows
  an email and password form and no Snowflake button. Connect Snowflake is
  unchanged in the account dialog, the Connectors page and the Snowflake
  workspace; it now goes to `/auth/snowflake/connect`.
### Phase G — sign-up asks for a full name

- `POST /auth/signup` requires `display_name` (1–120 characters after
  trimming), as well as email and password. The sign-up page marks it
  required.
- The invitation form keeps it optional, since the administrator who invited
  them knows who they are.
- **Password rules**, following NIST SP 800-63B rather than composition rules:
  - at least `PASSWORD_MIN_LENGTH` (10) characters;
  - not on a bundled list of the 10,000 most common passwords;
  - must not contain the email's local part.
  - The page states the rule before the person submits.
- Email verification stays on and is required before a password sign-in, as
  now.

### Rolling out

An operational procedure, in [DEPLOYMENT.md §4b](../DEPLOYMENT.md#4b-rolling-this-out-and-rolling-it-back):
back up, deploy with `PASSWORD_HASH_ALGORITHM=scrypt` to the dev stack, give
every Snowflake-only account a password through **Send sign-in link** on the
Users page (same account, same history), run the checks, and only later switch
hashing to Argon2id. Removing the sign-in means a Snowflake-only account that is
not given a password cannot get back in once its session expires, so that step
comes before the deploy reaches anyone's production.
## Data

**Migration 11** (additive, in `app/accounts/store.py`):

```sql
-- Bumped when every session of this account must end: a password reset or
-- change. A session carries the value it signed in with; a lower one is over.
ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
```

- **Reset tokens** reuse `email_tokens` with `purpose = 'reset'`. The column
  is free text, so this needs no schema change.
- **Sessions** gain `session_epoch: int = 0`. Sessions stored before the
  change read back as 0, which matches every account's starting value, so no
  one is signed out by the deploy.
- **Password hashes** are rewritten one at a time, at sign-in, in the same
  write that already resets `failed_logins`.
- Nothing is deleted. `credentials` rows of kind `snowflake` stay. They are
  the links that Connect Snowflake uses.

## Acceptance checks

**A — access without Snowflake**

1. Reproduce first, then fix. With `DATA_GROUPS_ENFORCED=true`, a non-admin
   password account with no Snowflake link opens a SQL Server dashboard, an
   upload dataset, a report and a chat that its groups allow. Before the fix
   this answers 409; after, 200.
2. The same person gets 404 for each of those when its groups do not allow it.
3. During those requests no Snowflake connection is opened, *including the
   shared one*. The test rigs both the per-user and shared connection
   factories to fail.
4. A person with a Snowflake link still has old Snowflake-stored dashboards
   resolved and filtered as before.

**B — rate limiting**

5. The 11th sign-in attempt for one email inside the window is 429 with
   `Retry-After`, from any IP. The 31st from one IP across many emails is 429.
6. The answer for a limited unknown email and a limited real one is
   identical.
7. Twenty wrong passwords lock the account. Five no longer do.
8. With Redis selected and unreachable, sign-in and sign-up answer 503, not
   200.
9. On the deployed dev stack, two clients on different IPs get separate
   per-IP buckets (checked by hand against the running stack).

**C — reset and sessions**

10. `forgot` answers identically for a real, an unknown and a disabled email,
    and sends mail only for the real, active one.
11. A reset link works once, stops working after 30 minutes, and is replaced
    by a newer one.
12. After a reset, the account's other sessions get 401 on their next request
    and the resetting browser is signed in.
13. After a password change, other sessions get 401 and the session that made
    the change keeps working.
14. "Set email and send sign-in link" on an account with no email ends with
    the same `user_id` and `username`, a confirmed email and a working
    password. It is refused for an account that already has an email.
15. Sessions stored before the migration still work after it.

**D — Argon2id**

16. A new account's stored hash starts `$argon2id$`. No stored hash contains
    the password.
17. An account with an existing scrypt hash signs in, and afterwards its hash
    is Argon2id.
18. A malformed hash fails the sign-in and never raises.

**E — Origin**

19. A cookie-authenticated `POST` with `Origin: https://evil.example` is 403.
    The same request from `FRONTEND_URL`'s origin, or with no `Origin` or
    `Referer`, goes through.

**F — Snowflake sign-in off**

20. `capabilities` has no `oauth` key and reports `snowflake_link: true` when
    OAuth is configured.
21. A callback that is not a link makes no account, stores no token and does
    not call the token endpoint.
22. Connect Snowflake from a password account still links. The account's next
    Snowflake query runs as that Snowflake user, and `CURRENT_ROLE()` is
    *their* role, not `SNOWFLAKE_DEFAULT_ROLE`.
23. Linking with a role in `ADMIN_ROLES` does not make the account an
    administrator.
24. A session that signed in with Snowflake before the switch keeps working
    until it expires.

**G — sign-up and the whole**

25. Sign-up without a name, with a short or common password, or with an email
    already in use is refused with a message that says which. The duplicate is
    caught by the database's unique index, not only a check in code.
26. A new account cannot sign in until its email is confirmed, and is not an
    administrator once it can. An unconfirmed email in `ADMIN_EMAILS` is not
    an administrator either.
27. The full backend suite passes, including the four Snowflake OAuth tests
    that fail today when the suite runs together (see *Notes*). The frontend
    typechecks.
28. By hand, against the running app: sign up, confirm, sign in, open a
    dashboard, chat, upload a file, sign out, reset the password. Each works,
    and each page behaves as before.

## Open questions

All four were decided by the owner on 2026-10-23.

1. **Default for `SNOWFLAKE_SIGN_IN_ENABLED`. Superseded.** Decided first as `true` in
   code and `false` in `.env.example`. The owner then chose to remove Snowflake
   sign-in rather than gate it, so the setting does not exist (Phase F). What
   carried over is the condition: no environment goes live until its
   Snowflake-only accounts have a way in (see *Rolling out*).
2. **Admin promotion. Decided:** linking or signing in with Snowflake never
   grants app administration, whether or not Snowflake sign-in is on.
   `promote_from_role` is no longer called from either path. Accounts
   already promoted keep their flag, because it was set and recorded.
   Acceptance check 23 applies with sign-in on as well as off.
3. **Breached passwords. Decided:** no outbound Have I Been Pwned call for
   now. The policy is the length rule, plus a maintained, bundled
   common/compromised-password list, plus the email check (Phase G). The
   list's source and version are recorded beside it, so it can be refreshed.
4. **`SIGNUP_MODE` in production. Decided:** it stays `invite` until SMTP,
   rate limiting (B), password reset (C) and their acceptance checks are
   verified on the deployed dev stack. Only then is `open` considered for
   prod.

## Notes

**Baseline before any code (2026-10-23).**

- The auth, account and session tests (116 in total) give 112 passed and
  4 failed when run together:
  - `test_a_password_account_links_its_snowflake`
  - `test_a_link_finished_in_another_browser_links_nothing`
  - `test_linking_needs_an_app_account`
  - `test_login_redirects_to_snowflake`
- `test_login_redirects_to_snowflake` passes on its own. So the failures look
  like ordering or environment interference between tests, not a product
  fault. One candidate is a newly created local `backend/.env` that `pipenv`
  loads into the environment.
- Not investigated yet. Diagnosing them is the first step of Phase A, before
  any code changes, so the baseline is known.

### Built 2026-10-23: what was verified, and what was not

**Baseline before any change.** Backend: 1523 passed, 3 skipped, 1 failed, 8
errors. The four OAuth tests failed only because `pipenv run` exports the
developer's `backend/.env`, whose blank `OAUTH_CLIENT_ID=` beat the tests'
`setdefault`. The nine other failures were `tests/engine/test_large_dashboards.py`
building an `httpx.Client` for the pinned `anthropic==1.2.0`, which is built on
`httpx2` and refuses it. Both fixed in the tests (an empty variable now counts as
unset; the tests use `httpx2`); no assertion was relaxed.

**After.** Backend: 1621 passed, 3 skipped, 0 failed (with a developer `.env`
loaded, which is what used to break it). Frontend: `tsc` clean and `vite build` succeeds.

**End to end, through the Vite dev proxy:** 55 of 55 checks (register, name
required, duplicate email, common password, confirm email, session restore, logout,
change password signing other sessions out, forgot/reset, 429 with Retry-After,
Origin 403, no Snowflake sign-in route, the callback doing nothing without a
link, Connect Snowflake redirect, `ADMIN_EMAILS` only after confirmation, a new
user not an administrator, every Snowflake-shaped list answering 200 for an
unlinked user, and an old Snowflake-made account getting a password with the same
user id and username).

**The dev-stack checklist is a script:** `backend/scripts/verify_dev_stack.py`. It was itself tested against a local backend in four ways: a normal run (50 passed), a wrong administrator password (fails, exit 1), the two address probes (probe A passes on a correct stack at attempt 31, fails when the stack believes forged X-Forwarded-For; probe B fails from an address already limited), and a run while rate limited (stops with exit 3 instead of reporting false failures). The real-mail section was driven by a simulated person reading the reset link from the log (58 passed). Testing it found three defects in the script, all fixed: it spent more of the confirm/reset allowance than a back-to-back run had left; a 429 was reported as a product failure; and a second run with the same --email-address was refused with no explanation.

**Not verified, and why.**
- **Per-IP limits behind Traefik and nginx.** The address logic is unit tested and
  the local proxy returns 429, but no Docker or deployed stack was available. Check
  on the dev stack that two networks are counted separately (acceptance 9).
- **Connect Snowflake against a real Snowflake account.** Every Snowflake call
  in the tests and the end-to-end run is faked; nothing reached a real account.
  The redirect, state handling and refusals are checked; a real consent screen
  and a real token exchange are not.
- **The UI in a browser.** The new pages typecheck and build, and every API they
  call was exercised, but no browser was driven.
- **Real email.** Mail was captured, not sent; SMTP delivery is unverified.
- **Frontend lint.** `eslint` reports 37 problems, none in the new pages or
  libs: 35 are in files this work did not touch and two are in
  `auth-context.tsx` on lines it did not write (React-compiler rules). Not
  fixed here.
- **No frontend unit tests exist** in the repository.

**Something the build turned up.** The previous release cannot read Argon2id
hashes, so upgrading hashes at sign-in would make rollback lock people out, with
no password reset on the old code to get them back in. Hence
`PASSWORD_HASH_ALGORITHM=scrypt` for the first stage (Phase D, *Rolling out*).

**Decisions the owner made along the way.** Snowflake sign-in removed, not
gated; no HIBP; `SIGNUP_MODE=invite` in production until SMTP, rate limiting and
reset are verified on the dev stack; a Snowflake role never grants app admin.

**Amended 2026-10-24: email confirmation is a setting.** The owner asked for plain sign-up and sign-in with no confirmation step. `EMAIL_VERIFICATION_REQUIRED` (default `true`) turns it off: sign-up then signs the new account straight in and login no longer demands a confirmed email. Because an unconfirmed address proves nothing, `ADMIN_EMAILS` keeps requiring a *confirmed* email, so with confirmation off it grants nothing; `backend/scripts/make_admin.py` makes an administrator from the server instead. The default is unchanged, so no deployment changes behaviour unless it sets this. Tests: sign-up signs in, an unconfirmed account can sign in, the same account is still refused with the setting on, and an unconfirmed `ADMIN_EMAILS` address is not an administrator. Full backend suite: 1627 passed, 3 skipped, 0 failed.
