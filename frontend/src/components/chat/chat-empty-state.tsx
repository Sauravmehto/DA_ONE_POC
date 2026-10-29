import { DatabaseIcon, ServerIcon } from "lucide-react"
import { useNavigate } from "react-router-dom"

import { ChatComposer } from "@/components/chat/chat-composer"
import { ChatSourcePicker } from "@/components/chat/chat-database-picker"
import { CHAT_COLUMN } from "@/components/chat/chat-layout"
import { FolderGlyph } from "@/components/file-type-icon"
import { Button } from "@/components/ui/button"
import { useChat } from "@/contexts/chat-context"
import { useDatabase } from "@/contexts/database-context"
import { useWorkspace } from "@/contexts/workspace-context"
import type { DatabaseSummary } from "@/lib/api"
import { accent } from "@/lib/accents"
import { cn } from "@/lib/utils"

type ChatEmptyStateProps = {
  input: string
  onInputChange: (value: string) => void
  onSend: () => void
  onStop?: () => void
  isLoading?: boolean
  /** null clears the selection, returning the user to the picker. */
  onSelectDatabase: (database: DatabaseSummary | null) => void
}

/** Which connection a chosen source belongs to, as one short label. */
function sourceLabel(source: DatabaseSummary): string {
  if (source.kind === "sqlserver") return source.description
  if (source.kind === "spreadsheet") return `Files · ${source.description}`
  return "Snowflake"
}

function SourceIcon({ source }: { source: DatabaseSummary }) {
  if (source.kind === "sqlserver") return <ServerIcon {...accent("connections")} />
  if (source.kind === "spreadsheet") return <FolderGlyph className="size-4" />
  return <DatabaseIcon {...accent("explore")} />
}

export function ChatEmptyState({
  input,
  onInputChange,
  onSend,
  onStop,
  isLoading = false,
  onSelectDatabase,
}: ChatEmptyStateProps) {
  const { chatDatabase } = useChat()
  const { workspace, paths } = useWorkspace()
  const navigate = useNavigate()
  const {
    databases,
    sqlServerDatabases,
    connections,
    isLoading: databasesLoading,
    error: databasesError,
  } = useDatabase()

  const hasDatabase = chatDatabase !== null
  // On Files, a new chat has no folder yet. The box stays typeable, and
  // Enter or the send button opens the library instead of sending a message.
  const sendOpensLibrary = workspace.kind === "files" && !hasDatabase

  function handleSend() {
    if (sendOpensLibrary) {
      navigate(paths.library)
      return
    }
    onSend()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex min-h-min flex-1 flex-col items-center justify-center gap-8 py-12">
        {/* A prompt, not a product name — the brand is already in the sidebar.
            Left deliberately unscoped: this surface answers questions, charts,
            explores and builds dashboards, so naming any one of them would
            undersell the rest. */}
        <h2 className="text-3xl font-semibold tracking-tight text-foreground">
          How can I help?
        </h2>

        {/* The source first, then the box you type in. On Files, with no
            folder chosen yet, the box stays open and Enter opens the library.
            Elsewhere, choosing a source is what enables the box. */}
        <div className={cn(CHAT_COLUMN, "flex flex-col gap-6")}>
          {chatDatabase ? (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
              <SourceIcon source={chatDatabase} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {chatDatabase.display_name}
                </span>
                <span className="block truncate text-xs text-muted-foreground">
                  {sourceLabel(chatDatabase)}
                </span>
              </span>
              <Button variant="ghost" size="sm" onClick={() => onSelectDatabase(null)}>
                Change
              </Button>
            </div>
          ) : (
            <ChatSourcePicker
              workspace={workspace}
              databases={databases}
              sqlServerDatabases={sqlServerDatabases}
              connections={connections}
              onSelect={onSelectDatabase}
              isLoading={databasesLoading}
              error={databasesError}
            />
          )}

          <ChatComposer
            value={input}
            onChange={onInputChange}
            onSend={handleSend}
            onStop={onStop}
            isLoading={isLoading}
            canSend={hasDatabase || sendOpensLibrary}
            disabled={!hasDatabase && !sendOpensLibrary}
            placeholder={
              hasDatabase
                ? `Ask anything about ${chatDatabase.display_name}…`
                : sendOpensLibrary
                  ? "Type a message, then press Enter…"
                  : "Choose a data source above to start…"
            }
          />
        </div>
      </div>
    </div>
  )
}
