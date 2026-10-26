import type { ReactNode } from "react"

import { BrandPanel } from "@/components/auth/brand-panel"
import { BrandLockup } from "@/components/brand"
import { ThemeToggle } from "@/components/theme-toggle"

/**
 * The frame for sign-in, sign-up, forgot, reset, verify and invite: the brand
 * panel on the left and the form on the right, so they all look like one
 * product (they used to be a sign-in page and five bare centred columns).
 *
 * The page body does not scroll (index.css locks it so the app shell can manage
 * its own panes), so this scrolls itself on a short screen.
 */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="scrollbar-slim h-svh overflow-y-auto">
      <div className="grid min-h-svh lg:grid-cols-[1.1fr_1fr]">
        <BrandPanel />

        <main className="relative flex items-center justify-center bg-gradient-to-b from-background to-muted/50 px-5 py-12 sm:px-8">
          <div className="absolute top-4 right-4">
            <ThemeToggle />
          </div>

          <div className="w-full max-w-md">
            {/* The panel is hidden on phones, and an unbranded form is a
                phishing lesson, so the logo repeats here. */}
            <div className="mb-8 lg:hidden">
              <BrandLockup className="w-[9.5rem]" />
            </div>
            <div className="animate-reveal rounded-2xl border bg-card p-6 shadow-md sm:p-8">
              {children}
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
