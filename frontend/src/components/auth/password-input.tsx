import { useState, type ComponentProps, type KeyboardEvent } from "react"
import { EyeIcon, EyeOffIcon } from "lucide-react"

import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

/**
 * A password field with a show/hide button and a Caps Lock warning.
 *
 * It is an ordinary `<input>` underneath, so the `name`, `id`, `autoComplete`,
 * value and handlers the pages pass are exactly what reaches the browser (and
 * its password manager). Only `type` flips between password and text.
 */
export function PasswordInput({
  className,
  onKeyDown,
  onKeyUp,
  onBlur,
  ...props
}: Omit<ComponentProps<typeof Input>, "type">) {
  const [shown, setShown] = useState(false)
  const [capsLock, setCapsLock] = useState(false)

  const track = (event: KeyboardEvent<HTMLInputElement>) =>
    setCapsLock(event.getModifierState("CapsLock"))

  return (
    <div>
      <div className="relative">
        <Input
          {...props}
          type={shown ? "text" : "password"}
          className={cn("pr-11", className)}
          onKeyDown={(event) => {
            track(event)
            onKeyDown?.(event)
          }}
          onKeyUp={(event) => {
            track(event)
            onKeyUp?.(event)
          }}
          onBlur={(event) => {
            setCapsLock(false)
            onBlur?.(event)
          }}
        />
        <button
          type="button"
          onClick={() => setShown((value) => !value)}
          aria-label={shown ? "Hide password" : "Show password"}
          aria-pressed={shown}
          className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-lg text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          {shown ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
        </button>
      </div>
      {capsLock ? (
        <p role="status" className="mt-1.5 text-xs text-warning">
          Caps Lock is on.
        </p>
      ) : null}
    </div>
  )
}
