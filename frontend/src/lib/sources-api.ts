import { api } from "@/lib/api"

/**
 * The databases a person can work on, from every connector.
 *
 * One list for anything that has to offer a choice of database — the semantic
 * layer editor, the Report Builder — so none of them works out for itself
 * which connector serves what, or who may write where.
 */

export type Source = {
  /** A warehouse database name, or SQLSERVER:<connection>/<database>. */
  key: string
  display_name: string
  connector: string
  /** Whether this person may author against it. The rule differs per connector. */
  can_edit: boolean
}

/** `scope` limits the list to one workspace's own databases. */
export type SourceScope = { connector?: string; connection?: string }

export async function fetchSources(scope: SourceScope = {}): Promise<Source[]> {
  const query = new URLSearchParams()
  if (scope.connector) query.set("connector", scope.connector)
  if (scope.connection) query.set("connection", scope.connection)
  const suffix = query.toString() ? `?${query}` : ""
  return api<Source[]>(`/sources${suffix}`)
}

/** What to call a connector on screen. */
export const CONNECTOR_LABELS: Record<string, string> = {
  snowflake: "Snowflake",
  sqlserver: "SQL Server",
}
