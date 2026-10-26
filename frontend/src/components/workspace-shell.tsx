import * as React from "react"
import { Navigate, useParams } from "react-router-dom"

import { AppShell } from "@/components/app-shell"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuth } from "@/contexts/auth-context"
import { useDatabase } from "@/contexts/database-context"
import { WorkspaceProvider } from "@/contexts/workspace-context"
import { fetchSavedDashboards } from "@/lib/api"
import {
  FILES_WORKSPACE,
  SNOWFLAKE_WORKSPACE,
  sqlServerWorkspace,
  workspaceForDashboard,
  workspacePaths,
  type Workspace,
} from "@/lib/workspace"

/**
 * The app, inside one connection.
 *
 * Keyed by the workspace, so moving to another connection starts from a clean
 * chat rather than carrying the previous source's selection across.
 */
export function WorkspaceShell({ kind }: { kind: Workspace["kind"] }) {
  const { connectionId = "" } = useParams()
  const { connections } = useDatabase()
  const { user } = useAuth()

  const workspace = React.useMemo<Workspace>(
    () =>
      kind === "snowflake"
        ? SNOWFLAKE_WORKSPACE
        : kind === "files"
          ? FILES_WORKSPACE
          : sqlServerWorkspace(connectionId),
    [kind, connectionId]
  )

  // Someone who has not connected Snowflake has nothing to see in its
  // workspace; the connectors page is where they connect it.
  if (kind === "snowflake" && user && !user.snowflake_linked) {
    return <Navigate to="/" replace />
  }

  // A SQL Server workspace for a connection that has since been deleted has
  // nothing to show; the connectors page is where to choose another.
  if (
    kind === "sqlserver" &&
    connections !== null &&
    !connections.some((c) => c.connection_id === connectionId)
  ) {
    return <Navigate to="/" replace />
  }

  return (
    <WorkspaceProvider workspace={workspace}>
      <AppShell key={workspace.base} />
    </WorkspaceProvider>
  )
}

/**
 * A dashboard link that names no workspace — the form save_dashboard hands out
 * and older bookmarks use — sent on to the workspace its dashboard belongs to.
 */
export function DashboardRedirect() {
  const { reportId = "" } = useParams()
  const [target, setTarget] = React.useState<string | null>(null)

  React.useEffect(() => {
    let cancelled = false
    fetchSavedDashboards()
      .then((list) => {
        if (cancelled) return
        const found = list.find((d) => d.report_id === reportId)
        setTarget(
          found ? workspacePaths(workspaceForDashboard(found)).dashboard(reportId) : "/"
        )
      })
      .catch(() => !cancelled && setTarget("/"))
    return () => {
      cancelled = true
    }
  }, [reportId])

  if (target === null) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-3 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-56 w-full" />
      </div>
    )
  }
  return <Navigate to={target} replace />
}
