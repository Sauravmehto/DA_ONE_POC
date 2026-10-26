/**
 * App accounts: signing in with a password, invitations, linking Snowflake,
 * and the administrators' Users screen.
 *
 * Sign-in calls do not go through `api()`. There a 401 means "your session
 * expired" and signs the app out; here it means "wrong password", and the
 * person should stay on the form and read why.
 */

import { api, type AuthUser } from "@/lib/api"

const API_BASE = "/api/v1"

/** An error from the server, with its machine-readable code when it has one. */
export class AccountRequestError extends Error {
  code: string | null
  email: string | null

  constructor(message: string, code: string | null = null, email: string | null = null) {
    super(message)
    this.code = code
    this.email = email
  }
}

/** The server's `detail` as an error, or a fallback when the body is not what it
 *  should be. A detail may be a sentence, or `{message, code}`. */
async function errorOf(response: Response, fallback: string): Promise<AccountRequestError> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    const detail = body.detail
    if (typeof detail === "string" && detail) return new AccountRequestError(detail)
    if (detail && typeof detail === "object") {
      const d = detail as { message?: unknown; code?: unknown; email?: unknown }
      if (typeof d.message === "string") {
        return new AccountRequestError(
          d.message,
          typeof d.code === "string" ? d.code : null,
          typeof d.email === "string" ? d.email : null
        )
      }
    }
  } catch {
    /* not JSON */
  }
  return new AccountRequestError(fallback)
}

async function authRequest<T>(
  path: string,
  init: { method?: string; json?: unknown } = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: init.method ?? "POST",
    credentials: "include",
    headers: init.json !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init.json !== undefined ? JSON.stringify(init.json) : undefined,
  })
  if (!response.ok) {
    throw await errorOf(response, "Something went wrong. Try again.")
  }
  return (await response.json()) as T
}

export function signInWithPassword(email: string, password: string): Promise<AuthUser> {
  return authRequest<AuthUser>("/auth/password", { json: { email, password } })
}

/** Makes the account and emails a confirmation link. No session until the
 *  link is opened. */
export function signUp(
  email: string,
  password: string,
  displayName: string
): Promise<{ status: string; email: string }> {
  return authRequest("/auth/signup", {
    json: { email, password, display_name: displayName || null },
  })
}

/** Confirms the email a link was sent to, and signs that account in. */
export function verifyEmail(token: string): Promise<AuthUser> {
  return authRequest<AuthUser>("/auth/verify", { json: { token } })
}

/** A new confirmation link. Answers the same for any email. */
export function resendVerification(email: string): Promise<{ status: string }> {
  return authRequest("/auth/verify/resend", { json: { email } })
}

/** A new confirmation link for the signed-in account's own email. */
export function sendMyVerification(): Promise<{ status: string }> {
  return authRequest("/auth/account/verify")
}

/** Whether the active Snowflake role may save the semantic layer and report
 *  templates. Asks Snowflake for the role when it is not yet known. */
export function fetchSnowflakeAuthor(): Promise<{ can_author: boolean; role: string | null }> {
  return authRequest("/auth/snowflake/author", { method: "GET" })
}

export function fetchInvitation(token: string): Promise<{ email: string }> {
  return authRequest<{ email: string }>("/auth/invitations/check", { json: { token } })
}

export function acceptInvitation(
  token: string,
  password: string,
  displayName: string
): Promise<AuthUser> {
  return authRequest<AuthUser>("/auth/invitations/accept", {
    json: { token, password, display_name: displayName || null },
  })
}

/** Emails a reset link. Answers the same for any email. */
export function requestPasswordReset(email: string): Promise<{ status: string }> {
  return authRequest("/auth/password/forgot", { json: { email } })
}

/** Which email a reset link is for. */
export function fetchReset(token: string): Promise<{ email: string }> {
  return authRequest<{ email: string }>("/auth/password/reset/check", { json: { token } })
}

/** Sets the new password, ends the account's other sessions, signs this browser in. */
export function resetPassword(token: string, password: string): Promise<AuthUser> {
  return authRequest<AuthUser>("/auth/password/reset", { json: { token, password } })
}

export function changePassword(input: {
  newPassword: string
  currentPassword?: string
  email?: string
}): Promise<AuthUser> {
  return authRequest<AuthUser>("/auth/account/password", {
    json: {
      new_password: input.newPassword,
      current_password: input.currentPassword || null,
      email: input.email || null,
    },
  })
}

/** A full-page hand-off to Snowflake that comes back connected to this account.
 *  Snowflake is a data source, not a way to sign in. */
export function linkSnowflakeUrl(): string {
  return `${API_BASE}/auth/snowflake/connect`
}

export function unlinkSnowflake(): Promise<AuthUser> {
  return authRequest<AuthUser>("/auth/snowflake/unlink")
}

// ── Administration ─────────────────────────────────────────────────────────────

export type Account = {
  user_id: string
  username: string
  email: string | null
  display_name: string | null
  is_admin: boolean
  /** An administrator because ADMIN_EMAILS lists them; the flag cannot change that. */
  is_admin_by_setting: boolean
  email_verified: boolean
  is_active: boolean
  created_at: string
  created_by: string | null
  last_login_at: string | null
  has_password: boolean
  snowflake_user: string | null
}

export type Invitation = {
  invitation_id: string
  email: string
  is_admin: boolean
  created_by: string | null
  created_at: string
  expires_at: string
}

export type UsersPage = {
  users: Account[]
  invitations: Invitation[]
  signup_mode: "open" | "invite" | "closed"
}

export function fetchUsers(): Promise<UsersPage> {
  return api<UsersPage>("/users")
}

export function inviteUser(
  email: string,
  isAdmin: boolean
): Promise<{ invitation: Invitation; link: string; emailed: boolean }> {
  return api("/users/invitations", { method: "POST", json: { email, is_admin: isAdmin } })
}

export function revokeInvitation(invitationId: string): Promise<void> {
  return api<void>(`/users/invitations/${encodeURIComponent(invitationId)}`, {
    method: "DELETE",
  })
}

/** For an account with no way to sign in with a password: sets its email and
 *  emails the person a link to choose one. The account, and everything it
 *  owns, is the same one. */
export function setUpSignIn(userId: string, email: string): Promise<{ user: Account; emailed: boolean }> {
  return api(`/users/${encodeURIComponent(userId)}/sign-in-setup`, {
    method: "POST",
    json: { email },
  })
}

export function updateUser(
  userId: string,
  change: { is_admin?: boolean; is_active?: boolean }
): Promise<Account> {
  return api<Account>(`/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    json: change,
  })
}
