import type { ReactNode } from "react"
import { CheckCircle2Icon, InfoIcon, TriangleAlertIcon } from "lucide-react"

import { cn } from "@/lib/utils"

const TONES = {
  error: {
    box: "border-destructive/30 bg-destructive/5 text-destructive",
    Icon: TriangleAlertIcon,
    role: "alert" as const,
  },
  warn: {
    box: "border-warning/30 bg-warning-soft text-warning",
    Icon: TriangleAlertIcon,
    role: "status" as const,
  },
  success: {
    box: "border-success/30 bg-success-soft text-success",
    Icon: CheckCircle2Icon,
    role: "status" as const,
  },
  info: {
    box: "border-brand/25 bg-brand-soft text-brand-strong",
    Icon: InfoIcon,
    role: "status" as const,
  },
}

/**
 * One notice for every auth page. It replaces the two copies the pages used to
 * carry (`Notice` on sign-in, `FormError` on the account forms).
 */
export function FormNotice({
  tone = "error",
  children,
  className,
}: {
  tone?: keyof typeof TONES
  children: ReactNode
  className?: string
}) {
  const { box, Icon, role } = TONES[tone]
  return (
    <p
      role={role}
      className={cn(
        "animate-reveal flex items-start gap-2 rounded-lg border p-3 text-sm leading-relaxed",
        box,
        className
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  )
}
