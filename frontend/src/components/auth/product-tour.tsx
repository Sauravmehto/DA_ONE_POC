import { useEffect, useState } from "react"
import { SparklesIcon } from "lucide-react"

import { cn } from "@/lib/utils"

/**
 * A looping, purely decorative demonstration of the product for the sign-in
 * panel: a question is typed, the app "thinks", and an answer draws itself.
 * Built from code rather than a GIF: sharp at any size, a few KB, no files to
 * record or keep in step with the real UI, and it stands still for anyone who
 * asked their system for reduced motion.
 *
 * The numbers are made up and the frame says so. Real screenshots dropped into
 * `assets/showcase/` replace this (see brand-panel.tsx).
 */

const QUESTION = "Which vintages are running above 2% delinquency?"
const BARS = [
  { year: "2021", value: 1.1 },
  { year: "2022", value: 1.8 },
  { year: "2023", value: 2.6 },
  { year: "2024", value: 3.1 },
  { year: "2025", value: 2.2 },
]
const MAX = 3.5

// One timeline, in milliseconds. Everything on screen is derived from the clock,
// so the loop can never drift out of step with itself.
const TYPE_START = 700
const TYPE_STEP = 34
const TYPE_END = TYPE_START + QUESTION.length * TYPE_STEP
const ANSWER_AT = TYPE_END + 1500
const LOOP_END = ANSWER_AT + 6500
const TICK = 60

export default function ProductTour({ className }: { className?: string }) {
  const [reduced] = useState(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  )
  const [clock, setClock] = useState(0)

  useEffect(() => {
    if (reduced) return
    const id = window.setInterval(
      () => setClock((c) => (c + TICK >= LOOP_END ? 0 : c + TICK)),
      TICK
    )
    return () => window.clearInterval(id)
  }, [reduced])

  // With reduced motion, show the finished answer and hold still.
  const t = reduced ? LOOP_END - 800 : clock
  const typed = Math.max(0, Math.min(QUESTION.length, Math.floor((t - TYPE_START) / TYPE_STEP)))
  const typing = t >= TYPE_START && typed < QUESTION.length
  const thinking = t >= TYPE_END && t < ANSWER_AT
  const answered = t >= ANSWER_AT

  return (
    <div
      aria-hidden
      className={cn(
        "overflow-hidden rounded-xl border border-white/10 bg-white/[0.04] shadow-2xl backdrop-blur-sm",
        className
      )}
    >
      <div className="flex items-center gap-1.5 border-b border-white/10 px-4 py-2.5">
        <span className="size-2.5 rounded-full bg-white/15" />
        <span className="size-2.5 rounded-full bg-white/15" />
        <span className="size-2.5 rounded-full bg-white/15" />
        <span className="ml-3 text-[11px] text-white/40">Sample data, for illustration</span>
      </div>

      <div className="min-h-[19rem] space-y-4 p-5">
        <div className="flex justify-end">
          <p
            className={cn(
              "max-w-[85%] rounded-2xl rounded-br-md bg-white/10 px-4 py-2.5 text-sm text-white/90 transition-opacity duration-300",
              t >= TYPE_START ? "opacity-100" : "opacity-0"
            )}
          >
            {QUESTION.slice(0, typed)}
            {typing ? <span className="animate-caret ml-px inline-block h-4 w-px translate-y-0.5 bg-white/80" /> : null}
          </p>
        </div>

        <div className="flex items-start gap-3">
          <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[#6FA0D6] to-[#2A9D8F]">
            <SparklesIcon className="size-3.5 text-[#0B1220]" />
          </span>
          <div className="min-w-0 flex-1 space-y-3">
            {thinking ? (
              <div className="space-y-2 pt-1.5">
                <div className="shimmer h-2.5 w-3/4 rounded-full !bg-white/10" />
                <div className="shimmer h-2.5 w-1/2 rounded-full !bg-white/10" />
              </div>
            ) : null}

            {answered ? (
              <>
                <p className="animate-reveal text-sm leading-relaxed text-white/85">
                  Three vintages are above 2%: <b className="font-semibold text-white">2023</b>,{" "}
                  <b className="font-semibold text-white">2024</b> and{" "}
                  <b className="font-semibold text-white">2025</b>. 2024 is highest at{" "}
                  <b className="font-semibold text-white tabular-nums">3.1%</b>.
                </p>
                <div className="rounded-lg border border-white/10 bg-black/20 p-4">
                  <div className="flex h-24 items-end gap-3">
                    {BARS.map((bar, i) => {
                      const high = bar.value > 2
                      return (
                        <div key={bar.year} className="flex h-full flex-1 flex-col justify-end">
                          <span
                            className={cn(
                              "mb-1 text-center text-[10px] tabular-nums transition-opacity duration-500",
                              high ? "text-[#9CC0E6]" : "text-white/40"
                            )}
                            style={{ transitionDelay: `${300 + i * 90}ms` }}
                          >
                            {bar.value.toFixed(1)}%
                          </span>
                          <div
                            className={cn(
                              "origin-bottom animate-reveal rounded-t-md",
                              high ? "bg-[#4175B5]" : "bg-white/20"
                            )}
                            style={{
                              height: `${(bar.value / MAX) * 100}%`,
                              animationDelay: `${i * 90}ms`,
                              animationDuration: "700ms",
                            }}
                          />
                        </div>
                      )
                    })}
                  </div>
                  <div className="mt-2 flex gap-3 text-center text-[10px] text-white/40">
                    {BARS.map((bar) => (
                      <span key={bar.year} className="flex-1">
                        {bar.year}
                      </span>
                    ))}
                  </div>
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
