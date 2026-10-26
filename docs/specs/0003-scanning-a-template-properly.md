# 0003 — A template is read properly, whatever shape it is in

**Status:** rules done (2026-09-24); model assistance deferred — see Notes
**Touches:** the Report Builder's template scanner, the approval step, the app
store

## Why

The Report Builder can only regenerate what it can prove it understands. Today
it proves that with a fixed rule set: a formula is safe to re-extend if it is
SUM, SUMIF(S), COUNTIF(S), AVERAGEIF or SUMPRODUCT over a range, or if it sits
in a single-column run where each cell is the one above shifted by exactly one
row. Everything else is flagged for a person.

That instinct is right — silently rewriting a formula it misread would produce
a report whose totals are wrong and whose wrongness nobody can see. But the
rules are narrow enough that a real workbook arrives as a wall of flags:

- a template built on INDEX/MATCH, VLOOKUP, XLOOKUP, IFERROR or an array
  formula has every one of those cells flagged, because the function is not on
  the list;
- a fill-down that spans two columns, or repeats every second row, is not
  recognised as one block, so each cell is flagged separately;
- header detection looks for a header row, which a form-shaped sheet — line
  items down column A, values in B, sections interleaved — does not have;
- the fingerprint covers sheet names, so renaming a tab makes an approved
  template unrecognised and it has to be approved again.

The result is that the person approving stops reading, which defeats the gate.
"Any workbook is parsed properly" is the requirement; today it is "a narrow
class of workbook is parsed confidently, and the rest is handed back".

## Not in scope

- **The upload path.** How a spreadsheet's *data* is profiled and mapped
  (`excel_profiler`, `excel_mapping`) is a different question and is working.
- **Writing the report's SQL.** Still typed by a person; spec 0002 settled
  that.
- **Removing the human approval.** It stays. This is about what the person is
  asked to approve, not whether they are asked.

## The checklist

| | |
|---|---|
| **Reach** | No new surface. The same scan → approve → bind flow, in every workspace, with better answers and fewer flags to wade through. |
| **Permission** | Unchanged: approving a template needs an administrator or a Snowflake role in ADMIN_ROLES. A model's proposal is never authority — it is what the person is shown. |
| **Failure** | Three kinds, kept apart. A formula the rules settle is stated as fact. A formula the model interprets is shown as a proposal with its evidence, and defaults to "not regenerated" if nobody accepts it. A workbook that cannot be read at all (corrupt, password-protected, no formulas) says so plainly. Nothing is ever auto-rewritten on the model's word alone. |
| **Isolation** | Scans, interpretations and approvals are records in the app store, per deployment. A template approved in dev means nothing in prod. |
| **The model** | It reads the structural evidence the scanner already produces — the formula, the shape with row numbers normalised, the cells around it, the sheet's role, what the neighbouring cells do — and answers one question per case: can this be re-extended to a different row count, and if so how. It does not see the data, and never rewrites a cell directly. |
| **Cost** | One call per template, batching every unsettled case, not one per cell. A template with 400 flagged cells is one call of a few thousand tokens, once, at approval time — nothing at run time. Reports regenerate with zero model calls, as they do now. |
| **Migration** | None. Templates approved before this keep their stored classification and keep working. A re-scan of the same workbook gets the new treatment. |

## The measurement comes first

Nothing here ships on a hunch. The first piece of work is a harness:

1. A set of **real templates** — formula-heavy, mixed, form-shaped — kept in
   `backend/test_fixtures/templates/` with any client identifiers removed.
2. For each one, what the scanner says today: tier counts, which cells are
   flagged, what the fingerprint is.
3. For each one, **what is actually true**, recorded once by a person: which
   cells genuinely re-extend with the row count, which must not be touched, and
   which sheet is the data sheet.
4. A report comparing the two, so any change can be judged: cells correctly
   auto-regenerated, cells wrongly auto-regenerated (the number that must stay
   zero), cells needlessly flagged, sheets misclassified.

That report is the acceptance criterion for everything below. Without it
"better" is an opinion.

## Behaviour

### 1. Wider structural rules, before any model

Some of the gap is rules the scanner could settle itself, and a rule is better
than a model call when it exists:

