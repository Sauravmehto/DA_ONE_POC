/**
 * The product logo, in the forms the UI actually needs.
 *
 * The supplied artwork is a single lockup: the alpha glyph, the "DAOne"
 * wordmark, and a "DATA TO DECISIONS" tagline. That tagline stops being
 * readable below roughly 240px of width, so the assets here are cut from it
 * rather than scaled down from it:
 *
 *   daone-mark            the alpha glyph alone — the only form that survives
 *                         a 32px favicon or a sidebar row
 *   daone-logo-compact    glyph + wordmark, tagline removed — readable from
 *                         about 110px wide
 *   daone-logo            the complete lockup, for places with real width
 *
 * The wordmark is near-black, so it disappears on a dark surface; each lockup
 * therefore ships a white knockout alongside it. The glyph needs no such
 * treatment — its ring and arrow hold up on both.
 */

import logo from "@/assets/brand/daone-logo.png"
import logoInverse from "@/assets/brand/daone-logo-inverse.png"
import logoCompact from "@/assets/brand/daone-logo-compact.png"
import logoCompactInverse from "@/assets/brand/daone-logo-compact-inverse.png"
import mark from "@/assets/brand/daone-mark.png"
import { cn } from "@/lib/utils"

const ART = {
  full: { ink: logo, inverse: logoInverse },
  compact: { ink: logoCompact, inverse: logoCompactInverse },
}

/** The alpha glyph. Decorative wherever the product name is already spelled out. */
export function BrandMark({
  className,
  alt = "",
}: {
  className?: string
  alt?: string
}) {
  return (
    <img
      src={mark}
      alt={alt}
      aria-hidden={alt === "" || undefined}
      draggable={false}
      className={cn("h-6 w-auto shrink-0 select-none", className)}
    />
  )
}

/**
 * Glyph and wordmark together.
 *
 * `full` keeps the tagline and needs ~240px or more; `compact` drops it.
 *
 * `tone` picks the artwork for the surface underneath. "auto" is for surfaces
 * that follow the theme: both files render and CSS shows one, which keeps the
 * choice out of React and avoids a flash on first paint. Panels with a fixed
 * colour pass "ink" or "inverse" and ship a single image.
 */
export function BrandLockup({
  variant = "compact",
  tone = "auto",
  className,
}: {
  variant?: "full" | "compact"
  tone?: "ink" | "inverse" | "auto"
  className?: string
}) {
  const art = ART[variant]
  const base = cn("h-auto w-40 select-none", className)

  if (tone !== "auto") {
    return (
      <img
        src={tone === "inverse" ? art.inverse : art.ink}
        alt="DataAlphaOne"
        draggable={false}
        className={base}
      />
    )
  }

  return (
    <>
      <img
        src={art.ink}
        alt="DataAlphaOne"
        draggable={false}
        className={cn(base, "dark:hidden")}
      />
      <img
        src={art.inverse}
        alt=""
        aria-hidden
        draggable={false}
        className={cn(base, "hidden dark:block")}
      />
    </>
  )
}
