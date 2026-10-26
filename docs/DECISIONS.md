# Decisions

Why the system is the way it is. Newest first. Each entry states what was
decided, what it rules out, and what would make us revisit it.

Add an entry when a choice would otherwise have to be re-argued later, or when
someone reading the code would reasonably ask "why like this?". Do not record
what the code already says plainly.

---

## 2026-10-23 · People sign in to the app; Snowflake is only connected

**Decided.**
- Email and password, against the app's own accounts, is the only way to sign
  in. Snowflake sign-in is **removed**, not switched off: `GET /auth/login` is
  gone, and the OAuth callback finishes only a "Connect Snowflake" started from
  a signed-in account.
- The Snowflake OAuth integration stays for one thing: a signed-in person
  connecting their own Snowflake user as a data source. Their queries run as
  them, under their own role.
- In the web app, **no Snowflake query ever runs on a shared or service
  identity**. The engine's shared connection is refused at startup; a person who
  has not connected Snowflake gets *no* warehouse, and Snowflake-shaped work
  answers "connect Snowflake". Lookups that decide who may see a local record
  (SQL Server dashboards, uploads, reports, chats) read the app store alone.
- Being an administrator is decided in the app (`ADMIN_EMAILS` once the email
  is confirmed, the Users page), never by a Snowflake role and never by
  signing up. `ADMIN_ROLES` now only lets a connected session SAVE a Snowflake
  semantic layer.
- Accounts the old Snowflake sign-in made keep their ids, usernames and
  history. An administrator emails the person a link to choose a password; it
  is the same account. Nothing is linked by an unverified email claim.
- Passwords are Argon2id, verified against old scrypt hashes and upgraded at
  sign-in, with a first rollout stage (`PASSWORD_HASH_ALGORITHM=scrypt`) so the
  previous release can be rolled back to.
- Emailed links carry their one-time token in the URL fragment, so it is in no
  access log.
- Spec 0007 carries all of it out.

**Because.** Needing a configured Snowflake user just to get in was the main
friction for new people, and meaningless for a deployment with only SQL Server
or uploads. And leaving a shared Snowflake connection reachable from web
requests meant a missing per-person connection could silently run as a service
account: the one failure that widens access without anyone choosing it.

**Rules out.**
- A shared Snowflake service account standing in for people who have not
  connected.
- Widening anyone's data access to make sign-in simpler.
- Snowflake roles granting app administration.
- Making or linking an account from a Snowflake identity alone.

**Revisit if.** A client needs people to query Snowflake without their own
Snowflake user. That is administrator-managed connections, a different
trade-off (one role for everyone on the connection), and its own spec.
## 2026-09-23 · Nothing in Snowflake is touched while the demo runs

**Decided.** No Snowflake table is created, altered or dropped, no existing row
written or deleted, and nothing copied out of one. Work moves forward by
writing NEW records to the app store; what is already in Snowflake stays there
and is still read where a session has Snowflake.

**Because.** A demo is running against Snowflake and prod and dev share those
tables. A change that looks safe in dev lands in front of a customer.

**Rules out.** Migrating existing records as part of moving a feature to the
app store; "tidying" unused tables.

**Revisit if.** The demo ends, or prod and dev stop sharing tables. Retiring
the Snowflake tables is then its own deliberate piece of work, with backups,
not a side effect of a feature.

## 2026-09-23 · Records move forward, they are not migrated

**Decided.** When a kind of record moves to the app store, new ones are written
there and the old place is read as a fallback. Nothing is copied, and the old
records are left where they are.

**Because.** Copying is the risky half of a move — it writes, it can run twice,
and it can run while someone is using the thing being copied. For records we
have few of, it buys nothing.

**Revisit if.** A move involves records people would genuinely lose (Snowflake
dashboards are the case to watch). Then the copy gets its own spec, its own
acceptance checks, and a dry run.

## 2026-09-23 · A feature belongs to every connector unless it cannot

**Decided.** A capability is built for every source the app can reach, with
only the parts that genuinely differ — the SQL dialect, how a connection is
opened, how a schema is read — living in the connector. A feature is tied to
one vendor only when it depends on something that vendor alone has, and that
dependency is named in the spec.

**Because.** The product should not feel like a Snowflake app with other
databases bolted on. Every feature that grew up Snowflake-shaped (the semantic
layer, the Report Builder) then costs a second implementation, and in the
meantime a client on SQL Server gets a visibly poorer product.

