import * as React from "react"
import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  Loader2Icon,
  TriangleAlertIcon,
} from "lucide-react"

import { ConnectorLogo } from "@/components/connector-logo"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  createConnection,
  errorDetail,
  testConnection,
  type ConnectionInput,
  type ConnectionTest,
} from "@/lib/connections-api"

const EMPTY_FORM: ConnectionInput = {
  name: "",
  host: "",
  port: 1433,
  username: "",
  password: "",
}

/** The kinds of source a connection can be made to. One so far; the list is
 *  what this dialog offers, so a second vendor is one entry here. */
const SOURCE_TYPES = [
  {
    id: "sqlserver" as const,
    name: "Microsoft SQL Server",
    detail: "Connect to a database on a SQL Server instance",
  },
]

/**
 * Add a connection: choose the kind of source, then give its details.
 *
 * Mounted only while open, so each opening starts from an empty form and no
 * earlier password stays in memory.
 */
export function AddConnectionDialog({
  allowedHosts,
  onOpenChange,
  onSaved,
}: {
  allowedHosts: string[]
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [chosen, setChosen] = React.useState<
    (typeof SOURCE_TYPES)[number] | null
  >(null)
  const [form, setForm] = React.useState<ConnectionInput>(() => ({
    ...EMPTY_FORM,
    host: allowedHosts.length === 1 ? allowedHosts[0] : "",
  }))
  const [busy, setBusy] = React.useState<"test" | "save" | null>(null)
  const [result, setResult] = React.useState<ConnectionTest | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const fieldId = React.useId()

  function update(changes: Partial<ConnectionInput>) {
    setForm((current) => ({ ...current, ...changes }))
    // A result describes the details it was run with, not the edited ones.
    setResult(null)
    setError(null)
  }

  const complete =
    form.host.trim() !== "" &&
    form.username.trim() !== "" &&
    form.password !== "" &&
    Number.isFinite(form.port)

  async function run(action: "test" | "save") {
    setBusy(action)
    setError(null)
    try {
      if (action === "test") {
        setResult(await testConnection(form))
      } else {
        await createConnection(form)
        onSaved()
      }
    } catch (err) {
      setResult(null)
      setError(
        errorDetail(
          err,
          action === "test"
            ? "The test failed"
            : "Could not save the connection"
        )
      )
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-md" showCloseButton={!busy}>
        {chosen === null ? (
          <>
            <DialogHeader>
              <DialogTitle>Add a connection</DialogTitle>
              <DialogDescription>
                Choose the kind of source to connect to.
              </DialogDescription>
            </DialogHeader>

            <ul className="divide-y overflow-hidden rounded-lg border">
              {SOURCE_TYPES.map((type) => (
                <li key={type.id}>
                  <button
                    type="button"
                    onClick={() => setChosen(type)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
                  >
                    <ConnectorLogo kind={type.id} className="size-6" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {type.name}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {type.detail}
                      </span>
                    </span>
                    <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <DialogHeader>
              <div className="flex items-center gap-3">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="-ml-2 size-8 shrink-0"
                  aria-label="Back to source types"
                  disabled={busy !== null}
                  onClick={() => setChosen(null)}
                >
                  <ArrowLeftIcon className="size-4" />
                </Button>
                <ConnectorLogo kind={chosen.id} className="size-6" />
                <DialogTitle>{chosen.name}</DialogTitle>
              </div>
              <DialogDescription>
                Everyone signed in can use its databases. Use a read-only login.
              </DialogDescription>
            </DialogHeader>

            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault()
                if (complete && !busy) void run("save")
              }}
            >
              <Field id={`${fieldId}-name`} label="Name">
                <Input
                  id={`${fieldId}-name`}
                  value={form.name}
                  placeholder="Operations SQL Server"
                  onChange={(e) => update({ name: e.target.value })}
                />
              </Field>
              <div className="grid grid-cols-[1fr_6rem] gap-3">
                <Field id={`${fieldId}-host`} label="Host">
                  <Input
                    id={`${fieldId}-host`}
                    value={form.host}
                    autoComplete="off"
                    list={`${fieldId}-hosts`}
                    onChange={(e) => update({ host: e.target.value })}
                  />
                  <datalist id={`${fieldId}-hosts`}>
                    {allowedHosts.map((host) => (
                      <option key={host} value={host} />
                    ))}
                  </datalist>
                </Field>
                <Field id={`${fieldId}-port`} label="Port">
                  <Input
                    id={`${fieldId}-port`}
                    type="number"
                    min={1}
                    max={65535}
                    value={Number.isFinite(form.port) ? form.port : ""}
                    onChange={(e) => update({ port: e.target.valueAsNumber })}
                  />
                </Field>
              </div>
              {allowedHosts.length > 0 ? (
                <p className="-mt-1 text-xs text-muted-foreground">
                  Allowed hosts: {allowedHosts.join(", ")}
                </p>
              ) : (
                <p className="-mt-1 text-xs text-amber-700 dark:text-amber-500">
                  No hosts are allowed on this deployment. Set
                  DB_CONNECTION_ALLOWED_HOSTS on the backend.
                </p>
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field id={`${fieldId}-user`} label="Username">
                  <Input
                    id={`${fieldId}-user`}
                    value={form.username}
                    autoComplete="off"
                    onChange={(e) => update({ username: e.target.value })}
                  />
                </Field>
                <Field id={`${fieldId}-password`} label="Password">
                  <Input
                    id={`${fieldId}-password`}
                    type="password"
                    value={form.password}
                    autoComplete="new-password"
                    onChange={(e) => update({ password: e.target.value })}
                  />
                </Field>
              </div>

              {error ? (
                <p
                  role="alert"
                  className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
                >
                  <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
                  {error}
                </p>
              ) : result ? (
                <div className="rounded-lg border border-emerald-600/30 bg-emerald-600/5 p-3 text-xs">
                  <p className="flex items-center gap-1.5 font-medium text-emerald-700 dark:text-emerald-500">
                    <CheckCircle2Icon className="size-3.5" />
                    Connected
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    {result.server_version}
                  </p>
                  <p className="mt-1">
                    {result.databases.length === 0
                      ? "No databases found on this server for this login."
                      : `Databases: ${result.databases.join(", ")}`}
                  </p>
                </div>
              ) : null}

              <DialogFooter className="pt-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={!complete || busy !== null}
                  onClick={() => void run("test")}
                  className="gap-1.5"
                >
                  {busy === "test" ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : null}
                  Test
                </Button>
                <Button
                  type="submit"
                  disabled={!complete || busy !== null}
                  className="gap-1.5"
                >
                  {busy === "save" ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : null}
                  Save
                </Button>
              </DialogFooter>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function Field({
  id,
  label,
  children,
}: {
  id: string
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  )
}
