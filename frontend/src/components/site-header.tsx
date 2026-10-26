import { useLocation } from "react-router-dom"

import { ChatHeaderDatabase } from "@/components/chat/chat-header-database"
import { useWorkspace } from "@/contexts/workspace-context"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { ThemeToggle } from "@/components/theme-toggle"

export function SiteHeader({ title }: { title?: string }) {
  const location = useLocation()
  const { paths } = useWorkspace()
  // Keyed by this workspace's own paths: every page exists once per
  // connection, so a fixed table of absolute paths would match nothing.
  const titles: Record<string, string> = {
    [paths.chat]: "New",
    [paths.explore]: "Explore",
    [paths.reports]: "Reports",
    [paths.dashboards]: "Dashboards",
    [paths.library]: "Files",
  }
  const resolvedTitle = title ?? titles[location.pathname] ?? "DataAlphaOne"

  return (
    <header className="flex h-(--header-height) shrink-0 items-center gap-2 border-b transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-(--header-height)">
      <div className="flex w-full min-w-0 items-center gap-1 px-4 lg:gap-2 lg:px-6">
        <SidebarTrigger className="-ml-1" />
        <Separator
          orientation="vertical"
          className="mx-2 data-[orientation=vertical]:h-4"
        />
        <h1 className="shrink-0 text-base font-medium">{resolvedTitle}</h1>
        <ChatHeaderDatabase />
        <ThemeToggle className="ml-auto" />
      </div>
    </header>
  )
}
