import * as React from "react"
import { CheckIcon, CopyIcon, MailPlusIcon, XIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuth } from "@/contexts/auth-context"
import {
  fetchUsers,
  extendTrial,
  inviteUser,
  revokeInvitation,
  setUpSignIn,
  updateUser,
  type Account,
  type UsersPage as UsersState,
} from "@/lib/accounts-api"
import { errorText } from "@/lib/data-groups-api"
import { parseUtcTimestamp } from "@/lib/time"

function when(value: string | null): string {
  if (!value) return "Never"
  const at = parseUtcTimestamp(value)
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleString()
}

const SIGNUP_MODE_TEXT: Record<UsersState["signup_mode"], string> = {
  open: "Anyone who reaches the sign-in page can create an account.",
  invite: "New accounts need an invitation link from an administrator.",
  closed: "No new accounts can be created.",
}

/**
 * For an account that has no password (made by the Snowflake sign-in this app
 * no longer has): email its owner a link to choose one. It is the SAME
 * account, so its dashboards, chats and history stay with it. The link goes to
 * the person only; the administrator never sees it.
 */
function SignInSetup({
  account,
  busy,
  onSend,
}: {
  account: Account
  busy: boolean
  onSend: (email: string) => Promise<void>
}) {
  const [open, setOpen] = React.useState(false)
  const [address, setAddress] = React.useState(account.email ?? "")

  if (!open) {
    return (
      <div className="mt-1">
        <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" onClick={() => setOpen(true)}>
          <MailPlusIcon className="size-3.5" />
          Send sign-in link
        </Button>
      </div>
    )
  }
  return (
    <form
      className="mt-1 flex items-center gap-1.5"
      onSubmit={(e) => {
        e.preventDefault()
        void onSend(address.trim()).then(() => setOpen(false))
      }}
    >
      <Input
        type="email"
        required
        aria-label={`Email for ${account.username}`}
        placeholder="their email"
        className="h-7 w-44 text-xs"
        value={address}
        onChange={(e) => setAddress(e.target.value)}
      />
      <Button type="submit" size="sm" className="h-7 text-xs" disabled={busy || !address.trim()}>
        Send
      </Button>
      <Button type="button" size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </form>
  )
}

/**
 * Who can sign in, who is an administrator, and invitations.
 *
 * Every change goes to the server and the list is read again afterwards, so
 * what is shown is what is stored.
 */
