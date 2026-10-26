# Real templates, for measuring the scanner

Put real report templates here — formula-heavy, mixed, form-shaped — with any
client identifiers removed. They are the evidence spec 0003 is judged against:
what the scanner says about each, and what is actually true of it.

One `<name>.truth.json` beside each workbook records what a person confirmed:

```json
{
  "data_sheet": "Data",
  "regenerates": ["Summary!D12", "Data!F3:F40"],
  "must_not_touch": ["Summary!B7"],
  "notes": "B7 is pinned to last year's close on purpose."
}
```

Without a truth file a workbook is still useful — it shows what the scanner
does — but it cannot say whether that was right.
