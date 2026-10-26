import { Loader2Icon } from "lucide-react"

import { cn } from "@/lib/utils"

/**
 * The one loading spinner. It keeps spinning under "reduce motion" (see
 * index.css): a frozen spinner would say nothing is happening, which is
 * information rather than decoration.
 */
function Spinner({
  className,
  label = "Loading",
}: {
  className?: string
  label?: string
}) {
  return (
    <Loader2Icon
      role="status"
      aria-label={label}
      className={cn("size-4 animate-spin", className)}
    />
  )
}

export { Spinner }
