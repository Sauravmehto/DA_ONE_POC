import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * What a list or page says when it has nothing in it yet: an illustration, what
 * is missing, why that is fine, and the one thing to do next. Replaces the grey
 * one-liners ("Nothing uploaded yet.") that said the first and none of the rest.
 */
export function EmptyState({
  illustration,
  title,
  description,
  action,
  compact = false,
  className,
}: {
  /** An SVG from `components/illustrations`, drawn in the brand colour. */
  illustration?: ReactNode
  title: ReactNode
  description?: ReactNode
  /** The next step: usually a button. */
  action?: ReactNode
  compact?: boolean
  className?: string
}) {
  return (
    <div
      className={cn(
        "animate-reveal flex flex-col items-center rounded-xl border border-dashed bg-card/60 text-center",
        compact ? "px-5 py-8" : "px-6 py-12",
        className
      )}
    >
      {illustration ? (
        <div
          aria-hidden
          className={cn("animate-float text-brand", compact ? "mb-3" : "mb-5")}
        >
          {illustration}
        </div>
      ) : null}
      <p className="text-base font-semibold tracking-tight">{title}</p>
      {description ? (
        <p className="mt-1.5 max-w-md text-sm text-pretty text-muted-foreground">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  )
}
