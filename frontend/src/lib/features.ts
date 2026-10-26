// Report Builder is opt-in per environment rather than a constant someone has
// to remember to flip back before shipping. Set VITE_SHOW_REPORT_BUILDER=true
// in frontend/.env locally, or pass it as a BUILD ARG for an image — Vite
// inlines VITE_* at build time, so a runtime container variable does nothing.
//
// This hides the LINKS, not the feature: /tools/report-builder still resolves
// for anyone with the URL. Saving from it needs a Snowflake role in
// ADMIN_ROLES, which the server checks.
export const SHOW_REPORT_BUILDER = import.meta.env.VITE_SHOW_REPORT_BUILDER === "true"