**Rules out.** "Snowflake tools" as a category; hiding a capability from a
connector because it has not been tried there yet.

**Applies to.** The Report Builder, which is next: the templates, the mapping
and the workbook are the same work for any database — only the dialect and the
execution engine change. Data groups and the audit log follow.

## 2026-09-23 · A semantic layer stays inside its database's boundary

**Decided.** The Builder shows the databases of the workspace it was opened
from, and offers no way across to another connector's or another connection's
layers. Uploaded files are not offered at all: a spreadsheet's columns are
described by the definitions agreed for its shape, and there is no schema
beyond that to document.

**Because.** A warehouse's databases and a SQL Server connection's are
different places that happen to be administered by the same person. Listing
them together invites editing the wrong one, and made the page query a
warehouse nobody had asked about.

**Revisit if.** Someone genuinely needs a deployment-wide view of which
databases have layers — that is a report, not an editor, and can be its own
read-only page.

## 2026-09-23 · The app keeps its own records in SQLite

**Decided.** Everything the app knows — accounts, connections, dashboards,
semantic layers, and in time reports, data groups and the audit log — lives in
one SQLite file per deployment, at `APP_DB_PATH`, on the app's own volume.

**Because.** These records have to exist where Snowflake does not. A client
with only files and SQL Server has no warehouse to hold them, and asking their
IT to run a database server for the app's bookkeeping is a setup cost that
loses deals. SQLite needs nothing installed.

**Rules out.** A database server per client (Postgres, SQL Server) as a
requirement; app records living inside a customer's warehouse.

**Revisit if.** A deployment needs more than one app server, or writes outgrow
one file. The storage layer is an interface, so the file can become Postgres or
the client's own database without touching the features above it.

## 2026-09-23 · A feature is written once, against the connector interface

**Decided.** Anything true of every database — the semantic layer, dashboards,
entitlements, auditing — is written against `DBConnector` and
`app/api/sources.py`. Only what genuinely differs per vendor lives in a
connector package.

**Because.** The semantic layer was written against Snowflake, so SQL Server
had none, and the chat had hand-written `if source starts with…` branches in
four places. Adding a third connector meant repeating all of it.

**Rules out.** Core modules importing a vendor module, or testing a source
key's prefix themselves.

**Revisit if.** A vendor needs something the interface cannot express — then
widen the interface rather than branching around it.

## 2026-09-23 · A semantic layer belongs to a source, not to Snowflake

**Decided.** Layers are keyed by source key and stored as the TOML a person
wrote, with draft and published as separate rows and every publish kept as a
version. The files in `config/metadata/` are copied in once on first use, and
the app is the authority after that.

**Because.** Publishing a layer on a server wrote into the container, so the
next redeploy silently restored the old one. And a SQL Server layer can never
be a committed file: it names a connection that exists on one deployment only.

**Rules out.** Editing layers by hand on a server and expecting them to
survive; a layer shared between dev and prod by being in git.

**Revisit if.** People want layers reviewed in pull requests — then export and
import, which keeps the document exactly as written, rather than moving the
store back to files.

## 2026-09-23 · Who may write a layer differs per connector

**Decided.** A Snowflake layer needs a Snowflake role listed in `ADMIN_ROLES`,
and the database must be visible to that role. Every other source's layer is an
administrator's to write. Reading is open to anyone who can see the source.

**Because.** A layer describes data, so for Snowflake the person writing it
should be able to see that data. A SQL Server connection has one shared login
and no per-person role to check, so the app-level rule is the only honest one.

**Revisit if.** Per-person connections arrive; then the connection's owner is a
candidate too.

## 2026-09-23 · Dev and prod never share a store

**Decided.** Each deployment's `app_data` volume is named outright
(`dataalpha_app_data_dev`, `dataalpha_app_data_prod`) rather than left to the
stack name.

**Because.** Dashboards, layers and accounts must not leak between
environments, and relying on stack naming means one rename does it silently.

**Rules out.** Pointing both stacks at one volume to "share config".

## 2026-09-23 · A source that will not answer stops the work

**Decided.** A connector distinguishes "this SQL is wrong" from "the source is
unreachable". Bad SQL drops that panel and says why; an unreachable source
fails the whole operation with a message naming the source.

