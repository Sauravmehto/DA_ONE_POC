import type { SavedDashboardSummary } from "@/lib/api"
import { isCollectionName, isSpreadsheetName } from "@/lib/excel-source-api"
import { isSqlServerName, parseSqlServerName } from "@/lib/connections-api"

/**
 * A workspace is one connection: Snowflake, one saved SQL Server connection, or
 * uploaded files. Everything inside it — chats, dashboards, reports — belongs to
 * that connection alone, and nothing from another connection is shown there.
 *
 * Which workspace a thing belongs to is read from the source name it already
 * carries: a conversation's `database`, a dashboard's `source_type` and
 * `source_key`. Nothing new is stored.
 */
export type Workspace =
  | { kind: "snowflake"; base: string }
  | { kind: "files"; base: string }
  | { kind: "sqlserver"; connectionId: string; base: string }

export const SNOWFLAKE_WORKSPACE: Workspace = { kind: "snowflake", base: "/snowflake" }
export const FILES_WORKSPACE: Workspace = { kind: "files", base: "/files" }

export function sqlServerWorkspace(connectionId: string): Workspace {
  return {
    kind: "sqlserver",
    connectionId,
    base: `/sqlserver/${encodeURIComponent(connectionId)}`,
  }
}

/** Where each page of a workspace lives. */
export function workspacePaths(workspace: Workspace) {
  const base = workspace.base
  return {
    chat: base,
    dashboards: `${base}/dashboards`,
    dashboard: (reportId: string) => `${base}/dashboards/${encodeURIComponent(reportId)}`,
    /** Snowflake only. */
    explore: `${base}/explore`,
    /** Snowflake only: Excel reports run against the warehouse. */
    reports: `${base}/reports`,
    /** Files only: the upload library. */
    library: `${base}/library`,
  }
}

/** The workspace a chat source name belongs to. */
export function workspaceForSource(name: string | null | undefined): Workspace {
  if (!name) return SNOWFLAKE_WORKSPACE
  if (isSqlServerName(name)) {
    const parsed = parseSqlServerName(name)
    return parsed ? sqlServerWorkspace(parsed.connectionId) : SNOWFLAKE_WORKSPACE
  }
  if (isSpreadsheetName(name) || isCollectionName(name)) return FILES_WORKSPACE
  return SNOWFLAKE_WORKSPACE
}

export function sameWorkspace(a: Workspace, b: Workspace): boolean {
  if (a.kind !== b.kind) return false
  return a.kind !== "sqlserver" || a.connectionId === (b as { connectionId: string }).connectionId
}

/** Whether a conversation, by its source name, belongs in `workspace`. */
export function sourceBelongs(workspace: Workspace, name: string | null | undefined): boolean {
  return sameWorkspace(workspace, workspaceForSource(name))
}

/** The workspace a saved dashboard belongs to. */
export function workspaceForDashboard(
  dashboard: Pick<SavedDashboardSummary, "source_type" | "source_key">
): Workspace {
  if (dashboard.source_type === "sqlserver" || dashboard.source_type === "file") {
    return workspaceForSource(dashboard.source_key)
  }
  return SNOWFLAKE_WORKSPACE
}

export function dashboardBelongs(
  workspace: Workspace,
  dashboard: Pick<SavedDashboardSummary, "source_type" | "source_key">
): boolean {
  return sameWorkspace(workspace, workspaceForDashboard(dashboard))
}
