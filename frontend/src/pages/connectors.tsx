import * as React from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { LogOutIcon, SettingsIcon } from "lucide-react"
import { toast } from "sonner"

import { BrandLockup } from "@/components/brand"
import { AddConnectionDialog } from "@/components/connections/add-connection-dialog"
import {
  AddConnectionTile,
  ConnectorCard,
  ConnectorCardSkeleton,
} from "@/components/connectors/connector-card"
import { DeleteDialog } from "@/components/delete-dialog"
import { PageHeader } from "@/components/page-header"
import { ThemeToggle } from "@/components/theme-toggle"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/contexts/auth-context"
import { useDatabase } from "@/contexts/database-context"
import { useDeleteTarget } from "@/hooks/use-delete-target"
import { linkSnowflakeUrl } from "@/lib/accounts-api"
import { userLabel } from "@/lib/api"
import { SHOW_CONNECTORS } from "@/lib/features"
import { deleteConnection } from "@/lib/connections-api"
import { listCollections, type Collection } from "@/lib/excel-source-api"
import {
  FILES_WORKSPACE,
  SNOWFLAKE_WORKSPACE,
  sqlServerWorkspace,
} from "@/lib/workspace"

/** "Design Check" -> "DC"; "ana@example.com" -> "AE". */
function initials(label: string): string {
  const parts = label.split(/[\s@._-]+/).filter(Boolean)
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase()
}
function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

/**
 * The first page after signing in: the connectors, and nothing else.
 *
 * Each connector opens its own workspace. Chats, dashboards and reports made in
 * one never appear in another, so the choice made here decides what the rest
 * of the app shows.
 */
