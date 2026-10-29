/**
 * The way in to the product.
 *
 * Signing in is an email and a password. The form renders whatever
 * /auth/capabilities reports, resolved through lib/auth-methods. Snowflake is
 * not a way in: it is a data source connected after signing in.
 *
 * The frame (brand panel, card, theme toggle) is AuthLayout, shared with the
 * sign-up, forgot, reset, verify and invite pages.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"

import { AuthLayout } from "@/components/auth/auth-layout"
import { FormNotice } from "@/components/auth/form-notice"
import { PasswordInput } from "@/components/auth/password-input"
import { ResendLink } from "@/components/resend-link"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { useAuth } from "@/contexts/auth-context"
import { AccountRequestError, signInWithPassword } from "@/lib/accounts-api"
import { api, fetchAuthCapabilities, type AuthCapabilities } from "@/lib/api"
import { availableMethods, type AuthMethod } from "@/lib/auth-methods"
import { SHOW_CONNECTORS } from "@/lib/features"
import { FILES_WORKSPACE } from "@/lib/workspace"

const LINK = "font-medium text-foreground underline-offset-4 hover:text-brand-strong hover:underline"

export default function LoginPage() {
  const { user, refreshUser } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const [capabilities, setCapabilities] = useState<AuthCapabilities | null>(null)
  const [probeFailed, setProbeFailed] = useState(false)
  const [pending, setPending] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  /* Set when the password was right but the email is not confirmed yet. */
  const [unconfirmed, setUnconfirmed] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})

  /* A redirect back to this page reports why with ?error=. */
  const redirectError = searchParams.get("error")

  useEffect(() => {
    if (user) navigate(SHOW_CONNECTORS ? "/" : FILES_WORKSPACE.base, { replace: true })
  }, [user, navigate])

  useEffect(() => {
    void fetchAuthCapabilities()
      .then(setCapabilities)
      .catch(() => setProbeFailed(true))
  }, [])

  const methods = useMemo(() => availableMethods(capabilities), [capabilities])
  /* Lowest order leads; anything else real is still offered, just not as the
     filled button. A deployment running password *and* SSO during a migration
     has to show both. */
  const [primary, ...alternates] = methods.filter((m) => !m.secondary)
  const secondary = methods.filter((m) => m.secondary)

  const run = useCallback(
    async (method: AuthMethod) => {
      setFormError(null)
      setUnconfirmed(null)

      setPending(method.id)
      try {
        if (method.id === "password") {
          await signInWithPassword(values.email ?? "", values.password ?? "")
        } else {
          await api(method.endpoint!, {
            method: "POST",
            json: method.kind === "credentials" ? values : undefined,
          })
        }
        await refreshUser()
      } catch (err) {
        if (err instanceof AccountRequestError && err.code === "email_not_verified") {
          setUnconfirmed(err.email)
        }
        setFormError(err instanceof Error ? err.message : "Sign-in failed")
      } finally {
        setPending(null)
      }
    },
    [refreshUser, values]
  )

  return (
    <AuthLayout>
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Welcome back. Enter your details to continue.
      </p>

      <div className="mt-6 space-y-3">
        {redirectError ? <FormNotice tone="error">{redirectError}</FormNotice> : null}
        {formError ? <FormNotice tone="error">{formError}</FormNotice> : null}
        {unconfirmed ? <ResendLink email={unconfirmed} /> : null}
      </div>

      <div className="mt-6 space-y-4">
        {capabilities === null && !probeFailed ? (
          <div className="space-y-4" aria-hidden>
            <div className="shimmer h-11 rounded-lg" />
            <div className="shimmer h-11 rounded-lg" />
            <div className="shimmer h-11 rounded-lg" />
          </div>
        ) : null}

        {probeFailed ? (
          <FormNotice tone="warn">
            Could not reach the server to check which sign-in methods are available.
            Refresh, or contact your administrator if this persists.
          </FormNotice>
        ) : null}

        {capabilities !== null && methods.length === 0 ? (
          <FormNotice tone="warn">
            No sign-in method is configured on this deployment yet. An administrator
            needs to finish setting up authentication.
          </FormNotice>
        ) : null}

        {primary ? (
          <MethodForm
            method={primary}
            values={values}
            onChange={(name, value) => setValues((v) => ({ ...v, [name]: value }))}
            onSubmit={() => run(primary)}
            pending={pending === primary.id}
          />
        ) : null}

        {primary?.id === "password" && capabilities?.password_reset ? (
          <p className="text-right text-sm">
            <Link to="/forgot-password" className="text-muted-foreground underline-offset-4 hover:text-brand-strong hover:underline">
              Forgot password?
            </Link>
          </p>
        ) : null}

        {alternates.length > 0 ? (
          <div className="relative py-2 text-center">
            <span className="absolute inset-x-0 top-1/2 border-t" />
            <span className="relative bg-card px-3 text-xs text-muted-foreground">or</span>
          </div>
        ) : null}

        {alternates.map((method) => (
          <div key={method.id} className="space-y-2">
            <MethodForm
              method={method}
              variant="outline"
              values={values}
              onChange={(name, value) => setValues((v) => ({ ...v, [name]: value }))}
              onSubmit={() => run(method)}
              pending={pending === method.id}
            />
            {method.description ? (
              <p className="text-xs leading-relaxed text-muted-foreground">{method.description}</p>
            ) : null}
          </div>
        ))}
      </div>

      {secondary.length > 0 ? (
        <div className="mt-8 border-t pt-6">
          {secondary.map((method) => (
            <div key={method.id} className="space-y-2">
              <Button
                variant="outline"
                className="h-10 w-full"
                onClick={() => void run(method)}
                disabled={pending !== null}
              >
                {pending === method.id ? <Spinner /> : null}
                {method.label}
              </Button>
              {method.description ? (
                <p className="text-xs leading-relaxed text-muted-foreground">{method.description}</p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-8 border-t pt-5 text-sm text-muted-foreground">
        No account yet?{" "}
        <Link to="/signup" className={LINK}>
          Create one
        </Link>
      </div>
    </AuthLayout>
  )
}

/**
 * One method, drawn according to its kind.
 *
 * A credentials method is a real form: submit on Enter, and the browser's
 * password manager can see the fields, which it cannot if the inputs are
 * assembled outside a <form>.
 */
function MethodForm({
  method,
  values,
  onChange,
  onSubmit,
  pending,
  variant = "brand",
}: {
  method: AuthMethod
  values: Record<string, string>
  onChange: (name: string, value: string) => void
  onSubmit: () => void
  pending: boolean
  variant?: "brand" | "outline"
}) {
  const button = (
    <Button
      type={method.kind === "credentials" ? "submit" : "button"}
      size="lg"
      variant={variant}
      className="press h-11 w-full gap-2"
      disabled={pending}
      onClick={method.kind === "credentials" ? undefined : onSubmit}
    >
      {pending ? <Spinner /> : null}
      {method.label}
    </Button>
  )

  if (method.kind !== "credentials") return button

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      {(method.fields ?? []).map((field) => (
        <div key={field.name} className="space-y-1.5">
          <Label htmlFor={field.name}>{field.label}</Label>
          {field.type === "password" ? (
            <PasswordInput
              id={field.name}
              name={field.name}
              autoComplete={field.autoComplete}
              placeholder={field.placeholder}
              value={values[field.name] ?? ""}
              onChange={(e) => onChange(field.name, e.target.value)}
              className="h-11 text-base sm:text-sm"
            />
          ) : (
            <Input
              id={field.name}
              name={field.name}
              type={field.type}
              autoComplete={field.autoComplete}
              placeholder={field.placeholder}
              value={values[field.name] ?? ""}
              onChange={(e) => onChange(field.name, e.target.value)}
              className="h-11 text-base sm:text-sm"
            />
          )}
        </div>
      ))}
      {button}
    </form>
  )
}
