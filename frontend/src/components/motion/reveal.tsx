import type { ElementType, HTMLAttributes } from "react"

import { cn } from "@/lib/utils"

type Props = HTMLAttributes<HTMLElement> & {
  /** The element to render. Default `div`. */
  as?: ElementType
}

/**
 * Fades and lifts its content in when it mounts.
 *
 * `delay` (ms) lets a few neighbouring blocks arrive one after another. The
 * content is in the DOM and readable from the first frame; only its opacity
 * and position animate, so a slow animation can never hide anything. Reduced
 * motion is handled once, globally, in index.css.
 */
export function Reveal({
  as,
  delay = 0,
  className,
  style,
  ...props
}: Props & { delay?: number }) {
  const Comp: ElementType = as ?? "div"
  return (
    <Comp
      className={cn("animate-reveal", className)}
      style={delay ? { animationDelay: `${delay}ms`, ...style } : style}
      {...props}
    />
  )
}

/**
 * A container whose children enter one after another (45 ms apart, the first
 * twelve; the rest arrive together so a long list never makes anyone wait).
 * It adds no wrapper around the children, so a `ul` stays a valid list.
 */
export function Stagger({ as, className, ...props }: Props) {
  const Comp: ElementType = as ?? "div"
  return <Comp className={cn("stagger", className)} {...props} />
}
