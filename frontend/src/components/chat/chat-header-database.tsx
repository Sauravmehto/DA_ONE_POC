import { DatabaseIcon, ServerIcon } from "lucide-react"
import { useLocation } from "react-router-dom"

import { useChat } from "@/contexts/chat-context"
import { useWorkspace } from "@/contexts/workspace-context"

export function ChatHeaderDatabase() {
  const location = useLocation()
  const { chatDatabase } = useChat()
  const { paths } = useWorkspace()

  if (location.pathname !== paths.chat || !chatDatabase) {
    return null
  }

  const isSqlServer = chatDatabase.kind === "sqlserver"
  const Icon = isSqlServer ? ServerIcon : DatabaseIcon

  return (
    <div className="ml-auto flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
      <Icon className="size-4 shrink-0" />
      <span className="truncate font-medium text-foreground">
        {chatDatabase.display_name}
      </span>
      {isSqlServer ? (
        <span className="hidden truncate text-xs sm:inline">{chatDatabase.description}</span>
      ) : null}
    </div>
  )
}
