/* eslint-disable react-refresh/only-export-components */
import * as React from "react"

import { workspacePaths, type Workspace } from "@/lib/workspace"

type WorkspaceContextValue = {
  workspace: Workspace
  paths: ReturnType<typeof workspacePaths>
}

const WorkspaceContext = React.createContext<WorkspaceContextValue | undefined>(
  undefined
)

export function WorkspaceProvider({
  workspace,
  children,
}: {
  workspace: Workspace
  children: React.ReactNode
}) {
  const value = React.useMemo(
    () => ({ workspace, paths: workspacePaths(workspace) }),
    [workspace]
  )
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

/** The connection the current page belongs to, and its page paths. */
export function useWorkspace(): WorkspaceContextValue {
  const context = React.useContext(WorkspaceContext)
  if (context === undefined) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider")
  }
  return context
}
