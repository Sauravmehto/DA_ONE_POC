# 0012 — Typing on an empty Files chat opens the library

**Status:** done (2026-10-29)
**Touches:** the Files chat empty state, the message box

## Why

On `/files`, a new chat shows a folder list and a message box that will not
accept typing until a folder is chosen. The person already knows they want
their files. The box should take a message, and Enter or the send button
should open the library at `/files/library`.

## Not in scope

- Sending that typed text as a chat message, or carrying it onto the library page.
- Snowflake or SQL Server chats. Those still require a database before the box enables.
- A Files chat that already has a folder selected. Enter and the button still send the message.

## The checklist

| | |
|---|---|
| **Reach** | The empty chat on `/files`, before a folder is selected. Other workspaces are unchanged. |
| **Permission** | Unchanged. The library is the same page the "Upload or manage files" link already opens for a signed-in person. |
| **Failure** | An empty box does not navigate: Enter and the button stay inactive until there is text, which is how the composer already works. |
| **Isolation** | n/a, because nothing is stored. |
| **The model** | n/a, because no message is sent. |
| **Cost** | n/a. One client-side navigation. |
| **Migration** | None. |

## Behaviour

- **UI** — On a Files chat with no folder selected, the textarea is enabled. Its placeholder is "Type a message, then press Enter…". Enter (without Shift) or the send button navigates to `/files/library`. After a folder is selected, the box sends the chat as before.
- **API** — no change.
- **Chat and the model** — the typed text is discarded on the redirect. It is not sent to the model.
- **Admin** — no change.

## Data

Nothing is stored.

## Acceptance checks

1. On `/files` with no folder selected, the message box accepts text.
2. Enter, or the send button, with non-empty text, lands on `/files/library`.
3. An empty box does not navigate.
4. After a folder is selected, Enter still sends the chat and does not open the library.

## Open questions

None.

## Notes
