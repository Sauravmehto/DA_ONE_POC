import { api, type DatabaseSummary } from "@/lib/api"

/** A database on a saved connection, as a chat or dashboard names its source. */
const SQLSERVER_PREFIX = "SQLSERVER:"

export function sqlServerSourceName(connectionId: string, database: string): string {
  return `${SQLSERVER_PREFIX}${connectionId}/${database}`
}

export function isSqlServerName(name: string): boolean {
  return name.toUpperCase().startsWith(SQLSERVER_PREFIX)
}

export function parseSqlServerName(
  name: string
): { connectionId: string; database: string } | null {
  if (!isSqlServerName(name)) return null
  const body = name.slice(SQLSERVER_PREFIX.length)
  const cut = body.indexOf("/")
  if (cut <= 0) return null
  return { connectionId: body.slice(0, cut), database: body.slice(cut + 1) }
}

/** A saved connection. The password never leaves the server. */
export type SavedConnection = {
  connection_id: string
  name: string
  vendor: "sqlserver"
  host: string
  port: number
  username: string
  created_by: string | null
  created_at: string | null
  /** Databases the saved login can open. Empty when the server is unreachable. */
  databases: string[]
  /** Why the databases could not be listed, or null. */
  error: string | null
  server_version?: string
}

export type ConnectionInput = {
  name: string
  host: string
  port: number
  username: string
  password: string
}

export type ConnectionTest = {
  ok: true
  server_version: string
  databases: string[]
}

export async function fetchConnections(): Promise<{
  connections: SavedConnection[]
  allowed_hosts: string[]
}> {
  return api("/connections")
}

export async function testConnection(input: ConnectionInput): Promise<ConnectionTest> {
  return api("/connections/test", { method: "POST", json: input })
}

export async function createConnection(input: ConnectionInput): Promise<SavedConnection> {
  return api("/connections", { method: "POST", json: input })
}

export async function deleteConnection(connectionId: string): Promise<void> {
  await api(`/connections/${encodeURIComponent(connectionId)}`, { method: "DELETE" })
}

/** One entry per database on every saved connection, for the source picker. */
export function sqlServerSources(connections: SavedConnection[]): DatabaseSummary[] {
  return connections.flatMap((connection) =>
    connection.databases.map((database) => ({
      name: sqlServerSourceName(connection.connection_id, database),
      display_name: database,
      description: `SQL Server · ${connection.name}`,
      is_documented: false,
      kind: "sqlserver" as const,
    }))
  )
}

/** The server's message out of an error thrown by `api`, which carries the
 *  response body: FastAPI sends {"detail": "..."}. */
export function errorDetail(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback
  try {
    const parsed = JSON.parse(err.message) as { detail?: unknown }
    if (typeof parsed.detail === "string") return parsed.detail
  } catch {
    /* not JSON: the message is already text */
  }
  return err.message || fallback
}
