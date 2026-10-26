import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"

import { AccountFormShell, NewPasswordForm } from "@/components/account-form"
import { FormNotice } from "@/components/auth/form-notice"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/contexts/auth-context"
import { fetchReset, resetPassword } from "@/lib/accounts-api"
import { useLinkToken } from "@/lib/link-token"

/** Where a reset link lands: choose a new password. Every other session of the
 *  account ends, and this browser is signed in. */
export default function ResetPage() {
  const token = useLinkToken()
  const { refreshUser } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState<string | null>(null)
  const [invalid, setInvalid] = useState<string | null>(null)
  const checked = useRef(false)

  useEffect(() => {
    if (checked.current) return
    checked.current = true
    void fetchReset(token)
      .then((found) => setEmail(found.email))
      .catch((err: unknown) =>
        setInvalid(err instanceof Error ? err.message : "This reset link is not valid.")
      )
  }, [token])

  if (invalid) {
    return (
      <AccountFormShell title="This link cannot be used">
        <div className="space-y-4">
          <FormNotice tone="error">{invalid}</FormNotice>
          <Button asChild variant="brand" size="lg" className="press h-11 w-full">
            <Link to="/forgot-password">Ask for a new link</Link>
          </Button>
        </div>
      </AccountFormShell>
    )
  }

  return (
    <AccountFormShell
      title="Choose a new password"
      subtitle="You will be signed out everywhere else."
    >
      {email === null ? (
        <div className="space-y-4" aria-hidden>
          <div className="shimmer h-11 rounded-lg" />
          <div className="shimmer h-11 rounded-lg" />
          <div className="shimmer h-11 rounded-lg" />
        </div>
      ) : (
        <NewPasswordForm
          email={email}
          submitLabel="Set password and sign in"
          onSubmit={async (password) => {
            await resetPassword(token, password)
            await refreshUser()
            navigate("/", { replace: true })
          }}
        />
      )}
    </AccountFormShell>
  )
}
