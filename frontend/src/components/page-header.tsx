import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * The title block at the top of a page: one size and weight everywhere (the
 * pages used to disagree), an optional icon, a one-line description, and a slot
 * for the page's main actions on the right.
 */
export function PageHeader({
  title,
  description,
  icon,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  icon?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <header
      className={cn(
        "mb-6 flex flex-wrap items-start justify-between gap-x-4 gap-y-3",
        className
      )}
    >
      <div className="animate-reveal min-w-0">
        <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight text-balance">
          {icon}
          {title}
        </h1>
        {description ? (
          <p className="mt-1.5 max-w-2xl text-sm text-pretty text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="animate-reveal flex shrink-0 flex-wrap items-center gap-2 [animation-delay:80ms]">
          {actions}
        </div>
      ) : null}
    </header>
  )
}
