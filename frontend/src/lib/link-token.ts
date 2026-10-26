import { useEffect, useState } from "react"
import { useParams } from "react-router-dom"

/**
 * The one-time token in an emailed link (confirm, invite, reset).
 *
 * New links carry it in the URL FRAGMENT (`/reset#<token>`). A browser never
 * sends the fragment to a server, so the token is in no access log on the way
 * here; the page posts it to the API in a request body instead. It is removed
 * from the address bar once read, so it is not left in history or a screenshot.
 *
 * Links emailed before that change carry it in the path (`/reset/<token>`);
 * those are still read, so an invitation already sent keeps working.
 */
export function useLinkToken(): string {
  const { token: legacy = "" } = useParams()
  const [token] = useState(() => window.location.hash.replace(/^#/, "") || legacy)

  useEffect(() => {
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search)
    }
  }, [])

  return token
}