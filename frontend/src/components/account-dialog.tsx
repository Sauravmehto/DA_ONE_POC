/**
 * The signed-in person's own account. Opens on a summary: who they are, how
 * they sign in, and the Snowflake user linked to them. Changing the password is
 * a step they choose, not the first thing shown.
 */

import { useState, type FormEvent, type ReactNode } from "react"
import { ArrowLeftIcon, CheckCircle2Icon, CircleAlertIcon, Loader2Icon } from "lucide-react"
import { toast } from "sonner"

import { FormError } from "@/components/account-form"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAuth } from "@/contexts/auth-context"
import {
  changePassword,
  linkSnowflakeUrl,
  sendMyVerification,
  unlinkSnowflake,
} from "@/lib/accounts-api"
import { userLabel, type AuthUser } from "@/lib/api"

export function AccountDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { user } = useAuth()
  const [view, setView] = useState<"summary" | "password">("summary")

  if (!user) return null

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) setView("summary")
        onOpenChange(value)
      }}
    >
      <DialogContent className="sm:max-w-md">
        {view === "summary" ? (
          <Summary user={user} onChangePassword={() => setView("password")} />
        ) : (
          <PasswordForm user={user} onDone={() => setView("summary")} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] items-start gap-3 py-2.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  )
}

function Summary({
  user,
  onChangePassword,
}: {
  user: AuthUser
  onChangePassword: () => void
}) {
  const { refreshUser } = useAuth()
  const [busy, setBusy] = useState<"verify" | "unlink" | null>(null)
  const hasAccount = user.auth_mode !== "keypair"

  const sendLink = async () => {
    setBusy("verify")
    try {
      await sendMyVerification()
      toast.success(`We sent a link to ${user.email}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "The link was not sent.")
    } finally {
      setBusy(null)
    }
  }

  const disconnect = async () => {
    setBusy("unlink")
    try {
      await unlinkSnowflake()
      await refreshUser()
      toast.success("Snowflake disconnected")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Snowflake was not disconnected.")
    } finally {
      setBusy(null)
    }
  }

  const ways = [
    user.has_password ? "Email and password" : null,
    user.snowflake_account ? "Snowflake" : null,
  ].filter(Boolean)

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          {userLabel(user)}
          {user.is_admin ? <Badge variant="secondary">Administrator</Badge> : null}
        </DialogTitle>
        <DialogDescription>Your account.</DialogDescription>
      </DialogHeader>

      {!hasAccount ? (
        <p className="text-sm text-muted-foreground">
          This is a developer sign-in with no account behind it.
        </p>
      ) : (
        <div className="divide-y">
          <Row label="Email">
            {user.email ? (
              <div className="space-y-1.5">
                <div className="truncate">{user.email}</div>
                {user.email_verified ? (
                  <span className="flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-500">
                    <CheckCircle2Icon className="size-3.5" /> Confirmed
                  </span>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-500">
                      <CircleAlertIcon className="size-3.5" /> Not confirmed
                    </span>
                    <Button size="sm" variant="outline" className="h-7 text-xs"
                      disabled={busy !== null} onClick={() => void sendLink()}>
                      Send confirmation link
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <span className="text-muted-foreground">None yet</span>
            )}
          </Row>

          <Row label="Signs in with">{ways.length ? ways.join(" · ") : "—"}</Row>

          <Row label="Snowflake">
            {user.snowflake_account ? (
              <div className="flex flex-wrap items-center gap-2">
                <span>
                  Connected as <span className="font-mono">{user.snowflake_account}</span>
                </span>
                {user.has_password ? (
                  <Button size="sm" variant="outline" className="h-7 text-xs"
                    disabled={busy !== null} onClick={() => void disconnect()}>
                    Disconnect
                  </Button>
                ) : null}
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-muted-foreground">Not connected</span>
                <Button size="sm" className="h-7 text-xs"
                  onClick={() => (window.location.href = linkSnowflakeUrl())}>
                  Connect Snowflake
                </Button>
              </div>
            )}
          </Row>

          {user.role ? (
            <Row label="Snowflake role">
              <span className="font-mono text-xs">{user.role}</span>
            </Row>
          ) : null}
        </div>
      )}

      {hasAccount ? (
        <div className="flex justify-end">
          <Button variant="outline" onClick={onChangePassword}>
            {user.has_password ? "Change password" : "Set a password"}
          </Button>
        </div>
      ) : null}
    </>
  )
}

function PasswordForm({ user, onDone }: { user: AuthUser; onDone: () => void }) {
  const { refreshUser } = useAuth()
  const [current, setCurrent] = useState("")
  const [email, setEmail] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const needsEmail = !user.email

  const save = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (next !== confirm) {
      setError("The two new passwords are different.")
      return
    }
    setPending(true)
    try {
      const updated = await changePassword({
        newPassword: next,
        currentPassword: user.has_password ? current : undefined,
        email: needsEmail ? email : undefined,
      })
      await refreshUser()
      toast.success(
        needsEmail && !updated.email_verified
          ? `Password set. Confirm ${updated.email} with the link we sent before signing in with it.`
          : user.has_password
            ? "Password changed"
            : "Password set"
      )
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : "The password was not saved.")
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{user.has_password ? "Change password" : "Set a password"}</DialogTitle>
        <DialogDescription>
          {user.has_password
            ? "Enter your current password, then the new one twice."
            : "Lets you sign in with your email and password as well as through Snowflake."}
        </DialogDescription>
      </DialogHeader>
      <form className="space-y-3" onSubmit={(e) => void save(e)}>
        {error ? <FormError>{error}</FormError> : null}
        {needsEmail ? (
          <div className="space-y-1.5">
            <Label htmlFor="account-email">Email</Label>
            <Input id="account-email" type="email" required autoComplete="username"
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        ) : null}
        {user.has_password ? (
          <div className="space-y-1.5">
            <Label htmlFor="account-current">Current password</Label>
            <Input id="account-current" type="password" required
              autoComplete="current-password" value={current}
              onChange={(e) => setCurrent(e.target.value)} />
          </div>
        ) : null}
        <div className="space-y-1.5">
          <Label htmlFor="account-new">New password</Label>
          <Input id="account-new" type="password" required autoComplete="new-password"
            value={next} onChange={(e) => setNext(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="account-confirm">New password again</Label>
          <Input id="account-confirm" type="password" required autoComplete="new-password"
            value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        <div className="flex items-center justify-between pt-1">
          <Button type="button" variant="ghost" className="gap-1.5" onClick={onDone}>
            <ArrowLeftIcon className="size-4" /> Back
          </Button>
          <Button type="submit" className="gap-2" disabled={pending}>
            {pending ? <Loader2Icon className="size-4 animate-spin" /> : null}
            {user.has_password ? "Change password" : "Set password"}
          </Button>
        </div>
      </form>
    </>
  )
}
