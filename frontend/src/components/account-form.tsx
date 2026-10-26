/**
 * The frame and fields shared by the pages that create or recover an account:
 * signing up, accepting an invitation, forgot and reset password, confirming an
 * email. They sit inside AuthLayout, so they look like the sign-in page.
 */

import { useState, type FormEvent, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { ArrowLeftIcon } from "lucide-react"

import { AuthLayout } from "@/components/auth/auth-layout"
import { FormNotice } from "@/components/auth/form-notice"
import { PasswordInput } from "@/components/auth/password-input"
import { PasswordStrength } from "@/components/auth/password-strength"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"

const LINK = "font-medium text-foreground underline-offset-4 hover:text-brand-strong hover:underline"

/** The way out of any dead end: every account page can get back to sign-in. */
export function BackToSignIn() {
  return (
    <Link to="/login" className={`inline-flex items-center gap-1.5 ${LINK}`}>
      <ArrowLeftIcon className="size-3.5" aria-hidden />
      Back to sign in
    </Link>
  )
}

/** For the sign-up page, where someone may already have an account. */
export function AlreadyHaveAnAccount() {
  return (
    <>
      Already have an account?{" "}
      <Link to="/login" className={LINK}>
        Sign in
      </Link>
    </>
  )
}

export function AccountFormShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle?: ReactNode
  children: ReactNode
  /** What sits under the card's content. Default: a way back to sign-in. */
  footer?: ReactNode
}) {
  return (
    <AuthLayout>
      <h1 className="text-2xl font-semibold tracking-tight text-balance">{title}</h1>
      {subtitle ? (
        <p className="mt-2 text-sm text-pretty text-muted-foreground">{subtitle}</p>
      ) : null}
      <div className="mt-6">{children}</div>
      <div className="mt-8 border-t pt-5 text-sm text-muted-foreground">
        {footer ?? <BackToSignIn />}
      </div>
    </AuthLayout>
  )
}

export function FormError({ children }: { children: ReactNode }) {
  return (
    <FormNotice tone="error" className="mb-4">
      {children}
    </FormNotice>
  )
}

function PasswordHint() {
  return (
    <p className="mt-1.5 text-xs text-muted-foreground">
      At least 10 characters. A few words together work well; a common password or
      one that contains your email is refused.
    </p>
  )
}

const FIELD = "h-11 text-base sm:text-sm"

/** A button that says it is working, and cannot be pressed twice. */
function SubmitButton({ pending, children }: { pending: boolean; children: ReactNode }) {
  return (
    <Button type="submit" variant="brand" size="lg" className="press h-11 w-full gap-2" disabled={pending}>
      {pending ? <Spinner /> : null}
      {children}
    </Button>
  )
}

/**
 * Name, and a password typed twice. `email` is either a field (signing up) or
 * fixed and shown (an invitation is for one address).
 */
export function NewAccountForm({
  fixedEmail,
  submitLabel,
  requireName = false,
  onSubmit,
}: {
  fixedEmail?: string
  submitLabel: string
  /** Signing yourself up asks for your full name; an invitation does not. */
  requireName?: boolean
  onSubmit: (values: { email: string; displayName: string; password: string }) => Promise<void>
}) {
  const [email, setEmail] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError("The two passwords are different.")
      return
    }
    setPending(true)
    try {
      await onSubmit({ email: fixedEmail ?? email, displayName, password })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Try again.")
    } finally {
      setPending(false)
    }
  }

  return (
    <form className="space-y-4" onSubmit={(e) => void submit(e)}>
      {error ? <FormError>{error}</FormError> : null}
      <div className="space-y-1.5">
        <Label htmlFor="email">Email</Label>
        {fixedEmail ? (
          <Input id="email" name="email" type="email" value={fixedEmail} readOnly
            autoComplete="username" className={`${FIELD} bg-muted/50`} />
        ) : (
          <Input id="email" name="email" type="email" required autoComplete="username"
            placeholder="you@company.com" value={email} className={FIELD}
            onChange={(e) => setEmail(e.target.value)} />
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="display_name">{requireName ? "Full name" : "Name"}</Label>
        <Input id="display_name" name="display_name" autoComplete="name"
          required={requireName} maxLength={120} className={FIELD}
          value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new_password">Password</Label>
        <PasswordInput id="new_password" name="new_password" required
          autoComplete="new-password" value={password} className={FIELD}
          onChange={(e) => setPassword(e.target.value)} />
        <PasswordStrength password={password} />
        <PasswordHint />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="confirm_password">Password again</Label>
        <PasswordInput id="confirm_password" name="confirm_password" required
          autoComplete="new-password" value={confirm} className={FIELD}
          onChange={(e) => setConfirm(e.target.value)} />
      </div>
      <SubmitButton pending={pending}>{submitLabel}</SubmitButton>
    </form>
  )
}

/** A new password, typed twice, for someone who already has an account. */
export function NewPasswordForm({
  email,
  submitLabel,
  onSubmit,
}: {
  email: string
  submitLabel: string
  onSubmit: (password: string) => Promise<void>
}) {
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError("The two passwords are different.")
      return
    }
    setPending(true)
    try {
      await onSubmit(password)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Try again.")
    } finally {
      setPending(false)
    }
  }

  return (
    <form className="space-y-4" onSubmit={(e) => void submit(e)}>
      {error ? <FormError>{error}</FormError> : null}
      <div className="space-y-1.5">
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" value={email} readOnly
          autoComplete="username" className={`${FIELD} bg-muted/50`} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="new_password">New password</Label>
        <PasswordInput id="new_password" name="new_password" required
          autoComplete="new-password" value={password} className={FIELD}
          onChange={(e) => setPassword(e.target.value)} />
        <PasswordStrength password={password} />
        <PasswordHint />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="confirm_password">New password again</Label>
        <PasswordInput id="confirm_password" name="confirm_password" required
          autoComplete="new-password" value={confirm} className={FIELD}
          onChange={(e) => setConfirm(e.target.value)} />
      </div>
      <SubmitButton pending={pending}>{submitLabel}</SubmitButton>
    </form>
  )
}
