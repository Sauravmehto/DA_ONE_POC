# 0010 — Anyone can sign up, and every account gets a 14-day trial

**Status:** done (2026-10-26)
**Touches:** sign-in and sign-up pages, accounts store (SQLite), session check, startup checks, Users admin page

## Why

Today the deployed demo is invite-only: the login page says "Access is
provisioned by your administrator", and no one can create the first account
without SMTP or log-reading tricks. We want a person to open the site, sign up
with their name, email and password, and use the product — for a limited
time. Each email address gets one 14-day trial.

## Not in scope

- Payment, plans, or converting a trial to a paid account (a later spec).
- Email confirmation links and SMTP. Sign-up stays one step with no mail,
  which means an address is **not proven**; see Open questions.
- Persistent storage on Render's free tier. Accounts still disappear on
  restart until a database volume exists (see Isolation).

## The checklist

| | |
|---|---|
| **Reach** | `/signup` (name, email, password) from a "Create one" link on `/login`. `/login` takes email and password only. After sign-in the user lands on `/files`. |
| **Permission** | Anyone may sign up. Administrators are unchanged (`ADMIN_EMAILS` / Users page) and are never subject to the trial. Everyone else is limited to their trial. |
| **Failure** | Trial over: every API request answers `403 trial_expired`; the UI shows a "Your trial has ended" page with a contact line and a sign-out button, not a broken app. A repeat sign-up with an email that already had a trial says so plainly and does not start a new one. |
| **Isolation** | Trial state belongs to the email, not the account row: a `trials` table keyed by normalised email survives an account being deleted, so deleting and re-creating an account cannot restart the clock. On Render's free tier the whole SQLite file is still ephemeral. |
| **The model** | Unchanged. |
| **Cost** | Open sign-up on a public URL means anyone can spend the Anthropic key during their 14 days. Rate limits on sign-up/sign-in already exist; a per-trial message cap is proposed in Open questions. |
| **Migration** | One new migration in `accounts/store.py` adds the `trials` table. Existing accounts get no trial row and are treated as **not on a trial** (unlimited), so nobody is locked out by the deploy. |

## Behaviour

- **API**
  - `POST /auth/signup` — requires a full name (already enforced), email,
    password. With `SIGNUP_MODE=open` and `EMAIL_VERIFICATION_REQUIRED=false`
    it creates the account, starts the trial (`trials.started_at = now`,
    `ends_at = now + TRIAL_DAYS`), signs the person in and returns `signed_in`.
  - `POST /auth/password` — email and password only; the name is never asked
    at sign-in. A wrong or unknown pair keeps today's single message.
  - `GET /auth/me` — gains `trial: {ends_at, days_left, expired}` for non-admin
    accounts (omitted for admins and for accounts with no trial row).
  - `get_current_session` — after the existing disabled-account check, a
    non-admin account whose trial has ended gets `403` with
    `{"code": "trial_expired"}` on every route except `/auth/me`, `/auth/logout`.
  - `TRIAL_DAYS` setting, default `14`.
- **UI**
  - `/login`: the "Access is provisioned by your administrator." line is
    removed. The "No account yet? Create one" link is always shown.
  - `/signup`: name, email, password, and a line "14-day free trial".
  - Sidebar footer and `/files`: show the person's **name** (not the role
    text) with the email under it, and a "N days left in your trial" badge.
  - Trial-ended page as described under Failure.
- **Admin** — Users page shows each account's trial end date and an
  "Extend trial" action (+14 days) so a specific email can be given more time.
- **Startup checks** — `SIGNUP_MODE=open` without SMTP stops being a startup
  error when `EMAIL_VERIFICATION_REQUIRED=false`; it becomes a loud warning
  that addresses are unproven. With verification on, the check stays.

## Data

New table, one row per email that ever started a trial:

```
trials(email TEXT PRIMARY KEY COLLATE NOCASE, started_at TEXT NOT NULL,
       ends_at TEXT NOT NULL, extended_by TEXT)
```

Email is normalised (trimmed, lower-cased; Gmail dots and `+tags` are **not**
folded, see Open questions). Passwords stay Argon2id in `credentials`; names in
`users.display_name`. Nothing about existing accounts changes.

## Acceptance checks

1. `/login` shows no "provisioned by your administrator" text anywhere, and
   shows "Create one" with `SIGNUP_MODE=open`.
2. Sign up with name + email + password, land on `/files` signed in, with the
   name and email in the sidebar footer.
3. Sign out, sign in with email + password only (no name field), land on `/files`.
4. A new account's `/auth/me` shows `days_left = 14`.
5. With a trial row whose `ends_at` is in the past, every API call but
   `/auth/me` and logout returns 403 `trial_expired`, and the UI shows the
   trial-ended page.
6. Deleting an account and signing up again with the same email does not
   reset `ends_at`.
7. An administrator is never blocked by a trial.
8. "Extend trial" on the Users page moves `ends_at` and unblocks the person.
9. The backend starts with `SIGNUP_MODE=open`, no SMTP, `DEBUG=false`,
   `EMAIL_VERIFICATION_REQUIRED=false`; it still refuses open sign-up without
   SMTP when verification is required.
10. Backend tests cover 4–8; the frontend type-checks.

## Open questions

All four were decided on 2026-10-26, taking the recommendations:

1. Gmail dots and `+tags` are folded into one trial key; real email
   confirmation comes with SMTP.
2. 50 chat messages per trial (`TRIAL_MESSAGE_CAP`; 0 turns it off).
3. After expiry everything is blocked except `/auth/me` and sign-out.
4. The first account on an empty database is the administrator.

## Notes

Written before any code, per CLAUDE.md. Amended in the same commit as the
change it describes.

- A repeat sign-up with an address whose trial already ended is allowed to
  create the account, but its trial is not restarted: the person lands on the
  "trial has ended" page. (The checklist said it "says so plainly"; the page does.)
- The message cap counts `/agent/chat` and `/chat`. Dashboard-assist calls are
  not counted yet.
- Existing accounts have no `trials` row and stay unlimited.
- `render.yaml` now sets `SIGNUP_MODE=open`, `EMAIL_VERIFICATION_REQUIRED=false`,
  `TRIAL_DAYS`, `TRIAL_MESSAGE_CAP`. Set the same on an already-created Render
  service by hand.