- **Wrapped aggregates.** `IFERROR(SUM(B3:B40), 0)` and `ROUND(SUM(...), 2)`
  are aggregates over a range with a wrapper around them; the wrapper does not
  change how the range re-extends.
- **Multi-column fill-downs.** A block is currently one column. A run of the
  same shape across two or three adjacent columns, each shifting by one row, is
  the same fill-down and should be one block.
- **Stepped runs.** A pattern repeating every second or third row is still a
  pattern; the step is the thing to record, not a reason to flag.
- **Whole-column and structured references** (`B:B`, `Table1[Amount]`) re-extend
  by definition — nothing to rewrite.

Each of these is a rule with a test, and each one removes flags that the model
would otherwise be asked about.

### 2. Model assistance for what the rules cannot settle

What remains — lookups, cross-sheet references, hand-wired formulas, unusual
functions — goes to the model **in one batch per template**, with the
structural facts as evidence: the formula, its normalised shape, its
neighbours, the sheet's role, and what the scanner already decided about
similar cells.

It answers, per case: does this re-extend with the row count; if so, by which
rule; if not, why not; and how confident. Then:

- a case it settles becomes a **proposal**, shown to the approver with its
  reasoning and the cells it covers, defaulting to off;
- a case it will not settle stays flagged exactly as today;
- a case the rules already settled is never sent, and never overridden.

### 3. What the approver sees

The approval step stops being a list of flags. It shows, in order: what will be
regenerated and why, what the model proposes with reasoning (accept or leave),
and what will be left alone. The counts at the top say how much of the workbook
is understood — which is the number that tells someone whether to trust it.

### 4. A template survives being renamed

The fingerprint stops including sheet names, or keeps them as a weak signal
beside the formula shapes. Renaming a tab should not throw away an approval.

## Data

Scans and approvals become records rather than a process cache, so the
decisions a person made are kept:

```sql
CREATE TABLE template_scans (
    scan_id        TEXT PRIMARY KEY,
    fingerprint    TEXT NOT NULL,
    template_name  TEXT,
    classification TEXT NOT NULL,   -- what the scanner said, as JSON
    proposals      TEXT,            -- what the model proposed, as JSON
    decisions      TEXT,            -- what the person accepted, as JSON
    created_by     TEXT,
    created_at     TEXT NOT NULL
);
```

The accepted decisions are what a later scan of the same shape starts from —
which is also how the thing learns, without any training.

## Acceptance checks

1. **Done.** The harness is `scripts/scan_templates.py`, over the real
   templates in `backend/test_fixtures/templates/`.
2. **Done.** No cell is auto-regenerated that should not be: a formula holding
   a reference pinned to one record, or a range read by position, is refused
   however clean the aggregate beside it. A test asserts it for every template
   in the set, and this is the check that cannot be traded for any other.
3. **Done, measured.** Flags fell from 175 to 25 on the servicer report and
   from 32 to 5 on the risk dashboard — and everything still flagged is a
   pinned record, held by a test that fails if a new category appears.
4. **Done, and it was worse than described.** A template's data sheet ships
   nearly empty because it is refilled at run time — the servicer report has
   two sample rows under 234 columns — and the rule demanded three, so the
   sheet the whole report fills was classified as a parameter sheet and could
   not be bound at all. A wide header of distinct labels now makes a table; a
   narrow label/value sheet still does not.
5. **Done.** A fill-down may now step by up to three rows, and adjacent
   columns covering the same rows are one region — which also lets a region
   two rows tall be recognised, where one column that short cannot. Neither
   real template needed it: both still find 13 and 46 blocks, unchanged.
6. **Done.** `IFERROR(SUM(range), 0)`, `ROUND(SUM(range), 2)` and
   `IF(SUM(range)>x, …)` are the aggregates they are.
7. **Done.** A second hash of the same workbook, with the sheets' names
   replaced by their positions, is matched when the exact one misses. Renaming
   a tab no longer sends a template back for approval.
8. **Not applicable yet** — there are no model proposals; see the Notes.
9. **Not applicable yet**, and the reason is the point: no model call is made
   at all.
