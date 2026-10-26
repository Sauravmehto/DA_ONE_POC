# 0006 — A group holds people, and any database

**Status:** done (2026-09-24)
**Touches:** data groups (membership and what can be filed), the connections
list, the data-groups admin page

## Why

Spec 0005 moved entitlements into the app store and deliberately changed
nothing about the model. Using it immediately showed two things the model had
inherited from living inside a warehouse, and one gap that made it look like a
Snowflake feature:

- **A member could be a warehouse role.** That made sense when the rules lived
  in the warehouse and its role names were the only names the app had. An
  organisation with more than one database has no single set of roles to key
  off, and somebody who signs in with a password has none at all — so the
  option was a source of confusion that could never work for half the people
  it was offered to.
- **The page called a member a "Snowflake user".** It is the person's account
  here. The field behind it is named `snowflake_user` for historical reasons
  and holds the app username for a password account, so the label was wrong
  rather than merely dated.
- **A SQL Server database could not be filed.** The list of databases on the
  page came from the semantic layers, which today are all Snowflake's. So the
  one SQL Server database in this deployment could not be put in a group, its
  dashboard showed as Ungrouped, and *every future dashboard on it would be
  ungrouped too* — an administrator would have been filing artefacts one at a
  time, which is the thing the inheritance model exists to prevent.
- **And it was not filtered either.** The connections list that builds the
  source picker had no group check, so a group could hold a SQL Server
  database and still not hide it: a rule that looks enforced and is not.

## Not in scope

- **Groups of people.** If listing forty names becomes tedious, a team is a
  thing to add then — an app-level one, not a warehouse role. Nothing here
  blocks it.
- **`ADMIN_ROLES`.** Writing a *Snowflake* source's semantic layer still asks
  for a Snowflake role, which is a warehouse permission and a separate
  question.
- **The `snowflake_user` field name.** Renaming it through the session, the
  audit trail and every record that stores an author is a rename with no
  behaviour in it — the cleanup spec.

## The checklist

| | |
|---|---|
| **Reach** — how does someone get to this, from every workspace and every connector? | The same admin page, with one fewer control and a longer list of databases. Every connector's databases can now be filed, so the model reaches every workspace rather than only the warehouse's. |
| **Permission** — who may do it, and what does everyone else see instead? | Unchanged: an administrator files and un-files, everyone else sees what their groups allow. Dropping role membership can only ever show a person *less*, never more. |
| **Failure** — what happens when the source is slow, down, or refuses? | A connection that will not answer contributes no databases to the page rather than emptying it, as it already did for the picker. Filtering is a local decision and cannot fail for want of a server. |
| **Isolation** — what is scoped per workspace, per connection, per deployment, and what survives a redeploy? | Unchanged from 0005: per deployment, in the app store. A group deliberately spans connectors — that is what makes one "Credit Analyst" group able to hold a warehouse database, a SQL Server database and two folders. |
| **The model** — what does the AI get, and when? | Nothing new. The chat is filtered through the same `Access`. |
| **Cost** | None. Both lists were already being built; one gained rows, the other gained a filter over rows it already had. |
| **Migration** | Membership rows naming a role stop counting. This deployment has none — its only member is a person — so nothing changes here. The copy from the warehouse reports any it finds and does not bring them, so an administrator knows to add those people by name. That direction is fail-closed: somebody sees less, never more. |

## Behaviour

### A member is a person

`MEMBER_TYPES` is `("USER",)`. `groups_of()` takes the person and nothing else.
A stored row naming a role matches nobody, and `normalise_member` refuses one
with "a group holds people".

The admin page drops the type selector: you type a name and add it. The label
is "User name".

### A group holds any database

The page's database list is every database the deployment can reach — the
warehouse's, and each saved SQL Server connection's, by the same source key
the rest of the app uses (`SQLSERVER:<connection>/<database>`). `_DEFAULT` is
no longer offered: it is a fallback config file, not a database.

### And hides it

`GET /connections` filters each connection's databases through
`access.allows([("database", source_key)])`, the same check the warehouse's
database list has always used. The connection itself stays listed, so it can
still be seen and managed.

### Ungrouped stays visible

Settled rather than changed: a resource in no group is visible to everyone.
Locking something down means filing it; filing two databases and nothing else
is a complete job. The consequence to know is that "only my group's things"
requires everything else to be filed too.

## Acceptance checks

1. **Done.** A group's membership is people only:
   `test_membership_is_the_person_and_nothing_else`, and
   `test_a_warehouse_role_is_not_a_member` holds that a stored role row counts
   for nothing and a new one is refused.
2. **Done.** A SQL Server database appears in what a group can hold, can be
   filed, and its dashboards then inherit that group —
   `test_a_sql_server_database_can_be_filed_and_is_filtered`.
3. **Done.** The same database is hidden from somebody outside the group in
   the connections list — `test_a_sql_server_database_is_hidden_like_any_other`.
4. **Done.** `_DEFAULT` is not offered as a database.
5. **Done.** An ungrouped source stays visible —
   `test_an_ungrouped_source_stays_visible`, asserted rather than assumed.
6. **Done.** The admin page offers no role option and says "User".

## Notes

Checked against the deployment afterwards: the page now offers 13 databases —
12 warehouse and `FINTECH_DB (DA SQL Server)` — where it offered 12 plus
`_DEFAULT` before.

Worth recording for whoever reads this next: dropping role membership was not
a reduction in what the app can express. Nothing enforced anything in the
warehouse through those rows; they were a shorthand for writing membership,
matched against the session's active role. What replaced it is the person's
own account, which works the same whether they signed in with a password or
with Snowflake.
