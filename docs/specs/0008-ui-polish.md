# 0008 — A calmer, more professional interface

**Status:** approved (2026-10-24); phases 1-3 built, awaiting the owner's review before phase 4
**Touches:** the frontend's look and motion only: global styles, shared
components, sign-in and account pages, the Connect page, the app shell and chat,
the Files library, dashboards

## Why

The app works but looks like an unstyled template: every surface is neutral grey,
every button near-black, nothing but the sidebar moves, and the sign-in, sign-up,
reset and invite pages look like they belong to different products. The sign-in
page's left panel is half empty because its image carousel has no images. The
"Connect to your data" cards are identical white boxes, the Files library's
upload area is a small dashed rectangle with plain "Loading…" text, and
Dashboards is a dense admin table. The owner asked for a more professional,
engaging interface with animation.

## Not in scope

- **Behaviour.** No change to routes, data fetching, forms' fields and handlers,
  sign-in, rate limiting, permissions or anything else spec 0007 built.
- **Real GIFs, photography or video.** Animation is code (CSS and SVG). Real
  screenshots can be dropped into `src/assets/showcase/` later and the existing
  carousel shows them.
- **A new animation library.** CSS first; revisit only for drag-reorder or
  shared-element animation.
- **Restructuring the very large files** (`excel-source.tsx`, `dashboards.tsx`,
  `dashboard-panel.tsx`): class and markup edits only, never state or effects.
- **A disconnect confirmation, new fonts, a logo change, i18n,** and the 37 lint
  problems that already exist.

## The checklist

| | |
|---|---|
| **Reach** | Every screen, in light and dark, desktop and phone width. A visible theme toggle (today only a hidden `d` key) on the sign-in page, the Connect page and the app header. |
| **Permission** | Unchanged. Nothing is shown or hidden differently by role. |
| **Failure** | Every existing loading, empty and error state is kept and restyled; none is removed. A failed animation must never hide content: motion is added to elements that are already in the DOM. |
| **Isolation** | n/a: no data is stored. The theme choice is the existing localStorage key. |
| **The model** | n/a. |
| **Cost** | No new runtime dependency. Animate only `transform` and `opacity`. The sign-in tour is lazy-loaded. Build size is compared before and after each phase. |
| **Migration** | n/a. Reversible by reverting the files; no data changes. |

## Behaviour

- **Colour.** A blue accent (the existing `#4175B5` family) for focus rings, links,
  the active menu item, progress and illustration. Primary buttons stay near-black,
  except the sign-in / sign-up submit and the Connect card actions. Success is
  teal. Neutrals get a very slight cool tint. Light and dark both stay AA.
- **Motion.** Entrances 300-400 ms, interactions 120-200 ms. **Reduced motion is
  honoured globally**, with spinners the one exception (they carry meaning).
- **Phases**, each shippable and checked in a real browser (light and dark, phone
  width, keyboard) before the next:
  1. Foundation: tokens, motion utilities, shared components, theme toggle.
  2. Sign-in and every account page share one branded layout.
  3. The Connect page.
  4. App shell, chat, account dialog.
  5. Files library.
  6. Dashboards.

## Acceptance checks

1. `pnpm typecheck` and `pnpm build` pass after every phase; the main chunk's size
   change is reported.
2. `pnpm lint` on touched files adds no problems (37 exist today, none in these
   files' new code).
3. Sign-in, sign-up, forgot, reset, verify and invite still work end to end, with
   the same field names, `autocomplete` values and error messages.
4. With the OS "reduce motion" setting on, nothing animates except spinners.
5. Every screen is legible and has visible focus in light and dark at 390, 768
   and 1280 px.
6. The existing backend suite still passes (the backend is untouched).

## Open questions

1. **Suggested prompts in the chat's empty state** use four generic placeholders
   until the owner supplies private-credit ones.

## Notes

**Phases 1-3 built (2026-10-24).**

- **Phase 1, foundation.** Brand, status, shadow and easing tokens in `index.css`;
  a global reduced-motion rule (spinners exempt, measured: with the setting on,
  reveal/float/pulse collapse to one instant frame and the spinner keeps its 1 s
  loop); `Reveal`/`Stagger`, `PageHeader`, `EmptyState`, `StatusBadge`,
  `ThemeToggle`, `Spinner`, three SVG illustrations. The toggle is on the
  sign-in page, the Connect page and the app header. `progress`/`collapsible`
  and the count-up/in-view hooks were NOT built yet: they are created in the
  phase that first uses them (5 and 6) rather than shipped unused.
- **Phase 2, sign-in and account pages.** `AuthLayout` + `BrandPanel` shared by
  sign-in, sign-up, forgot, reset, verify and invite; a coded product tour
  (lazy, 3.5 kB) replaces the empty carousel panel and yields to real
  screenshots in `assets/showcase/`; `PasswordInput` (show/hide, Caps Lock),
  advisory `PasswordStrength`, one `FormNotice`, "Back to sign in" on every dead end.
  A `brand` button variant was added.
- **Phase 3, Connect page.** `ConnectorCard` with a status badge, hover lift,
  staggered entrance and matching skeletons; delete moved to a "..." menu that
  opens the existing dialog; only "Connect Snowflake" is blue.

**Verified, in a real browser (Edge via playwright-core, outside the repo, against
an isolated backend and database):** light, dark and 390 px screenshots of each
screen; 19 sign-in/sign-up/forgot/theme flows and 11 Connect-page flows pass
(wrong password, show/hide, Enter submits, mismatched and common passwords,
sign-up straight into the app, forgot then back, theme survives reload, delete
menu/dialog, Open, Add dialog, non-administrator view). `pnpm typecheck` and
`pnpm build` pass; main bundle +11.5 kB (+3.4 kB gzip); `eslint` is clean on every
file touched (the one problem in `ui/button.tsx` was there before).

**A real bug found by driving the page, and fixed:** opening the delete
confirmation from the "..." menu left the page with `pointer-events: none` after
the dialog closed, so nothing on the Connect page could be clicked until a
reload. The menu is now non-modal (Radix issue #1241). A screenshot would never
have shown it.

**Not verified:** the in-motion feel (checked by sampled frames and computed
styles, not by eye in real time), Safari and Firefox (Chromium/Edge only), a
screen reader, and the pages for phases 4-6, which are unchanged so far.
