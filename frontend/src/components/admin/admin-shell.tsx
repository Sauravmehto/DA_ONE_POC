import * as React from "react"
import { Link, NavLink, Outlet } from "react-router-dom"
import {
  DatabaseIcon,
  ExternalLinkIcon,
  FileSpreadsheetIcon,
  InfoIcon,
  ShieldAlertIcon,
  UserCogIcon,
  UsersIcon,
} from "lucide-react"

import { PageErrorBoundary } from "@/components/error-boundary"
import { useAuth } from "@/contexts/auth-context"
import { fetchSnowflakeAuthor } from "@/lib/accounts-api"
import { accent } from "@/lib/accents"
import { userLabel } from "@/lib/api"
import { cn } from "@/lib/utils"

/*
 * Two separate areas share this layout, and neither shows the other's links.
 *
 * Administration (/admin) is the app's own settings: who can sign in, and who
 * sees what. Nothing in it is about one data source, so it is the same for a
 * client with Snowflake as for one with only files and SQL Server. For
 * administrators.
 *
 * Snowflake tools (/tools) author against Snowflake: the semantic layer and
 * Excel report templates. Anyone with Snowflake connected can open them, and
 * saving depends on their Snowflake role.
 *
 * Both open in their own tab and deliberately do not use AppShell: no chat
 * sidebar, no conversation history. Someone changing settings should be able
 * to tell at a glance that they are not in the analyst-facing app.
 */

type NavItem = { to: string; label: string; icon: React.ReactNode }

function Frame({
  title,
  items,
  children,
}: {
  title: string
  items: NavItem[]
  children: React.ReactNode
}) {
  const link = ({ isActive }: { isActive: boolean }) =>
    cn(
      "flex items-center gap-2 text-sm",
      isActive ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
    )

  return (
    <div className="flex h-svh flex-col overflow-hidden bg-background">
      <header className="flex min-h-12 shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-2">
        <span className="text-[11px] tracking-wide text-muted-foreground uppercase">
          {title}
        </span>
        {items.map((item) => (
          <NavLink key={item.to} to={item.to} className={link}>
            {item.icon}
            {item.label}
          </NavLink>
        ))}
        <a
          href="/"
          className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          Open DataAlphaOne
          <ExternalLinkIcon className="size-3.5" />
        </a>
      </header>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
    </div>
  )
}

/** Users and data groups: the app's own settings, for administrators. */
export function AdminShell() {
  const { user, isLoading } = useAuth()
  return (
    <Frame
      title="Administration"
      items={[
        { to: "/admin/users", label: "Users", icon: <UserCogIcon {...accent("users")} /> },
        {
          to: "/admin/data-groups",
          label: "Data groups",
          icon: <UsersIcon {...accent("groups")} />,
        },
      ]}
    >
      {isLoading || !user ? null : !user.is_admin ? (
        <Refusal title="Administrator access required">
          Users and data groups are managed by administrators. You are signed in as{" "}
          <span className="font-mono">{userLabel(user)}</span>. Ask an administrator for access.
        </Refusal>
      ) : (
        <PageErrorBoundary>
          <Outlet />
        </PageErrorBoundary>
      )}
    </Frame>
  )
}

/** The Semantic Builder, for any connector's databases, and the Report
 *  Builder, which is Snowflake's own. */
export function SnowflakeToolsShell({ showReportBuilder }: { showReportBuilder: boolean }) {
  const { user, isLoading } = useAuth()
  const items: NavItem[] = [
    {
      to: "/tools/semantic",
      label: "Semantic Builder",
      icon: <DatabaseIcon {...accent("semantic")} />,
    },
  ]
  if (showReportBuilder) {
    items.push({
      to: "/tools/report-builder",
      label: "Report Builder",
      icon: <FileSpreadsheetIcon {...accent("reports")} />,
    })
  }

  // A layer belongs to a source, not to Snowflake: an administrator with only
  // a SQL Server connection writes one here too. The Snowflake role notice is
  // shown only to someone working on Snowflake databases.
  return (
    <Frame title="Tools" items={items}>
      {isLoading || !user ? null : (
        <>
          {user.snowflake_linked ? <AuthorNotice /> : null}
          <PageErrorBoundary>
            <Outlet />
          </PageErrorBoundary>
        </>
      )}
    </Frame>
  )
}

function Refusal({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-md p-10 text-center">
      <ShieldAlertIcon className="mx-auto size-6 text-muted-foreground" />
      <h1 className="mt-3 text-lg font-medium">{title}</h1>
      <div className="mt-2 text-sm text-muted-foreground">{children}</div>
      <Link to="/" className="mt-4 inline-block text-sm underline underline-offset-4">
        Back to DataAlphaOne
      </Link>
    </div>
  )
}

/** Says, before anyone tries, when their Snowflake role can look but not save.
 *  The server refuses the save either way; this spares the surprise. */
function AuthorNotice() {
  const [state, setState] = React.useState<{ can_author: boolean; role: string | null } | null>(
    null
  )

  React.useEffect(() => {
    let cancelled = false
    fetchSnowflakeAuthor()
      .then((result) => !cancelled && setState(result))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  if (!state || state.can_author) return null
  return (
    <div className="flex items-start gap-2 border-b bg-amber-500/5 px-4 py-2 text-xs text-amber-800 dark:text-amber-400">
      <InfoIcon className="mt-0.5 size-3.5 shrink-0" />
      <span>
        You can look around, but saving needs a Snowflake role allowed to change these
        settings. Your active role is{" "}
        <span className="font-mono">{state.role ?? "not known"}</span>.
      </span>
    </div>
  )
}