**Because.** A dashboard came back a panel short because one panel's check hit
a connection timeout. Quietly producing less turns an outage into a wrong
answer that nobody questions.

## 2026-09-23 · Connections to a slow source are kept, not reopened

**Decided.** One login per request, connections pooled between requests, proved
alive when idle, and dropped rather than reused after the server refuses
something. A rejected login is never retried.

**Because.** Measured on a real SQL Server: 3–19 seconds per login, and every
statement was opening its own. Repeated retries of a rejected login are also
how an account gets locked out.

## 2026-09-23 · Dashboard plans are stored whole, not shredded into rows

**Decided.** A dashboard version holds its plan as one JSON document. Only what
is filtered or sorted on — owner, source, title, dates — is a column.

**Because.** The AI generates and edits whole plans, and a version is a
snapshot of one. Splitting panels into rows would copy every panel on every
version and reassemble them on read, for no gain.

**Revisit if.** We need to query across plans (every dashboard using a column);
then add a derived index table beside the document, not instead of it.

## 2026-09-24 · A dashboard's record belongs to the app, not to a warehouse

**Decided.** Every dashboard — its plan, its versions, its author, the source
it reads — is a row in the app's own store, per deployment. The warehouse is
read to run a dashboard's panels and for nothing else. Existing rows are
copied once, by a script an administrator runs; nothing is written back.

**Because.** Dev and prod shared one table, so a dashboard made while testing
appeared in the list somebody was demonstrating. A client without that
warehouse could not save a dashboard at all. And reading a record — which is
the app's own metadata, not the customer's data — cost a round trip and,
on a cold connection, a login.

**Revisit if.** Never for the direction. The table itself is dropped only after
prod has run on the app store long enough to trust it, as its own decision.

## 2026-09-24 · Where a dashboard came from is recorded, not re-derived

**Decided.** A dashboard stores the kind of source it reads at save time. Code
that needs it reads the column; working it out from the key's prefix is the
fallback for records written before there was a column.

**Because.** Deriving it on every read meant one function decided the answer
for every dashboard ever made, so it could only say what that function knew —
and a change to a prefix would silently relabel history.

## 2026-09-24 · Entitlements are the app's own, and span connectors

**Decided.** Groups, their members and what they hold are rows in the app's
store, per deployment. The model is unchanged: access is inherited from a
resource's source, the first assigned link in a chain decides, and ungrouped
is visible. Existing rules are copied once; the warehouse tables are read and
never written.

**Because.** Rules inside one connector cannot govern the others — a "Fintech"
group covering a Snowflake database, a SQL Server database and an uploads
folder is only expressible above them. A deployment without that warehouse had
no entitlements at all, and reading the rules could refuse a request that never
touched the warehouse.

**Revisit if.** Membership needs to mean more than one person at a time — a
team, an app-level one. Settled the same day: a member is a person, never a
warehouse role. See below.

## 2026-09-24 · A group holds people, and any database

**Decided.** A data group's members are people — accounts here — and never
warehouse roles. What it can hold is every database the deployment can reach,
whatever serves it, and everything it holds is hidden from non-members the
same way.

**Because.** A warehouse role could only ever match somebody who signed in
with that warehouse, so the option was useless to half the people it was
offered to, and an organisation with several databases has no single set of
roles to key off. And a group that cannot hold a SQL Server database cannot
govern its dashboards: they fall through to ungrouped one at a time, which
turns filing sources into filing artefacts.

**Note.** This takes nothing away. A role row enforced nothing in the
warehouse — it was a shorthand for writing membership. The person's own
account does the same job for every way of signing in.

## 2026-09-24 · Never connected is not the same as connected and broken

**Decided.** An account that has never linked a warehouse gets an empty list
from anything warehouse-shaped — databases, sources, dashboards. The 409
"Connect your Snowflake account" is kept only for an account that HAS linked
one and whose stored token no longer works. The check is a local read of the
stored link, never an attempt to connect.

**Because.** A person who signs in with a password, on a deployment that may
have no warehouse at all, was shown an error about connecting Snowflake on an
ordinary visit. It reads as a failure, and it is not one — they have no
Snowflake databases, which is a fact. The page that offers to connect one goes
by the account's own link, not by that call failing.

**Note.** This reverses an earlier decision that the 409 was "something the app
can act on". Nothing acted on it.