export default function UsersPage() {
  const { user } = useAuth()
  const [state, setState] = React.useState<UsersState | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [email, setEmail] = React.useState("")
  const [asAdmin, setAsAdmin] = React.useState(false)
  const [newLink, setNewLink] = React.useState<{
    email: string
    link: string
    emailed: boolean
  } | null>(null)
  const [copied, setCopied] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setState(await fetchUsers())
      setError(null)
    } catch (err) {
      setError(errorText(err, "The list of users could not be loaded."))
    }
  }, [])

  React.useEffect(() => {
    void (async () => {
      await load()
    })()
  }, [load])

  const change = async (action: () => Promise<unknown>, done?: string) => {
    setBusy(true)
    try {
      await action()
      await load()
      if (done) toast.success(done)
    } catch (err) {
      toast.error(errorText(err, "The change was not saved."))
    } finally {
      setBusy(false)
    }
  }

  const invite = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      const made = await inviteUser(email.trim(), asAdmin)
      setNewLink({ email: made.invitation.email, link: made.link, emailed: made.emailed })
      setCopied(false)
      setEmail("")
      setAsAdmin(false)
      await load()
    } catch (err) {
      toast.error(errorText(err, "The invitation was not created."))
    } finally {
      setBusy(false)
    }
  }

  const copyLink = async () => {
    if (!newLink) return
    try {
      await navigator.clipboard.writeText(newLink.link)
      setCopied(true)
    } catch {
      toast.error("Copying failed. Select the link and copy it by hand.")
    }
  }

  if (state === null && !error) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-3 p-6">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  const isMe = (account: Account) =>
    !!user && account.username.toLowerCase() === user.username.toLowerCase()

  return (
    <div className="scrollbar-slim h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl space-y-6 p-6">
        <div>
          <h1 className="text-xl font-medium">Users</h1>
          <p className="text-sm text-muted-foreground">
            People who can sign in, and who administers this deployment.
            {state ? ` ${SIGNUP_MODE_TEXT[state.signup_mode]}` : null}
          </p>
        </div>

        {error ? (
          <p className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </p>
        ) : null}

        {/* ---------------------------------------------------- invite */}
        <section className="rounded-lg border p-4">
          <h2 className="text-sm font-medium">Invite someone</h2>
          <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={(e) => void invite(e)}>
            <div className="min-w-0 flex-1 space-y-1.5">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                required
                placeholder="name@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <label className="flex h-9 items-center gap-2 text-sm">
              <Checkbox checked={asAdmin} onCheckedChange={(v) => setAsAdmin(v === true)} />
              Administrator
            </label>
            <Button type="submit" disabled={busy || !email.trim()} className="gap-2">
              <MailPlusIcon className="size-4" />
              Create invitation link
            </Button>
          </form>

          {newLink ? (
            <div className="mt-4 rounded-md border bg-muted/40 p-3">
              <p className="text-xs text-muted-foreground">
                {newLink.emailed
                  ? `We emailed this link to ${newLink.email}. `
                  : `Send this link to ${newLink.email}. `}
                It works once, and only the newest link for an email works.
              </p>
              <div className="mt-2 flex min-w-0 items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded bg-background px-2 py-1.5 text-xs">
                  {newLink.link}
                </code>
                <Button size="sm" variant="outline" className="gap-1.5" onClick={() => void copyLink()}>
                  {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
                  {copied ? "Copied" : "Copy link"}
                </Button>
              </div>
            </div>
          ) : null}

          {state && state.invitations.length > 0 ? (
            <ul className="mt-4 divide-y rounded-md border text-sm">
              {state.invitations.map((inv) => (
                <li key={inv.invitation_id} className="flex min-w-0 items-center gap-3 px-3 py-2">
                  <span className="min-w-0 flex-1 truncate">{inv.email}</span>
                  {inv.is_admin ? <Badge variant="secondary">Administrator</Badge> : null}
                  <span className="hidden text-xs text-muted-foreground sm:inline">
                    Expires {when(inv.expires_at)}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="gap-1 text-muted-foreground"
                    disabled={busy}
                    onClick={() =>
                      void change(() => revokeInvitation(inv.invitation_id), "Invitation withdrawn")
                    }
                  >
                    <XIcon className="size-3.5" />
                    Withdraw
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        {/* ----------------------------------------------------- people */}
        <section className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Person</th>
                <th className="px-3 py-2 font-medium">Signs in with</th>
                <th className="px-3 py-2 font-medium">Last signed in</th>
                <th className="px-3 py-2 font-medium">Trial</th>
                <th className="px-3 py-2 font-medium">Administrator</th>
                <th className="px-3 py-2 font-medium">Can sign in</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {(state?.users ?? []).map((account) => (
                <tr key={account.user_id} className={account.is_active ? "" : "text-muted-foreground"}>
                  <td className="px-3 py-2">
                    <div className="font-medium">
                      {account.display_name || account.email || account.username}
                      {isMe(account) ? (
                        <span className="ml-1.5 text-xs font-normal text-muted-foreground">(you)</span>
                      ) : null}
                    </div>
                    {account.display_name && account.email ? (
                      <div className="text-xs text-muted-foreground">{account.email}</div>
                    ) : null}
                    {account.email && !account.email_verified ? (
                      <div className="text-xs text-amber-700 dark:text-amber-500">
                        Email not confirmed
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {[
                      account.has_password ? "Password" : null,
                      account.snowflake_user ? `Snowflake connected (${account.snowflake_user})` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "—"}
                    {!account.has_password && account.is_active ? (
                      <SignInSetup
                        account={account}
                        busy={busy}
                        onSend={(address) =>
                          change(
                            () => setUpSignIn(account.user_id, address),
                            `Sign-in link sent to ${address}`
                          )
                        }
                      />
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-xs">{when(account.last_login_at)}</td>
                  <td className="px-3 py-2 text-xs">
                    {account.trial ? (
                      <div className="flex items-center gap-2">
                        <span className={account.trial.expired ? "text-destructive" : ""}>
                          {account.trial.expired
                            ? "Ended"
                            : `${account.trial.days_left} day${account.trial.days_left === 1 ? "" : "s"} left`}
                        </span>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 px-2 text-xs"
                          disabled={busy}
                          onClick={() =>
                            void change(() => extendTrial(account.user_id, 14), "Trial extended by 14 days")
                          }
                        >
                          +14 days
                        </Button>
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2">
                      <label
                        className="flex items-center gap-2 text-xs"
                        title={
                          account.is_admin_by_setting
                            ? "Listed in ADMIN_EMAILS, so always an administrator"
                            : undefined
                        }
                      >
                        <Checkbox
                          checked={account.is_admin}
                          disabled={busy || isMe(account) || account.is_admin_by_setting}
                          onCheckedChange={(v) =>
                            void change(() => updateUser(account.user_id, { is_admin: v === true }))
                          }
                        />
                        {account.is_admin_by_setting ? "Set by ADMIN_EMAILS" : null}
                      </label>
                  </td>
                  <td className="px-3 py-2">
                    <Checkbox
                      checked={account.is_active}
                      disabled={busy || isMe(account)}
                      onCheckedChange={(v) =>
                        void change(
                          () => updateUser(account.user_id, { is_active: v === true }),
                          v === true ? "Account enabled" : "Account disabled"
                        )
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  )
}
