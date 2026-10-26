# <number> — <what this delivers, in plain words>

**Status:** draft · approved · building · done (date)
**Touches:** <areas, e.g. dashboards, SQL Server connector, admin UI>

## Why

What is wrong or missing today, in terms of what a person cannot do. One
paragraph. If this is only "because it would be tidier", say so — that is a
fair reason, stated honestly.

## Not in scope

What this deliberately leaves alone, and which spec picks it up.

## The checklist

Filled in before any code is written. A blank row is the spec saying "I have
not thought about this yet", not "this does not apply" — write "n/a, because…"
when it genuinely does not.

| | |
|---|---|
| **Reach** — how does someone get to this, from every workspace and every connector? | |
| **Permission** — who may do it, and what does everyone else see instead? | |
| **Failure** — what happens when the source is slow, down, or refuses? | |
| **Isolation** — what is scoped per workspace, per connection, per deployment, and what survives a redeploy? | |
| **The model** — what does the AI get, and when? | |
| **Cost** — round trips, logins, tokens, money. | |
| **Migration** — what already exists in the old place, and how does it move? | |

## Behaviour

What the system does, by surface. Written so the acceptance checks below can
be read straight off it.

- **API** — routes, who they answer, what they return.
- **UI** — where it appears, what it says, what it looks like when empty,
  loading, refused or broken.
- **Chat and the model** — what changes in what the model is told.
- **Admin** — settings, and what an administrator sees that others do not.

## Data

What is stored, where, and in what shape. Migrations. What happens to records
that already exist.

## Acceptance checks

Numbered, each one verifiable by a test or by a named check against the
running app. This is what "done" means; nothing ships with one unticked
unless the spec is amended to say why.

1.
2.

## Open questions

Anything that needs a decision before or during the build, with a
recommendation. Cleared before the spec is approved, or carried with a named
owner.

## Notes

Changes made during the build, and why. Kept in the same commit as the change,
so the spec never describes something the code does not do.
