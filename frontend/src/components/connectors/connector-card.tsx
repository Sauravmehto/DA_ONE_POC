import {
  ArrowRightIcon,
  MoreHorizontalIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from "lucide-react"

import { ConnectorLogo, type ConnectorKind } from "@/components/connector-logo"
import { StatusBadge, type StatusTone } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

type Status = "connected" | "error" | "idle"

const BADGE: Record<Status, { tone: StatusTone; label: string; live: boolean }> = {
  connected: { tone: "success", label: "Connected", live: true },
  error: { tone: "danger", label: "Needs attention", live: false },
  idle: { tone: "neutral", label: "Not connected", live: false },
}

/**
 * One data source on the Connect page: what it is, whether it is working, and
 * the one thing to do with it. Only a source that still has to be set up gets
 * the blue button; one that is ready opens quietly.
 */
export function ConnectorCard({
  kind,
  title,
  subtitle,
  detail,
  status,
  statusLabel,
  actionLabel,
  onConnect,
  onDelete,
  onRetry,
  isRetrying = false,
}: {
  kind: ConnectorKind
  title: string
  subtitle: string
  /** null while loading. */
  detail: string | null
  /** idle: available but not set up for this person yet. */
  status: Status
  /** Replaces the badge's wording, e.g. "Ready" for something always there. */
  statusLabel?: string
  actionLabel?: string
  onConnect: () => void
  /** Offered in a "more" menu, and only to someone who may delete. */
  onDelete?: () => void
  /** Offered only in the error state: a server that was busy a moment ago is
      usually reachable a moment later, and the alternative was reloading the
      whole page to find out. */
  onRetry?: () => void
  isRetrying?: boolean
}) {
  const badge = BADGE[status]
  const needsSetup = status === "idle"

  return (
    <li className="lift flex min-h-[220px] flex-col rounded-2xl border bg-card p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="grid size-12 shrink-0 place-items-center rounded-xl border bg-muted/50">
          <ConnectorLogo kind={kind} className="size-7" />
        </div>
        <div className="-mr-1.5 flex items-center gap-1">
          <StatusBadge tone={badge.tone} live={badge.live}>
            {statusLabel ?? badge.label}
          </StatusBadge>
          {onDelete ? (
            // Not modal: the item opens a confirmation dialog, and two modal
            // layers fighting over the page leave it with `pointer-events:
            // none` after the dialog closes (Radix issue #1241).
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-muted-foreground"
                  aria-label={`More actions for ${title}`}
                >
                  <MoreHorizontalIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuItem variant="destructive" onSelect={onDelete}>
                  <Trash2Icon />
                  Delete connection
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      </div>

      <p className="mt-4 truncate text-base font-semibold tracking-tight">{title}</p>
      <p className="truncate text-sm text-muted-foreground">{subtitle}</p>

      {/* A div, not a p: while the detail is loading this holds a placeholder
          block, and a div inside a p is invalid HTML. */}
      <div
        className={cn(
          "mt-3 flex min-h-6 items-start gap-1.5 text-xs",
          status === "error" ? "text-destructive" : "text-muted-foreground"
        )}
      >
        {detail === null ? (
          <div aria-hidden className="shimmer h-5 w-24 rounded-md" />
        ) : status === "error" ? (
          <>
            <TriangleAlertIcon className="mt-px size-3.5 shrink-0" aria-hidden />
            <span className="line-clamp-2">{detail}</span>
          </>
        ) : status === "idle" ? (
          // A sentence explaining what connecting does, not a fact: no chip.
          <span className="line-clamp-2 text-pretty">{detail}</span>
        ) : (
          <span className="line-clamp-2 rounded-md bg-muted px-2 py-0.5 font-medium text-foreground/70">
            {detail}
          </span>
        )}
      </div>

      <div className="mt-auto flex gap-2 pt-5">
        <Button
          variant={needsSetup ? "brand" : "outline"}
          className="press group/open flex-1"
          onClick={onConnect}
        >
          {actionLabel ?? (needsSetup ? "Connect" : "Open")}
          {needsSetup ? null : (
            <ArrowRightIcon className="transition-transform duration-200 group-hover/open:translate-x-0.5" />
          )}
        </Button>
        {status === "error" && onRetry ? (
          <Button
            variant="outline"
            className="press"
            onClick={onRetry}
            disabled={isRetrying}
            aria-label={`Try ${title} again`}
          >
            <RefreshCwIcon className={cn(isRetrying && "animate-spin")} />
            Try again
          </Button>
        ) : null}
      </div>
    </li>
  )
}

/** The same shape as a card, shimmering, while the list of sources loads. */
export function ConnectorCardSkeleton() {
  return (
    <li
      aria-hidden
      className="flex min-h-[220px] flex-col rounded-2xl border bg-card p-5 shadow-sm"
    >
      <div className="flex items-start justify-between">
        <div className="shimmer size-12 rounded-xl" />
        <div className="shimmer h-6 w-24 rounded-full" />
      </div>
      <div className="shimmer mt-4 h-5 w-36 rounded-md" />
      <div className="shimmer mt-2 h-4 w-44 rounded-md" />
      <div className="shimmer mt-4 h-5 w-24 rounded-md" />
      <div className="shimmer mt-auto h-8 w-full rounded-lg" />
    </li>
  )
}

/** For an administrator: add another connection. */
export function AddConnectionTile({ onClick }: { onClick: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className="group/add flex h-full min-h-[220px] w-full flex-col items-center justify-center gap-3 rounded-2xl border border-dashed bg-card/40 p-5 text-sm font-medium text-muted-foreground transition-colors duration-200 outline-none hover:border-brand/60 hover:bg-brand-soft/60 hover:text-brand-strong focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span className="grid size-10 place-items-center rounded-full border bg-background transition-transform duration-300 ease-(--ease-spring) group-hover/add:rotate-90 group-hover/add:scale-110">
          <PlusIcon className="size-5" />
        </span>
        Add new connection
      </button>
    </li>
  )
}
