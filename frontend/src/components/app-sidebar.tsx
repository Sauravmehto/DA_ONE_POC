import { useNavigate } from "react-router-dom"
import {
  ArrowLeftRightIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileChartColumnIcon,
  FileSpreadsheetIcon,
  LayoutDashboardIcon,
  PlusIcon,
  SearchIcon,
} from "lucide-react"

import { useAuth } from "@/contexts/auth-context"
import { useDatabase } from "@/contexts/database-context"
import { useWorkspace } from "@/contexts/workspace-context"
import { ConnectorLogo } from "@/components/connector-logo"
import { FolderGlyph } from "@/components/file-type-icon"
import { accent } from "@/lib/accents"
import { SHOW_REPORT_BUILDER } from "@/lib/features"
import { NavConversations } from "@/components/nav-conversations"
import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import { Button } from "@/components/ui/button"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"


export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const { workspace, paths } = useWorkspace()
  const { connections } = useDatabase()

  const connection =
    workspace.kind === "sqlserver"
      ? connections?.find((c) => c.connection_id === workspace.connectionId)
      : undefined
  const workspaceName =
    workspace.kind === "snowflake"
      ? "Snowflake"
      : workspace.kind === "files"
        ? "Files"
        : (connection?.name ?? "SQL Server")
  // Both tools work on a database. Uploads are not one, so this workspace
  // has no use for either — and an unscoped link showed it every OTHER
  // workspace's semantic layers, which is the boundary these are supposed to
  // keep.
  const hasDatabase = workspace.kind !== "files"

  // The tools open in their own tab, which knows nothing about where it was
  // opened from. The workspace goes in the link so the Builder shows this
  // workspace's databases rather than every source in the deployment.
  const semanticHref =
    workspace.kind === "sqlserver"
      ? `/tools/semantic?connector=sqlserver&connection=${encodeURIComponent(workspace.connectionId)}`
      : "/tools/semantic?connector=snowflake"

  // A report runs against a database, so the Report Builder is scoped the same
  // way the Semantic Builder is — and is offered wherever there is one.
  const reportBuilderHref = semanticHref.replace("/tools/semantic", "/tools/report-builder")

  const workspaceDetail =
    workspace.kind === "snowflake"
      ? (user?.snowflake_account ?? "Data warehouse")
      : workspace.kind === "files"
        ? "Spreadsheets and documents"
        : connection
          ? `SQL Server · ${connection.host}`
          : "SQL Server"

  // Only the pages this source supports. Explore and Excel reports read the
  // Snowflake catalogue; the upload library belongs to Files.
  const navItems =
    workspace.kind === "snowflake"
      ? [
          { title: "Explore", url: paths.explore, icon: <SearchIcon {...accent("explore")} /> },
          { title: "Dashboards", url: paths.dashboards, icon: <LayoutDashboardIcon {...accent("dashboards")} /> },
          { title: "Reports", url: paths.reports, icon: <FileChartColumnIcon {...accent("reports")} /> },
        ]
      : workspace.kind === "files"
        ? [
            { title: "Library", url: paths.library, icon: <FolderGlyph className="size-4" /> },
            { title: "Dashboards", url: paths.dashboards, icon: <LayoutDashboardIcon {...accent("dashboards")} /> },
          ]
        : [
            { title: "Dashboards", url: paths.dashboards, icon: <LayoutDashboardIcon {...accent("dashboards")} /> },
          ]

  return (
    <Sidebar collapsible="offcanvas" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              onClick={() => navigate("/")}
              tooltip="Switch connection"
              className="group/switch"
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background">
                <ConnectorLogo kind={workspace.kind} className="size-5" />
              </span>
              <span className="grid min-w-0 flex-1 text-left leading-tight">
                <span className="truncate text-sm font-semibold">{workspaceName}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {workspaceDetail}
                </span>
              </span>
              <ArrowLeftRightIcon
                className="size-4 shrink-0 text-muted-foreground"
                aria-label="Switch connection"
              />
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup className="gap-2">
          <Button
            onClick={() => navigate(paths.chat, { state: { newChat: true } })}
            className="justify-start gap-2"
          >
            <PlusIcon className="size-4" />
            New chat
          </Button>
        </SidebarGroup>

        <NavMain label="General" items={navItems} />

        {/* Both tools are here in every workspace that has a database. A
            semantic layer — what the tables MEAN — and a report built from a
            workbook are the same work whatever database is underneath; each
            was Snowflake's only because that is where it was written first.
            Uploads have neither: a spreadsheet has no schema to document and
            is not a report source, so this group is not shown there at all.
            App administration is not here; it lives in its own area, reached
            from the user menu, in every workspace.
            Plain anchors with target=_blank, not router Links: the tools are a
            separate workspace, and someone editing a layer usually wants the
            analyst app still open to test against. */}
        {hasDatabase ? (
          <SidebarGroup>
            <SidebarGroupLabel>Tools</SidebarGroupLabel>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild tooltip="Semantic Builder (opens a new tab)">
                  <a href={semanticHref} target="_blank" rel="noopener noreferrer">
                    <DatabaseIcon {...accent("semantic")} />
                    <span>Semantic Builder</span>
                    <ExternalLinkIcon className="ml-auto size-3.5 opacity-60" />
                  </a>
                </SidebarMenuButton>
              </SidebarMenuItem>
              {SHOW_REPORT_BUILDER ? (
                <SidebarMenuItem>
                  <SidebarMenuButton asChild tooltip="Report Builder (opens a new tab)">
                    <a href={reportBuilderHref} target="_blank" rel="noopener noreferrer">
                      <FileSpreadsheetIcon {...accent("reports")} />
                      <span>Report Builder</span>
                      <ExternalLinkIcon className="ml-auto size-3.5 opacity-60" />
                    </a>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ) : null}
            </SidebarMenu>
          </SidebarGroup>
        ) : null}

        <NavConversations />
      </SidebarContent>

      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  )
}
