import { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"

import { AccountFormShell } from "@/components/account-form"
import { FormNotice } from "@/components/auth/form-notice"
import { Spinner } from "@/components/ui/spinner"
import { useAuth } from "@/contexts/auth-context"
import { verifyEmail } from "@/lib/accounts-api"
import { useLinkToken } from "@/lib/link-token"

/** Where the confirmation link lands: confirms the email and signs in. */
export default function VerifyPage() {
  const token = useLinkToken()
  const { refreshUser } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  /* The link is single use. React runs effects twice in development, and a
     second attempt would report a spent link as the result. */
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    void verifyEmail(token)
      .then(async () => {
        await refreshUser()
        navigate("/", { replace: true })
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "This link could not be used.")
      )
  }, [token, refreshUser, navigate])

  if (error) {
    return (
      <AccountFormShell title="This link cannot be used">
        <FormNotice tone="error">{error}</FormNotice>
      </AccountFormShell>
    )
  }
  return (
    <AccountFormShell title="Confirming your email">
      <p className="flex items-center gap-2.5 text-sm text-muted-foreground">
        <Spinner label="Confirming" />
        One moment…
      </p>
    </AccountFormShell>
  )
}
