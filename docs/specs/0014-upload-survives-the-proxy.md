# 0013 — An upload finishes even when the proxy will not wait

**Status:** done (2026-10-29)
**Touches:** file upload, the library page, the app store

## Why

On the hosted site, uploading a file goes through Netlify to Render. Netlify
gives up on a proxied request after about 26 seconds, and Render closes a
connection that sends no data for a short while. The page then shows Render's
"Inactivity Timeout" HTML under "Could not read these spreadsheets", and the
files never arrive.

## Not in scope

- Raising Netlify's or Render's own timeout. Those are not settings this app has.
- Changing what a successfully read file becomes.

## The checklist

| | |
|---|---|
| **Reach** | The library upload, on `/files/library`, for a folder or for individual files. Local development uses the same path. |
| **Permission** | Unchanged. Only the signed-in person who started an upload can ask how it is going. |
| **Failure** | A selection with nothing readable is still a 400 before a job exists. A failure while reading is reported on the job, and the page says "Upload failed" with that reason. A proxy timeout is a sentence about the connection, not the timeout page's HTML. |
| **Isolation** | The job row is in the app store, so any worker can answer it. The reading itself runs on the worker that accepted the bytes. |
| **The model** | Unchanged. It still runs once per spreadsheet shape, just after the upload request has already returned. |
| **Cost** | The upload request itself is short. The browser then makes one small status request a second until reading finishes. |
| **Migration** | One new `upload_jobs` table. Nothing already stored moves. |

## Behaviour

- **API** — `POST /api/v1/excel-sources/upload` returns `{job_id}` once the bytes are accepted. `GET /api/v1/excel-sources/uploads/{job_id}` returns `running` with a percent and a stage, then `complete` with the same batch result the stream used to send, or `failed` with a detail. `GET /api/v1/excel-sources/ping` is a cheap wake-up.
- **UI** — The library calls ping first, then uploads, then polls. The progress line shows the stage. A cut-off connection does not render an HTML error page.

## Data

`upload_jobs(job_id, owner, status, percent, stage, result_json, detail, created_at)`.

## Acceptance checks

1. `POST /upload` of a readable file returns `{job_id}` without waiting for reading to finish.
2. Polling that job reaches `complete` with a result, and another account gets 404 for it.
3. A 504 whose body is the inactivity-timeout HTML is shown as a short sentence, under "Upload failed".

## Open questions

None.

## Notes