export function ConnectorsPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { user, logout } = useAuth()

  /* Connecting Snowflake reports a failure by coming back here with ?error=.
     It used to report it on the sign-in page, which sends anyone already
     signed in straight back to this one — so the reason never appeared and
     the attempt looked like it had simply done nothing. */
  const connectError = searchParams.get("error")
  React.useEffect(() => {
    if (!connectError) return
    toast.error(connectError, { duration: 12000 })
    const next = new URLSearchParams(searchParams)
    next.delete("error")
    setSearchParams(next, { replace: true })
  }, [connectError, searchParams, setSearchParams])
  const isAdmin = Boolean(user?.is_admin)
  const {
    databases,
    connections,
    allowedHosts,
    isLoading: warehousesLoading,
    error: warehouseError,
    refresh,
  } = useDatabase()

  /* Asking again is the whole of it: the list is read fresh, and a server
     that was busy answers this time or says so again. */
  const [isRetrying, setRetrying] = React.useState(false)
  const retry = React.useCallback(() => {
    setRetrying(true)
    void Promise.resolve(refresh()).finally(() => setRetrying(false))
  }, [refresh])

  const [collections, setCollections] = React.useState<Collection[] | null>(null)
  const [isAdding, setAdding] = React.useState(false)

  React.useEffect(() => {
    let cancelled = false
    listCollections()
      .then((result) => !cancelled && setCollections(result))
      .catch(() => !cancelled && setCollections([]))
    return () => {
      cancelled = true
    }
  }, [])

  const removal = useDeleteTarget<{ id: string; name: string }>(
    deleteConnection,
    React.useCallback(() => {
      void refresh()
      toast.success("Connection deleted")
    }, [refresh])
  )

  return (
    <div className="scrollbar-slim relative h-svh overflow-y-auto">
      {/* A soft wash of brand colour behind the top of the page. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-80 bg-[radial-gradient(60%_100%_at_50%_0%,var(--brand-soft),transparent)]"
      />

      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 w-full max-w-5xl items-center gap-3 px-4 sm:px-6">
          <BrandLockup variant="compact" className="w-32" />
          {user ? (
            <div className="ml-auto flex min-w-0 items-center gap-2 sm:gap-3">
              <ThemeToggle />
              <div className="flex min-w-0 items-center gap-2.5">
                <Avatar className="size-8">
                  <AvatarFallback className="bg-brand-soft text-xs font-semibold text-brand-strong">
                    {initials(userLabel(user))}
                  </AvatarFallback>
                </Avatar>
                <span className="hidden min-w-0 text-left text-xs leading-tight sm:block">
                  <span className="block truncate font-medium">{userLabel(user)}</span>
                  <span className="block truncate text-muted-foreground">
                    {user.role ?? user.email ?? user.username}
                  </span>
                </span>
              </div>
              {user.is_admin ? (
                <Button variant="outline" size="sm" className="gap-1.5" asChild>
                  <a href="/admin/users" target="_blank" rel="noopener noreferrer">
                    <SettingsIcon className="size-4" />
                    <span className="hidden sm:inline">Administration</span>
                    <span className="sr-only sm:hidden">Administration</span>
                  </a>
                </Button>
              ) : null}
              <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => void logout()}>
                <LogOutIcon className="size-4" />
                <span className="hidden sm:inline">Log out</span>
                <span className="sr-only sm:hidden">Log out</span>
              </Button>
            </div>
          ) : null}
        </div>
      </header>

      <main className="relative mx-auto w-full max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
        <PageHeader
          title="Connect to your data"
          description="Choose a source. Each source keeps its own chats and dashboards."
          className="mb-8"
        />

        <ul className="stagger grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {SHOW_CONNECTORS ? (
            user && !user.snowflake_linked ? (
              <ConnectorCard
                kind="snowflake"
                title="Snowflake"
                subtitle="Cloud data warehouse"
                detail="Connect your Snowflake user to query with your own permissions."
                status="idle"
                actionLabel="Connect Snowflake"
                onConnect={() => (window.location.href = linkSnowflakeUrl())}
              />
            ) : (
              <ConnectorCard
                kind="snowflake"
                title="Snowflake"
                subtitle={
                  user?.snowflake_account
                    ? `Connected as ${user.snowflake_account}`
                    : "Cloud data warehouse"
                }
                detail={
                  warehousesLoading
                    ? null
                    : warehouseError
                      ? "Could not list databases"
                      : plural(databases.length, "database")
                }
                status={warehouseError ? "error" : "connected"}
                onConnect={() => navigate(SNOWFLAKE_WORKSPACE.base)}
                onRetry={retry}
                isRetrying={isRetrying}
              />
            )
          ) : null}

          {connections === null ? (
            <ConnectorCardSkeleton />
          ) : (
            connections.map((connection) => (
              <ConnectorCard
                key={connection.connection_id}
                kind="sqlserver"
                title={connection.name}
                subtitle={`SQL Server · ${connection.host}`}
                detail={
                  connection.error
                    ? connection.error
                    : plural(connection.databases.length, "database")
                }
                status={connection.error ? "error" : "connected"}
                onConnect={() =>
                  navigate(sqlServerWorkspace(connection.connection_id).base)
                }
                onRetry={retry}
                isRetrying={isRetrying}
                onDelete={
                  isAdmin
                    ? () =>
                        removal.ask({
                          id: connection.connection_id,
                          name: connection.name,
                        })
                    : undefined
                }
              />
            ))
          )}

          <ConnectorCard
            kind="files"
            title="Files"
            subtitle="Spreadsheets and documents"
            detail={collections === null ? null : plural(collections.length, "folder")}
            status="connected"
            statusLabel="Ready"
            onConnect={() => navigate(FILES_WORKSPACE.base)}
          />

          {SHOW_CONNECTORS && isAdmin ? (
            <AddConnectionTile onClick={() => setAdding(true)} />
          ) : null}
        </ul>

        {!isAdmin && connections !== null && connections.length === 0 ? (
          <p className="animate-fade-in-soft mt-8 text-sm text-muted-foreground">
            Need another source, such as a SQL Server database? Ask an administrator to
            add a connection.
          </p>
        ) : null}
      </main>

      {isAdding ? (
        <AddConnectionDialog
          allowedHosts={allowedHosts}
          onOpenChange={setAdding}
          onSaved={() => {
            setAdding(false)
            void refresh()
            toast.success("Connection added")
          }}
        />
      ) : null}

      <DeleteDialog
        open={removal.target !== null}
        name={removal.target?.name ?? ""}
        kind="connection"
        busy={removal.busy}
        error={removal.error}
        onConfirm={() => void removal.confirm()}
        onOpenChange={(open) => (open ? null : removal.close())}
      />
    </div>
  )
}

export default ConnectorsPage