import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"

import {
  AccountFormShell,
  AlreadyHaveAnAccount,
  NewAccountForm,
} from "@/components/account-form"
import { SuccessMark } from "@/components/auth/success-mark"
import { ResendLink } from "@/components/resend-link"
import { useAuth } from "@/contexts/auth-context"
import { signUp } from "@/lib/accounts-api"
import { fetchAuthCapabilities } from "@/lib/api"

export default function SignupPage() {
  const { user, refreshUser } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState<boolean | null>(null)
  const [sentTo, setSentTo] = useState<string | null>(null)

  useEffect(() => {
    if (user) navigate("/", { replace: true })
  }, [user, navigate])

  useEffect(() => {
    void fetchAuthCapabilities()
      .then((c) => setOpen(c.signup === true))
      .catch(() => setOpen(false))
  }, [])

  if (sentTo) {
    return (
      <AccountFormShell title="Check your email">
        <div className="space-y-5 text-sm">
          <SuccessMark />
          <p className="text-muted-foreground">
            We sent a link to <span className="font-medium text-foreground">{sentTo}</span>.
            Open it to confirm your email and sign in.
          </p>
          <ResendLink email={sentTo} />
        </div>
      </AccountFormShell>
    )
  }

  if (open === false) {
    return (
      <AccountFormShell
        title="Accounts are by invitation"
        subtitle="Ask your administrator for an invitation link."
      >
        {null}
      </AccountFormShell>
    )
  }

  return (
    <AccountFormShell
      title="Create your account"
      subtitle="Start your 14-day free trial. It takes a minute."
      footer={<AlreadyHaveAnAccount />}
    >
      {open === null ? (
        <div className="space-y-4" aria-hidden>
          <div className="shimmer h-11 rounded-lg" />
          <div className="shimmer h-11 rounded-lg" />
          <div className="shimmer h-11 rounded-lg" />
        </div>
      ) : (
        <NewAccountForm
          submitLabel="Create account"
          requireName
          onSubmit={async ({ email, displayName, password }) => {
            const result = await signUp(email, password, displayName)
            if (result.status === "signed_in") {
              /* No confirmation step on this deployment: the account is
                 already signed in, so go straight to the app. */
              await refreshUser()
              return
            }
            setSentTo(result.email)
          }}
        />
      )}
    </AccountFormShell>
  )
}
