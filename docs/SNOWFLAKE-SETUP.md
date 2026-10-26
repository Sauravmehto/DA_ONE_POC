# Snowflake setup

What must exist in Snowflake for people to use Snowflake **data** in the current
build. Everything below needs `ACCOUNTADMIN`.

## What Snowflake is, and is not, to the app

**Snowflake is a data source a person connects, not how they sign in.** People
sign in to DataAlphaOne with an app account: email and password, kept in the
app's own store (`APP_DB_PATH`, see [ARCHITECTURE.md](./ARCHITECTURE.md)).
Who someone is, and whether they are an administrator, are decided there.

A person who wants Snowflake data connects their Snowflake user to their
account ("Connect Snowflake", on the Connectors page or in the account dialog).
From then on:

- their Snowflake queries run **as that Snowflake user, under their own
  role**, through their own OAuth token, so Snowflake's grants and row-access
  policies apply to them exactly as they would anywhere else;
- the app never queries Snowflake for them through a shared or service
  identity.

Someone who never connects Snowflake can still use everything that is not
Snowflake: SQL Server connections, uploads, and the dashboards and chats built
on them. They see no Snowflake databases.

> **There is no Snowflake sign-in.** The OAuth integration below is used only
> by **Connect Snowflake**, which a signed-in person starts from their own
> account. The callback finishes nothing else: a callback that is not a
> Connect Snowflake request is refused before Snowflake is asked for tokens,
> makes no account, and starts no session (spec
> [0007](./specs/0007-sign-in-is-the-apps-own.md)). Accounts that an earlier
> version made when someone signed in with Snowflake still exist, with their
> history; an administrator gives each a password from the Users page
> ([DEPLOYMENT.md](./DEPLOYMENT.md#4b-rolling-this-out-and-rolling-it-back)).

So the steps below are needed only on a deployment where people will use
Snowflake data. A deployment with only SQL Server or uploads needs none of
them.

---

## 1. OAuth security integration — what Connect Snowflake uses

Snowflake custom OAuth has one primary `OAUTH_REDIRECT_URI` per integration,
plus optional `OAUTH_ALTERNATE_REDIRECT_URIS`. Either make one integration per
environment, or list every environment's callback as an alternate (see
[DEPLOYMENT.md §3](./DEPLOYMENT.md#3-connecting-snowflake-oauth)).

```sql
CREATE SECURITY INTEGRATION DATAALPHAONE_OAUTH
  TYPE = OAUTH
  ENABLED = TRUE
  OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  OAUTH_REDIRECT_URI = 'https://one.dataalpha.in/api/v1/auth/callback'
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  OAUTH_REFRESH_TOKEN_VALIDITY = 86400
  BLOCKED_ROLES_LIST = ('ACCOUNTADMIN','SECURITYADMIN')
  COMMENT = 'DataAlphaOne web app';

SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('DATAALPHAONE_OAUTH');
```

For local development, the same again with a different name and:

```sql
  OAUTH_REDIRECT_URI = 'http://localhost:8001/api/v1/auth/callback'
```

`http://` is accepted **only** for localhost. Put the client id and secret into
that environment's `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET`.

**`OAUTH_REFRESH_TOKEN_VALIDITY` is how long a connection lasts.** The app
stores each person's refresh token (encrypted with `TOKEN_ENCRYPTION_KEY`) so
it can open their Snowflake connection again after they next sign in with
their password. When the refresh token expires, which is 86400 s (one day) as
written above, the person is asked to Connect Snowflake again. Raise it, within
your security policy, if a daily reconnect is too often.

## 2. Every Snowflake user who connects needs a DEFAULT_ROLE

The app does not force a role onto a connection. It connects under the user's
own default role, which is what makes per-person access work.

```sql
ALTER USER analyst_priya SET DEFAULT_ROLE = PRIVATE_CREDIT_ANALYST;
```

A user with no usable default role connects and sees no databases, with no
error explaining why.

`ACCOUNTADMIN` and `SECURITYADMIN` are blocked from OAuth by
`BLOCKED_ROLES_LIST`. A user whose **default** role is one of those cannot
connect at all, and the failure does not say so.

Where `SNOWFLAKE_AVAILABLE_ROLES` is set, a connected person can switch to
another listed role in the app. Snowflake must still grant them that role.

## 3. Roles for the databases people should see

One role per audience. `SHOW DATABASES` returns only what the role can see, and
that is what the app lists, queries and lets the assistant read, for that
person.

```sql
CREATE ROLE PRIVATE_CREDIT_ANALYST;
GRANT USAGE ON DATABASE PRIVATECREDIT TO ROLE PRIVATE_CREDIT_ANALYST;
GRANT USAGE ON ALL SCHEMAS IN DATABASE PRIVATECREDIT TO ROLE PRIVATE_CREDIT_ANALYST;
GRANT SELECT ON ALL TABLES IN DATABASE PRIVATECREDIT TO ROLE PRIVATE_CREDIT_ANALYST;
GRANT SELECT ON FUTURE TABLES IN DATABASE PRIVATECREDIT TO ROLE PRIVATE_CREDIT_ANALYST;
GRANT USAGE ON WAREHOUSE <WH> TO ROLE PRIVATE_CREDIT_ANALYST;

GRANT ROLE PRIVATE_CREDIT_ANALYST TO USER analyst_priya;
ALTER USER analyst_priya SET DEFAULT_ROLE = PRIVATE_CREDIT_ANALYST;
```

Repeat per persona. A demo persona for a private credit fund gets this role and
sees only `PRIVATECREDIT`.

Snowflake's grants decide what a connected person can **query**. The app's
data groups (Configuration → Data groups) decide which databases, dashboards,
reports and folders they are **shown** in the app. Both apply, and neither
widens the other.

## 4. A role for writing Snowflake semantic layers

**This is not how someone becomes an app administrator.** Administrators are
app accounts: a confirmed email listed in `ADMIN_EMAILS`, or someone made one
on the Users page (see [DEPLOYMENT.md](./DEPLOYMENT.md#4-app-accounts-and-sign-in)).

What a Snowflake role still decides is narrower: who may **save** a Snowflake
database's semantic layer and Excel report templates. That needs a connected
Snowflake session whose active role is listed in `ADMIN_ROLES`, because a
layer describes data, and the person writing it should be able to see that
data. Every other source's layer is any app administrator's to write. Reading
is open to anyone who can see the source.

```sql
CREATE ROLE DATAALPHA_ADMIN;

-- Must be able to READ what it describes, or the editor is empty.
GRANT USAGE ON DATABASE PRIVATECREDIT TO ROLE DATAALPHA_ADMIN;
GRANT USAGE ON ALL SCHEMAS IN DATABASE PRIVATECREDIT TO ROLE DATAALPHA_ADMIN;
GRANT SELECT ON ALL TABLES IN DATABASE PRIVATECREDIT TO ROLE DATAALPHA_ADMIN;
GRANT SELECT ON FUTURE TABLES IN DATABASE PRIVATECREDIT TO ROLE DATAALPHA_ADMIN;
GRANT USAGE ON WAREHOUSE <WH> TO ROLE DATAALPHA_ADMIN;

GRANT ROLE DATAALPHA_ADMIN TO USER admin_sam;
ALTER USER admin_sam SET DEFAULT_ROLE = DATAALPHA_ADMIN;
```

Then set `ADMIN_ROLES=DATAALPHA_ADMIN` in the app environment.

Two things that catch people out:

- **The app checks the ACTIVE role, not every role the user holds.** A role
  that is not the user's default, and not switched to in the app, grants
  nothing. Set it as their default.
- **The role needs its own database grants.** It means "may write the layer",
  not "may see more". A `DATAALPHA_ADMIN` with no `USAGE` grants opens the
  editor to an empty database list.

A Snowflake role never makes anyone an app administrator, and connecting
Snowflake does not either. An earlier version promoted an account once, when
it signed in through Snowflake with a role in `ADMIN_ROLES`; that is gone
(spec [0007](./specs/0007-sign-in-is-the-apps-own.md)). Accounts promoted that
way keep their flag, and an administrator can change it on the Users page.
## 5. Tables in Snowflake

None are needed for a new deployment. Dashboards, reports, data groups,
semantic layers and connections are the app's own records, in `APP_DB_PATH`
(specs 0001, 0004, 0005).

`backend/sql/saved_dashboards.sql` and `backend/sql/data_groups.sql` describe
the Snowflake tables older deployments used. Where those tables exist, the app
still reads them as a fallback for records made before the move. It never
writes to them (DECISIONS, 2026-09-23).

---

## Verify

```sql
-- Does the persona see only what it should?
SHOW GRANTS TO ROLE PRIVATE_CREDIT_ANALYST;

-- Is the default role set, and not a blocked one?
DESC USER analyst_priya;   -- check DEFAULT_ROLE

-- Is the integration's redirect URI exactly the app's callback?
DESC SECURITY INTEGRATION DATAALPHAONE_OAUTH;
```

Then, in the app:

1. Sign in with an email and password.
2. Connect Snowflake as that user.
3. Confirm `GET /api/v1/auth/me` reports `snowflake_linked: true`, and that
   their own role appears once a Snowflake page has been opened, not the value
   of `SNOWFLAKE_DEFAULT_ROLE`.
