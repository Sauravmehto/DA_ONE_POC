import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"

import { AccountFormShell, NewAccountForm } from "@/components/account-form"
import { FormNotice } from "@/components/auth/form-notice"
import { useAuth } from "@/contexts/auth-context"
import { acceptInvitation, fetchInvitation } from "@/lib/accounts-api"
import { useLinkToken } from "@/lib/link-token"

export default function InvitePage() {
  const token = useLinkToken()
  const { user, refreshUser } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState<string | null>(null)
  const [invalid, setInvalid] = useState<string | null>(null)

  useEffect(() => {
    if (user) navigate("/", { replace: true })
  }, [user, navigate])

  useEffect(() => {
    void fetchInvitation(token)
      .then((invitation) => setEmail(invitation.email))
      .catch((err: unknown) =>
        setInvalid(err instanceof Error ? err.message : "This invitation link is not valid.")
      )
  }, [token])

  if (invalid) {
    return (
      <AccountFormShell title="This invitation cannot be used">
        <FormNotice tone="error">{invalid}</FormNotice>
      </AccountFormShell>
    )
  }

  return (
    <AccountFormShell
      title="Set up your account"
      subtitle="Choose a password to finish your invitation."
    >
      {email === null ? (
        <div className="space-y-4" aria-hidden>
          <div className="shimmer h-11 rounded-lg" />
          <div className="shimmer h-11 rounded-lg" />
          <div className="shimmer h-11 rounded-lg" />
        </div>
      ) : (
        <NewAccountForm
          fixedEmail={email}
          submitLabel="Create account"
          onSubmit={async ({ displayName, password }) => {
            await acceptInvitation(token, password, displayName)
            await refreshUser()
          }}
        />
      )}
    </AccountFormShell>
  )
}
