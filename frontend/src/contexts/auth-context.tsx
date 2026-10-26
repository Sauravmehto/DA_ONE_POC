import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { toast } from "sonner"

import { linkSnowflakeUrl } from "@/lib/accounts-api"
import {
  fetchAvailableRoles,
  fetchCurrentUser,
  logout as apiLogout,
  onSnowflakeDisconnected,
  onUnauthorized,
  setActiveRole,
  type AuthUser,
} from "@/lib/api"

/** How often, at most, coming back to the tab re-checks the session. */
const RECHECK_ON_RETURN_MS = 30 * 1000

type AuthContextValue = {
  user: AuthUser | null
  isLoading: boolean
  availableRoles: string[]
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  setRole: (role: string) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [availableRoles, setAvailableRoles] = useState<string[]>([])

  /* Whether the first check has finished. After it, only a real 401 signs the
     app out (through onUnauthorized below): a network blip or a server
     restarting is not a reason to throw away a working session. */
  const checkedOnce = useRef(false)

  const refreshUser = useCallback(async () => {
    try {
      const currentUser = await fetchCurrentUser()
      setUser(currentUser)
      try {
        const roles = await fetchAvailableRoles()
        setAvailableRoles(roles.roles)
      } catch {
        setAvailableRoles([])
      }
    } catch {
      if (!checkedOnce.current) {
        setUser(null)
        setAvailableRoles([])
      }
    } finally {
      checkedOnce.current = true
      setIsLoading(false)
    }
  }, [])

  // Track whether the user is considered logged in so we can avoid redundant
  // redirects when multiple 401s fire at once.
  const isLoggedInRef = useRef(false)
  useEffect(() => {
    isLoggedInRef.current = user !== null
  }, [user])

  useEffect(() => {
    void refreshUser()
  }, [refreshUser])

  // Periodic session validity check every 5 minutes
  useEffect(() => {
    const id = setInterval(() => {
      void refreshUser()
    }, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [refreshUser])

  // Coming back to the tab after a while: check the session before the page
  // is used, so an expired one goes to sign-in at once instead of opening
  // pages that then fail request by request.
  useEffect(() => {
    let last = Date.now()
    const recheck = () => {
      if (document.visibilityState !== "visible") return
      if (Date.now() - last < RECHECK_ON_RETURN_MS) return
      last = Date.now()
      void refreshUser()
    }
    window.addEventListener("focus", recheck)
    document.addEventListener("visibilitychange", recheck)
    return () => {
      window.removeEventListener("focus", recheck)
      document.removeEventListener("visibilitychange", recheck)
    }
  }, [refreshUser])

  // Snowflake stopped answering for this person while the app sign-in is
  // still good: offer to reconnect, once, rather than a failure on every
  // panel. The user is re-read so pages that depend on Snowflake notice.
  useEffect(() => {
    return onSnowflakeDisconnected((message) => {
      void refreshUser()
      toast.error(message, {
        id: "snowflake-disconnected",
        duration: 15000,
        action: {
          label: "Connect Snowflake",
          onClick: () => {
            window.location.href = linkSnowflakeUrl()
          },
        },
      })
    })
  }, [refreshUser])

  // Global 401 intercept — when any API call returns 401, clear the session
  // immediately so the auth guard redirects to login.
  useEffect(() => {
    const unsub = onUnauthorized(() => {
      if (isLoggedInRef.current) {
        setUser(null)
        setAvailableRoles([])
        isLoggedInRef.current = false
      }
    })
    return unsub
  }, [])


  const logout = useCallback(async () => {
    await apiLogout()
    setUser(null)
    setAvailableRoles([])
  }, [])

  const setRole = useCallback(async (role: string) => {
    const updated = await setActiveRole(role)
    setUser(updated)
  }, [])

  const value = useMemo(
    () => ({
      user,
      isLoading,
      availableRoles,
      logout,
      refreshUser,
      setRole,
    }),
    [user, isLoading, availableRoles, logout, refreshUser, setRole]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider")
  }
  return context
}
