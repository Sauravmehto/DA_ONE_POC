import { useState, type FormEvent } from "react"

import { AccountFormShell, FormError } from "@/components/account-form"
import { SuccessMark } from "@/components/auth/success-mark"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { requestPasswordReset } from "@/lib/accounts-api"

/** Ask for a reset link. Says the same thing whether or not the email has an
 *  account, so it cannot be used to find out who does. */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("")
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      await requestPasswordReset(email)
      setSent(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Try again.")
    } finally {
      setPending(false)
    }
  }

  if (sent) {
    return (
      <AccountFormShell title="Check your email">
        <div className="space-y-5 text-sm">
          <SuccessMark />
          <p className="text-muted-foreground">
            If <span className="font-medium text-foreground">{email}</span> has an account, we
            sent it a link to choose a new password. It works once and expires soon.
          </p>
        </div>
      </AccountFormShell>
    )
  }

  return (
    <AccountFormShell
      title="Reset your password"
      subtitle="Enter your email and we will send you a link."
    >
      <form className="space-y-4" onSubmit={(e) => void submit(e)}>
        {error ? <FormError>{error}</FormError> : null}
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" name="email" type="email" required autoComplete="username"
            placeholder="you@company.com" value={email} className="h-11 text-base sm:text-sm"
            onChange={(e) => setEmail(e.target.value)} />
        </div>
        <Button type="submit" variant="brand" size="lg" className="press h-11 w-full gap-2" disabled={pending}>
          {pending ? <Spinner /> : null}
          Send reset link
        </Button>
      </form>
    </AccountFormShell>
  )
}
