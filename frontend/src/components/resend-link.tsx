import { useState } from "react"
import { Loader2Icon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { resendVerification } from "@/lib/accounts-api"

/** "Send a new link" for an email waiting to be confirmed. */
export function ResendLink({ email }: { email: string }) {
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle")

  if (state === "sent") {
    return <p className="text-xs text-muted-foreground">A new link is on its way.</p>
  }
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="gap-2"
      disabled={state === "sending"}
      onClick={() => {
        setState("sending")
        void resendVerification(email)
          .then(() => setState("sent"))
          .catch(() => setState("idle"))
      }}
    >
      {state === "sending" ? <Loader2Icon className="size-4 animate-spin" /> : null}
      Send a new link
    </Button>
  )
}
