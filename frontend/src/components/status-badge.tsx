import type { ComponentProps } from "react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const TONES = {
  success: "bg-success-soft text-success",
  neutral: "bg-muted text-muted-foreground",
  danger: "bg-destructive/10 text-destructive dark:bg-destructive/20",
  warning: "bg-warning-soft text-warning",
  info: "bg-brand-soft text-brand-strong",
} as const

export type StatusTone = keyof typeof TONES

/**
 * A status as a coloured dot and a word: "Connected", "Not connected", "Error".
 * Colour is never the only signal (the word is always there). `live` makes the
 * dot pulse, for something that is up right now.
 */
export function StatusBadge({
  tone = "neutral",
  live = false,
  className,
  children,
  ...props
}: Omit<ComponentProps<typeof Badge>, "variant"> & {
  tone?: StatusTone
  live?: boolean
}) {
  return (
    <Badge
      className={cn("h-6 gap-1.5 px-2.5 font-medium", TONES[tone], className)}
      {...props}
    >
      <span
        aria-hidden
        className={cn("size-1.5 shrink-0 rounded-full bg-current", live && "pulse-ring")}
      />
      {children}
    </Badge>
  )
}
