import { Fragment, lazy, Suspense } from "react"

import { BrandLockup } from "@/components/brand"
import { ShowcaseCarousel } from "@/components/showcase-carousel"

// Loaded on demand, so the animated tour is not part of the page's first
// download.
const ProductTour = lazy(() => import("./product-tour"))

// Real screenshots, when someone adds them, take the tour's place. Counting
// them needs no image loaded (a lazy glob only lists the files).
const HAS_SHOWCASE =
  Object.keys(import.meta.glob("../../assets/showcase/*.{png,jpg,jpeg,webp,avif}")).length > 0

const HEADLINE = "Ask your data a question.".split(" ")

/** The slow-drifting colour behind the panel. Static for reduced motion. */
function Backdrop() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      <div className="animate-mesh absolute -top-32 -left-24 size-[34rem] rounded-full bg-[#4175B5] opacity-30 blur-3xl" />
      <div className="animate-mesh absolute -right-24 -bottom-32 size-[30rem] rounded-full bg-[#2A9D8F] opacity-25 blur-3xl [animation-delay:-6s] [animation-duration:22s]" />
      <div className="animate-mesh absolute top-1/3 left-1/3 size-[22rem] rounded-full bg-[#6FA0D6] opacity-15 blur-3xl [animation-delay:-11s] [animation-duration:26s]" />
      <div className="absolute inset-0 [background-image:radial-gradient(rgb(255_255_255/0.08)_1px,transparent_1px)] [background-size:26px_26px] [mask-image:radial-gradient(70%_70%_at_50%_45%,black,transparent)]" />
    </div>
  )
}

/**
 * The left side of every sign-in and account page. Hidden below `lg` rather
 * than stacked: on a phone it would push the form below the fold.
 *
 * Fixed dark colours, not bg-foreground/text-background: those invert with the
 * theme, which once turned this panel white in dark mode.
 */
export function BrandPanel() {
  return (
    <aside className="relative hidden overflow-hidden bg-[oklch(0.14_0.02_255)] p-12 text-neutral-50 lg:flex lg:flex-col lg:justify-between">
      <Backdrop />

      {/* The full lockup, tagline and all, because this is the one surface with
          the width to carry it. Knockout artwork: the wordmark is near-black
          and vanishes against this panel. */}
      <div className="animate-fade-in-soft relative">
        <BrandLockup variant="full" tone="inverse" className="w-[15rem]" />
      </div>

      <div className="relative max-w-xl">
        <p className="text-4xl leading-[1.12] font-semibold tracking-tight text-balance xl:text-5xl">
          {HEADLINE.map((word, i) => (
            <Fragment key={i}>
              <span
                className="animate-reveal inline-block"
                style={{ animationDelay: `${150 + i * 80}ms` }}
              >
                {word}
              </span>{" "}
            </Fragment>
          ))}
        </p>
        <p
          className="animate-reveal mt-4 text-base text-white/60"
          style={{ animationDelay: "650ms" }}
        >
          Answers, dashboards and reports — from your own warehouse.
        </p>

        <div className="animate-reveal mt-10" style={{ animationDelay: "800ms" }}>
          {HAS_SHOWCASE ? (
            <ShowcaseCarousel />
          ) : (
            <Suspense fallback={<div className="h-[22rem]" />}>
              <ProductTour />
            </Suspense>
          )}
        </div>
      </div>

      <p className="animate-fade-in-soft relative text-xs text-white/40" style={{ animationDelay: "900ms" }}>
        Runs in your own cloud account.
      </p>
    </aside>
  )
}
