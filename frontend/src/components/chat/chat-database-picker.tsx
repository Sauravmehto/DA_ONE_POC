import * as React from "react"
import { Link } from "react-router-dom"
import { DatabaseIcon, SearchIcon, TriangleAlertIcon } from "lucide-react"

import type { DatabaseSummary } from "@/lib/api"
import type { SavedConnection } from "@/lib/connections-api"
import {
  collectionSource,
  listCollections,
  type Collection,
} from "@/lib/excel-source-api"
import { workspacePaths, type Workspace } from "@/lib/workspace"
import { cn } from "@/lib/utils"
import { FolderGlyph } from "@/components/file-type-icon"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"

/** More items than this and the list gets a search box. */
const SEARCH_THRESHOLD = 6

type ChatSourcePickerProps = {
  /** The connection this chat belongs to; only its items are offered. */
  workspace: Workspace
  /** Snowflake databases the signed-in role can see. */
  databases: DatabaseSummary[]
  /** Databases on saved SQL Server connections, one entry each. */
  sqlServerDatabases: DatabaseSummary[]
  /** Saved SQL Server connections; null while loading. */
  connections: SavedConnection[] | null
  onSelect: (source: DatabaseSummary) => void
  isLoading?: boolean
  error?: string | null
  className?: string
}

/**
 * The databases or folders a new chat can use, from this workspace's
 * connection only.
 */
export function ChatSourcePicker({
  workspace,
  databases,
  sqlServerDatabases,
  connections,
  onSelect,
  isLoading = false,
  error = null,
  className,
}: ChatSourcePickerProps) {
  if (workspace.kind === "snowflake") {
    return (
      <ItemList
        className={className}
        title="Choose a database"
        items={databases}
        isLoading={isLoading}
        error={error}
        emptyText="Your Snowflake role cannot see any databases."
        itemIcon={<DatabaseIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
        onSelect={onSelect}
      />
    )
  }

  if (workspace.kind === "sqlserver") {
    const connection = connections?.find((c) => c.connection_id === workspace.connectionId)
    const prefix = `SQLSERVER:${workspace.connectionId}/`.toUpperCase()
    return (
      <ItemList
        className={className}
        title="Choose a database"
        items={sqlServerDatabases
          .filter((db) => db.name.toUpperCase().startsWith(prefix))
          .map((db) => ({ ...db, description: "" }))}
        isLoading={connections === null}
        error={connection?.error ?? null}
        emptyText="No databases found on this server for this login."
        itemIcon={<DatabaseIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
        onSelect={onSelect}
      />
    )
  }

  return <FilesList className={className} onSelect={onSelect} />
}

function FilesList({
  onSelect,
  className,
}: {
  onSelect: (source: DatabaseSummary) => void
  className?: string
}) {
  const [collections, setCollections] = React.useState<Collection[] | null>(null)

  React.useEffect(() => {
    let cancelled = false
    listCollections()
      .then((result) => {
        if (!cancelled) setCollections(result)
      })
      .catch(() => {
        if (!cancelled) setCollections([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  const library = workspacePaths({ kind: "files", base: "/files" }).library
  return (
    <ItemList
      className={className}
      title="Choose a folder"
      items={(collections ?? []).map(collectionSource)}
      isLoading={collections === null}
      emptyText="Nothing has been uploaded yet."
      itemIcon={<FolderGlyph className="mt-0.5 size-4" />}
      onSelect={onSelect}
      footer={
        <Link
          to={library}
          className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          Upload or manage files
        </Link>
      }
    />
  )
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

function ItemList({
  title,
  items,
  isLoading = false,
  error = null,
  emptyText,
  itemIcon,
  onSelect,
  footer,
  className,
}: {
  title: string
  items: DatabaseSummary[]
  isLoading?: boolean
  error?: string | null
  emptyText: string
  itemIcon: React.ReactNode
  onSelect: (source: DatabaseSummary) => void
  footer?: React.ReactNode
  className?: string
}) {
  const [query, setQuery] = React.useState("")
  const needle = query.trim().toLowerCase()
  const shown = needle
    ? items.filter(
        (item) =>
          item.display_name.toLowerCase().includes(needle) ||
          item.description.toLowerCase().includes(needle)
      )
    : items

  return (
    <div className={cn("w-full space-y-3", className)}>
      <p className="text-sm font-medium text-foreground">{title}</p>

      {items.length > SEARCH_THRESHOLD ? (
        <div className="relative">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${plural(items.length, "item")}`}
            className="pl-8"
            aria-label="Search"
          />
        </div>
      ) : null}

      {error ? (
        <p className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
          <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
          {error}
        </p>
      ) : isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full rounded-lg" />
          <Skeleton className="h-12 w-full rounded-lg" />
        </div>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          {emptyText}
        </p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing matches “{query}”.</p>
      ) : (
        <ul className="scrollbar-slim max-h-80 divide-y overflow-y-auto rounded-lg border">
          {shown.map((item) => (
            <li key={item.name}>
              <button
                type="button"
                onClick={() => onSelect(item)}
                className="flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors hover:bg-muted/50"
              >
                {itemIcon}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {item.display_name}
                  </span>
                  {item.description ? (
                    <span className="line-clamp-1 text-xs text-muted-foreground">
                      {item.description}
                    </span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {footer}
    </div>
  )
}
