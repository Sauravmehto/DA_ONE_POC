import { api } from "@/lib/api"

/**
 * Client for data groups: business groups that decide who sees which
 * databases, folders, dashboards and reports.
 *
 * Every write here is admin-only on the server; `fetchMyDataGroups` is the one
 * call any signed-in person makes.
 */

export type ResourceType = "database" | "folder" | "dashboard" | "report"
/** A group holds people. It once held warehouse roles too, from when the
 *  rules lived in the warehouse. */
export type MemberType = "USER"

export type DataGroupMember = {
  member_type: MemberType
  member_name: string
}

export type DataGroupResource = {
  resource_type: ResourceType
  resource_id: string
  is_main: boolean
}

export type DataGroup = {
  group_id: string
  name: string
  description: string
  members: DataGroupMember[]
  resources: DataGroupResource[]
}

export type DataGroupsState = {
  configured: boolean
  enforced: boolean
  setup_hint?: string
  groups: DataGroup[]
}

export type CatalogItem = { id: string; label: string }
export type DataGroupCatalog = Record<ResourceType, CatalogItem[]>

export const RESOURCE_TYPE_NAMES: Record<ResourceType, string> = {
  database: "Database",
  folder: "Folder",
  dashboard: "Dashboard",
  report: "Report",
}

const base = "/data-groups"
const enc = encodeURIComponent

export function fetchDataGroups(): Promise<DataGroupsState> {
  return api(base)
}

export function fetchDataGroupCatalog(): Promise<DataGroupCatalog> {
  return api(`${base}/catalog`)
}

export function fetchMyDataGroups(): Promise<{
  configured: boolean
  enforced: boolean
  is_admin: boolean
  groups: { group_id: string; name: string }[]
}> {
  return api(`${base}/mine`)
}

export function createDataGroup(name: string, description = ""): Promise<{ group_id: string }> {
  return api(base, { method: "POST", json: { name, description } })
}

export function updateDataGroup(
  groupId: string,
  changes: { name?: string; description?: string }
): Promise<{ group_id: string }> {
  return api(`${base}/${enc(groupId)}`, { method: "PATCH", json: changes })
}

export function deleteDataGroup(groupId: string): Promise<unknown> {
  return api(`${base}/${enc(groupId)}`, { method: "DELETE" })
}

export function addDataGroupMember(
  groupId: string,
  memberType: MemberType,
  memberName: string
): Promise<unknown> {
  return api(`${base}/${enc(groupId)}/members`, {
    method: "POST",
    json: { member_type: memberType, member_name: memberName },
  })
}

export function removeDataGroupMember(
  groupId: string,
  memberType: MemberType,
  memberName: string
): Promise<unknown> {
  return api(`${base}/${enc(groupId)}/members/${enc(memberType)}/${enc(memberName)}`, {
    method: "DELETE",
  })
}

export function assignToDataGroup(
  groupId: string,
  resourceType: ResourceType,
  resourceId: string,
  isMain = false
): Promise<unknown> {
  return api(`${base}/${enc(groupId)}/resources`, {
    method: "POST",
    json: { resource_type: resourceType, resource_id: resourceId, is_main: isMain },
  })
}

export function removeFromDataGroup(
  groupId: string,
  resourceType: ResourceType,
  resourceId: string
): Promise<unknown> {
  return api(`${base}/${enc(groupId)}/resources/${enc(resourceType)}/${enc(resourceId)}`, {
    method: "DELETE",
  })
}

/** File a dashboard under a group, or (null) back under its source's group. */
export function setDashboardDataGroup(
  reportId: string,
  groupId: string | null
): Promise<{ report_id: string; group_id: string | null; group: string | null }> {
  return api(`/dashboards/${enc(reportId)}/group`, {
    method: "PUT",
    json: { group_id: groupId },
  })
}

/** A readable message from an API error, whose text is usually `{"detail": ...}`. */
export function errorText(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback
  try {
    const parsed = JSON.parse(err.message) as { detail?: unknown }
    if (typeof parsed.detail === "string") return parsed.detail
  } catch {
    // Not JSON: the message is already plain text.
  }
  return err.message || fallback
}