10. **Done.** A template approved before this change keeps its stored
    classification; only a re-scan sees the new rules. The shape hash is
    stored beside the exact one, never instead of it, so a template approved
    before it existed is still found the way it always was.

## Open questions

1. **Which model?** Recommendation: the cheap one first (Haiku or Sonnet) with
   the harness to say whether the expensive one is worth it. This is structured
   reasoning over small evidence, not a writing task.
2. **Does a rejected proposal get remembered?** Recommendation: yes — "the
   person said no to this shape" is the most valuable signal in the system, and
   it is the start of not asking again.
3. **How much of a workbook must be understood before it can be approved at
   all?** Recommendation: no threshold. A person may approve a template where
   most cells are left static; that is a legitimate report.

## Notes

**Built 2026-09-24 — rules only, and the model step is deferred.**

Two real templates were measured before anything changed, and the baseline
said something the spec had not: the flags were not lookups. On the servicer
report, 150 of 175 flagged cells contained SUMIFS — a safe aggregate — and were
refused for also mentioning `EDATE` or `IFERROR`. On the risk dashboard, 27 of
32 wrapped SUM or AVERAGEIF in `IFERROR`. So ~85% of what a reviewer was asked
to read was a rule problem, not a judgement problem.

| | servicer report | risk dashboard |
|---|---|---|
| auto-regenerated | 0 → 150 | 53 → 80 |
| flagged | 175 → 25 | 32 → 5 |
| data sheet found | no → yes | yes |

**What the model was going to be for, the rules now settle.** Everything still
flagged on both templates is a reference pinned to one record — which genuinely
wants a person, and which a model should not be asked to wave through. So the
model assistance in this spec is deferred, not cancelled: the test
`test_every_flagged_cell_in_a_real_template_deserves_it` fails the moment a
template produces a flag that is neither pinned nor positional, and that is the
signal that a model has something to add. A template leaning on VLOOKUP or
INDEX/MATCH is the case to look for.

**The harness earned its place immediately.** The first version of the range
rule took the risk dashboard to zero flags — including five formulas like
`IFERROR(LOAN_DATA!L6/SUM(LOAN_DATA!L3:L7),0)`, pinned to a single record.
That is precisely the silent wrong number this feature exists to avoid, and
only measuring against real files caught it.

**Checks 5 and 7 built 2026-09-24, after the rest.**

*A fill-down that skips rows.* A run was a set of cells in one column sitting
on consecutive rows. A layout that prints one record every second row — a
spacer beneath each, a sub-line — broke into single cells and became one flag
each. A run now holds a constant step of up to three rows; past three a gap is
not a layout, it is two cells that happen to rhyme. Two things are refused
that were not obviously going to be: a step that changes halfway (two layouts
meeting, not one block), and a run advancing more than one record per cell,
which regenerated against a row count would read past the end of the data.
The rule that a jumbled run is refused *whole*, rather than split into the
part that happens to be clean, is kept — splitting is how hand-wiring gets
approved.

*A fill-down across columns.* Adjacent columns covering the same rows with the
same step are marked as one region. They are grouped, not merged: adjacent
columns of a region rarely compute the same thing, so each keeps its own
formula and the count says one region instead of four. That also settles a
case one column could never settle alone — two rows repeated across three or
more columns is a pattern somebody filled, where two rows in a single column
is a coincidence.

*A renamed tab.* The fingerprint covered sheet names, which renaming changes
in two places at once: the sheet list, and every formula reading across to
that sheet. So renaming a tab produced a template nobody had approved, and a
person had to read a workbook that had not changed. A second hash now replaces
each sheet name with its position and is matched only when the exact one
misses. Position, not absence: swapping two tabs is still a different shape.
Stored in a new column beside the exact hash — every template approved before
this has none, and is found exactly as it was.

The two real templates measure identically to the baseline above: 150/13/25
and 80/46/5. That is the result to expect, since the Notes already said
neither needed these rules. What they buy is the next template, and what they
cost is nothing so far.

Still not built: model assistance (checks 8 and 9), the approval screen that
shows proposals rather than a list of flags (§3), and `template_scans` —
nothing needs that table until proposals exist.
