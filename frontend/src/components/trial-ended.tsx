import { AuthLayout } from "@/components/auth/auth-layout"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/contexts/auth-context"

/** What a person sees once their free trial has run out: a plain statement,
 *  not a broken app. The server refuses everything but /auth/me and sign-out. */
export function TrialEnded() {
  const { user, logout } = useAuth()

  return (
    <AuthLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Your free trial has ended</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The 14-day trial for {user?.email ?? "this account"} is over. To keep using
        DataAlphaOne, contact us and we will extend your access.
      </p>
      <div className="mt-6 flex gap-3">
        <Button asChild>
          <a href="mailto:admin-tech@dataalpha.ai?subject=Extend%20my%20DataAlphaOne%20trial">
            Contact us
          </a>
        </Button>
        <Button variant="outline" onClick={() => void logout()}>
          Sign out
        </Button>
      </div>
    </AuthLayout>
  )
}
