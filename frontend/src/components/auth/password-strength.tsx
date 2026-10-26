import { cn } from "@/lib/utils"

type Level = { score: 0 | 1 | 2 | 3 | 4; label: string }

/**
 * A rough read of how hard a password is to guess. ADVISORY ONLY: the server's
 * rules (length, not a common password, not built from the email) are the only
 * validation, and this never blocks or allows a submit. It exists so that a
 * person typing "password1" sees a red bar before they press the button, not
 * after.
 */
function assess(password: string): Level {
  if (!password) return { score: 0, label: "" }
  // Under the server's minimum there is nothing to grade yet.
  if (password.length < 10) return { score: 0, label: "Too short" }
  let score = 1 // meets the minimum
  if (password.length >= 14) score += 1
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length
  if (kinds >= 3) score += 1
  if (/\s/.test(password) || password.length >= 20) score += 1 // a passphrase
  const level = Math.min(4, score) as Level["score"]
  return { score: level, label: (["Too short", "Weak", "Okay", "Good", "Strong"] as const)[level] }
}
const COLOURS = [
  "bg-destructive",
  "bg-destructive",
  "bg-warning",
  "bg-brand",
  "bg-success",
] as const

export function PasswordStrength({ password, className }: { password: string; className?: string }) {
  const { score, label } = assess(password)
  if (!password) return null
  return (
    <div className={cn("mt-2", className)}>
      <div className="flex gap-1" aria-hidden>
        {[1, 2, 3, 4].map((segment) => (
          <span
            key={segment}
            className={cn(
              "h-1 flex-1 rounded-full transition-colors duration-300",
              segment <= score ? COLOURS[score] : "bg-muted"
            )}
          />
        ))}
      </div>
      <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">
        Strength: <span className="font-medium text-foreground">{label}</span>
      </p>
    </div>
  )
}
