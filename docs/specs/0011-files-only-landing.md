# 0011 — Sign-in lands on Files, and Snowflake stays hidden

**Status:** done (2026-10-29)
**Touches:** sign-in, the home route, the sidebar, the account dialog, the connector picker

## Why

This deployment only uses Files: upload a document, chat about it. After
signing in, everyone is sent to `/`, a "Connect to your data" picker that
offers Snowflake, any SQL Server connections, and Files side by side, with
its own header and its own log-out button. That page is a detour in front of
the one thing the app does here, and the Snowflake controls in the sidebar,
the account dialog, and that picker expose a source nobody on this deployment
uses.

Snowflake stays in the repo. It is an optional per-user data source, not a
way in, and a deployment that wants it sets one flag. See
[DECISIONS.md](../DECISIONS.md) (2026-10-23): no Snowflake query runs without
a person's own linked Snowflake user.

## Not in scope

- Deleting Snowflake, SQL Server, or the connector picker. The flag hides
  them; it does not remove them.
- Admin pages that mention Snowflake (`pages/admin/users.tsx`,
  `pages/admin/semantic-layers.tsx`). They report real state to an
  administrator and stay visible.
- The chat's source picker. On `/files` it lists folders. The Snowflake
  database list only renders when the workspace kind is `snowflake`.
- The Snowflake-disconnect toast in the auth context. It only runs when a
  session that had Snowflake linked loses it.
- The role submenu in the user menu. It already requires
  `user.snowflake_linked`, so it does not appear when Snowflake is not
  connected.
- Backend auth. The flag is not a permission check.

## The checklist

| | |
|---|---|
| **Reach** | Login and signup go to `/files` when the flag is off, and to `/` when it is on. A signed-in visit to `/` does the same. Invite, email verification, and password reset still navigate to `/`, which then redirects to `/files` while the flag is off, so they land on Files without showing the picker. A bookmark to `/snowflake` with no linked Snowflake user is sent to `/` and then to `/files`. `/sqlserver/...` for a missing connection does the same. `/files` and `/files/library` are unchanged. |
| **Permission** | Unchanged. The flag is a UI affordance, not an auth boundary. Anyone signed in can open Files. The backend already refuses a Snowflake query when the session has no linked Snowflake connection. |
| **Failure** | If the flag is left on by mistake, the old picker is what people see. Nothing else breaks. A direct visit to a Snowflake or SQL Server route still behaves as it does today. |
| **Isolation** | n/a, because this is frontend routing and visibility only. The flag is read from `VITE_SHOW_CONNECTORS` at build time, per deployment, the same way `VITE_SHOW_REPORT_BUILDER` is. It is unset here, so it is off. |
| **The model** | n/a, because nothing the model is told changes. |
| **Cost** | n/a, because no new request, login, or token spend is added. Sign-in skips one page. |
| **Migration** | None. No records move. Turning the flag on restores the picker without a data change. |

## Behaviour

- **API** — no new routes and no change to existing ones.
- **UI** — `SHOW_CONNECTORS` defaults to false (`VITE_SHOW_CONNECTORS === "true"`).
  Off: login and signup replace the location with `/files`; `/` redirects
  there; the sidebar header shows the workspace name and is not a button;
  the account dialog has no Snowflake row and does not list Snowflake under
  "Signs in with"; the picker, if it were rendered, would omit the Snowflake
  card and the "add a connection" tile. On: today's picker, clickable
  sidebar switcher, and Snowflake rows, unchanged.
- **Chat and the model** — unchanged. A new chat on Files still asks for a
  folder. Snowflake's database list is only built when
  `workspace.kind === "snowflake"`.
- **Admin** — unchanged. Snowflake badges and labels stay on the admin pages.

## Data

Nothing is stored. No migration.

## Acceptance checks

1. With `VITE_SHOW_CONNECTORS` unset, `pnpm tsc -p tsconfig.app.json --noEmit`
   and eslint on the changed frontend files report no new errors.
2. Signing in at `/login` lands on `/files`, with no connector picker in
   between.
3. Creating an account that signs in immediately lands on `/files`.
4. A signed-in visit to `/` redirects to `/files`.
5. On `/files`, the sidebar header is not a button and shows no switch icon
   or "Switch connection" tooltip. Account (avatar menu) has no Snowflake
   row.
6. With `VITE_SHOW_CONNECTORS=true` and the dev server restarted, `/` is the
   picker again, the Snowflake card is back, and the sidebar switcher is
   clickable.

## Open questions

None. Files is the only source on this deployment, including "add a SQL
Server connection", which is hidden with the same flag.

## Notes

Invite, verify, and reset still target `/`. The index route's redirect is
what sends them to Files, so those pages did not need their own flag check.
The chat empty state still mounts the source picker on Files; that picker
renders the folder list for `kind === "files"` and the Snowflake database
list only for `kind === "snowflake"`.
