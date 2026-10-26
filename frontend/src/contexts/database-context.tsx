/* eslint-disable react-refresh/only-export-components */
import * as React from "react"

import { fetchDatabases, type DatabaseSummary } from "@/lib/api"
import {
  fetchConnections,
  isSqlServerName,
  sqlServerSources,
  type SavedConnection,
} from "@/lib/connections-api"
import {
  collectionSource,
  fetchDataset,
  isCollectionName,
  isSpreadsheetName,
  listCollections,
  sourceIdFromName,
} from "@/lib/excel-source-api"

type DatabaseContextValue = {
  /** Warehouse databases, for the chat picker. Spreadsheets are NOT here. */
  databases: DatabaseSummary[]
  /** Databases on saved SQL Server connections, one entry each. */
  sqlServerDatabases: DatabaseSummary[]
  /** The saved SQL Server connections themselves; null until first loaded. */
  connections: SavedConnection[] | null
  /** Hosts a new SQL Server connection may point at, from the same load. */
  allowedHosts: string[]
  isLoading: boolean
  error: string | null
  refresh: () => Promise<void>
  /**
   * The source behind a conversation's `database` name, whichever kind it is.
   *
   * Chat needs to name the source it is querying — including for a resumed
   * thread, where all it has is the stored name. A warehouse is found in the
   * list above; an uploaded spreadsheet is fetched on demand, because it is
   * deliberately not in that list.
   */
  resolveSource: (name: string) => Promise<DatabaseSummary | null>
}

const DatabaseContext = React.createContext<DatabaseContextValue | undefined>(
  undefined
)

export function DatabaseProvider({ children }: { children: React.ReactNode }) {
  const [databases, setDatabases] = React.useState<DatabaseSummary[]>([])
  const [sqlServerDatabases, setSqlServerDatabases] = React.useState<
    DatabaseSummary[]
  >([])
  const [connections, setConnections] = React.useState<SavedConnection[] | null>(
    null
  )
  const [allowedHosts, setAllowedHosts] = React.useState<string[]>([])
  const [isLoading, setIsLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const loadSqlServer = React.useCallback(async () => {
    try {
      const result = await fetchConnections()
      const sources = sqlServerSources(result.connections)
      setConnections(result.connections)
      setAllowedHosts(result.allowed_hosts)
      setSqlServerDatabases(sources)
      return sources
    } catch {
      // Not fatal: Snowflake and uploads still work without it.
      setConnections((current) => current ?? [])
      return []
    }
  }, [])

  const load = React.useCallback(async () => {
    setIsLoading(true)
    setError(null)
    // Separately, and not awaited: a slow SQL Server login must not hold up
    // the Snowflake list.
    void loadSqlServer()
    try {
      // Warehouses ONLY. Uploaded spreadsheets deliberately do not appear here:
      // a firm has a dozen databases but will accumulate hundreds of
      // spreadsheets, and merging them turns a short, stable picker into an
      // unusable list. They live in their own library and reach chat through
      // resolveSource below.
      const result = await fetchDatabases()
      setDatabases(result.map((db) => ({ ...db, kind: "warehouse" as const })))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load databases")
    } finally {
      setIsLoading(false)
    }
  }, [loadSqlServer])

  React.useEffect(() => {
    void load()
  }, [load])

  // Spreadsheets already resolved this session. A thread's source is looked up
  // on every render pass that touches it, and re-fetching an immutable record
  // each time would put a request behind every keystroke.
  const spreadsheetCache = React.useRef(new Map<string, DatabaseSummary>())

  const resolveSource = React.useCallback(
    async (name: string): Promise<DatabaseSummary | null> => {
      if (!name) return null
      if (isSqlServerName(name)) {
        const match = (list: DatabaseSummary[]) =>
          list.find((db) => db.name.toUpperCase() === name.toUpperCase()) ?? null
        // Resolved against a fresh list when the loaded one lacks it: the
        // Connections page can open a chat on a connection added moments ago.
        return match(sqlServerDatabases) ?? match(await loadSqlServer())
      }
      if (isCollectionName(name)) {
        const cached = spreadsheetCache.current.get(name)
        if (cached) return cached

        // A folder is not in any list the picker shows, so it is looked up by
        // id the same way a spreadsheet is.
        const id = name.slice(name.indexOf(":") + 1)
        const collection = (await listCollections()).find(
          (c) => c.collection_id === id
        )
        if (!collection) return null
        const folder = collectionSource(collection)
        spreadsheetCache.current.set(name, folder)
        return folder
      }
      if (!isSpreadsheetName(name)) {
        return databases.find((db) => db.name === name) ?? null
      }

      const cached = spreadsheetCache.current.get(name)
      if (cached) return cached

      const source = await fetchDataset(sourceIdFromName(name))
      if (!source) return null
      const summary: DatabaseSummary = {
        name,
        display_name: source.name,
        description: `${source.file_count} file${source.file_count === 1 ? "" : "s"} · ${source.total_rows.toLocaleString()} rows · ${source.tables.length} tables`,
        is_documented: true,
        kind: "spreadsheet",
      }
      spreadsheetCache.current.set(name, summary)
      return summary
    },
    [databases, sqlServerDatabases, loadSqlServer]
  )

  const value = React.useMemo<DatabaseContextValue>(
    () => ({
      databases,
      sqlServerDatabases,
      connections,
      allowedHosts,
      isLoading,
      error,
      refresh: load,
      resolveSource,
    }),
    [databases, sqlServerDatabases, connections, allowedHosts, isLoading, error, load,
      resolveSource]
  )

  return (
    <DatabaseContext.Provider value={value}>
      {children}
    </DatabaseContext.Provider>
  )
}

export function useDatabase() {
  const context = React.useContext(DatabaseContext)
  if (context === undefined) {
    throw new Error("useDatabase must be used within a DatabaseProvider")
  }
  return context
}
