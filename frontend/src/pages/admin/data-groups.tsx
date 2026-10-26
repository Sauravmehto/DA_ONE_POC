import * as React from "react"
import {
  FolderIcon,
  PlusIcon,
  StarIcon,
  Trash2Icon,
  UserIcon,
  UsersIcon,
  XIcon,
} from "lucide-react"
import { toast } from "sonner"

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
import { Skeleton } from "@/components/ui/skeleton"
import {
  addDataGroupMember,
  assignToDataGroup,
  createDataGroup,
  deleteDataGroup,
  errorText,
  fetchDataGroupCatalog,
  fetchDataGroups,
  removeDataGroupMember,
  removeFromDataGroup,
  RESOURCE_TYPE_NAMES,
  updateDataGroup,
  type DataGroup,
  type DataGroupCatalog,
  type DataGroupsState,
  type ResourceType,
} from "@/lib/data-groups-api"
import { cn } from "@/lib/utils"

const SELECT_CLASS = "h-9 rounded-md border bg-background px-2 text-sm"
const RESOURCE_TYPES: ResourceType[] = ["database", "folder", "dashboard", "report"]
const EMPTY_CATALOG: DataGroupCatalog = { database: [], folder: [], dashboard: [], report: [] }

/**
 * Data groups: business groups, their members, and what is filed under them.
 *
 * Every change is sent straight to the server and the page reloads the whole
 * state afterwards, so what is shown is always what is stored — the rules
 * decide who sees what, and a stale local copy would misstate them.
 */
export function DataGroupsPage() {
  const [state, setState] = React.useState<DataGroupsState | null>(null)
  const [catalog, setCatalog] = React.useState<DataGroupCatalog>(EMPTY_CATALOG)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [newName, setNewName] = React.useState("")
  const [busy, setBusy] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      const next = await fetchDataGroups()
      setState(next)
      setError(null)
      if (next.configured) {
        setCatalog(await fetchDataGroupCatalog())
      }
      return next
    } catch (err) {
      setError(errorText(err, "Could not load data groups"))
      return null
    }
  }, [])

  React.useEffect(() => {
    let cancelled = false
    void (async () => {
      const next = await load()
      if (!cancelled && next && next.groups.length > 0) {
        setSelectedId((current) => current ?? next.groups[0].group_id)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [load])

  /** Run one change, then show what the server now holds. */
  const change = React.useCallback(
    async (action: () => Promise<unknown>, done?: string) => {
      setBusy(true)
      try {
        await action()
        await load()
        if (done) toast.success(done)
        return true
      } catch (err) {
        toast.error(errorText(err, "The change was not saved"))
        return false
      } finally {
        setBusy(false)
      }
    },
    [load]
  )

  if (state === null && !error) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-3 p-6">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  const groups = state?.groups ?? []
  const selected = groups.find((g) => g.group_id === selectedId) ?? null

  return (
    <div className="scrollbar-slim h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl space-y-5 p-6">
        <div>
          <h1 className="text-xl font-medium">Data groups</h1>
          <p className="text-sm text-muted-foreground">
            Members of a group see the databases, folders, dashboards and reports filed
            under it. Anything in no group is visible to everyone.
          </p>
        </div>

        {error ? (
          <p className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </p>
        ) : null}

        {state && !state.configured ? (
          <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm text-amber-800 dark:text-amber-400">
            {state.setup_hint}
          </p>
        ) : null}

        {state?.configured ? (
          <>
            <p
              className={cn(
                "rounded-lg border p-3 text-sm",
                state.enforced
                  ? "border-emerald-500/40 bg-emerald-500/5 text-emerald-800 dark:text-emerald-400"
                  : "border-border bg-muted/40 text-muted-foreground"
              )}
            >
              {state.enforced
                ? "Access control is on: people see only what shares a group with them."
                : "Access control is off: dashboards are filed by group and nothing is hidden. Set DATA_GROUPS_ENFORCED=true to turn it on."}
            </p>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-[240px_minmax(0,1fr)]">
              <aside className="min-w-0 space-y-2">
                <ul className="divide-y overflow-hidden rounded-lg border">
                  {groups.map((group) => (
                    <li key={group.group_id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(group.group_id)}
                        className={cn(
                          "flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm hover:bg-muted/50",
                          selectedId === group.group_id && "bg-muted"
                        )}
                      >
                        <UsersIcon className="size-4 shrink-0 text-indigo-500" />
                        <span className="min-w-0 flex-1 truncate">{group.name}</span>
                        <span className="text-xs text-muted-foreground tabular-nums">
                          {group.members.length}
                        </span>
                      </button>
                    </li>
                  ))}
                  {groups.length === 0 ? (
                    <li className="px-3 py-3 text-sm text-muted-foreground">No groups yet.</li>
                  ) : null}
                </ul>
                <form
                  className="flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    const name = newName.trim()
                    if (!name) return
                    void (async () => {
                      setBusy(true)
                      try {
                        const created = await createDataGroup(name)
                        setNewName("")
                        await load()
                        setSelectedId(created.group_id)
                        toast.success(`Created ${name}`)
                      } catch (err) {
                        toast.error(errorText(err, "The group was not created"))
                      } finally {
                        setBusy(false)
                      }
                    })()
                  }}
                >
                  <Input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="New group"
                    aria-label="New group name"
                    className="h-9"
                  />
                  <Button type="submit" size="icon" disabled={busy || !newName.trim()} aria-label="Create group">
                    <PlusIcon className="size-4" />
                  </Button>
                </form>
              </aside>

              {selected ? (
                <GroupDetail
                  key={selected.group_id}
                  group={selected}
                  groups={groups}
                  catalog={catalog}
                  busy={busy}
                  change={change}
                  onDeleted={() => setSelectedId(null)}
                />
              ) : (
                <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                  Select a group, or create one.
                </p>
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}

function GroupDetail({
  group,
  groups,
  catalog,
  busy,
  change,
  onDeleted,
}: {
  group: DataGroup
  groups: DataGroup[]
  catalog: DataGroupCatalog
  busy: boolean
  change: (action: () => Promise<unknown>, done?: string) => Promise<boolean>
  onDeleted: () => void
}) {
  const [name, setName] = React.useState(group.name)
  const [description, setDescription] = React.useState(group.description)
  const [memberName, setMemberName] = React.useState("")
  const [resourceType, setResourceType] = React.useState<ResourceType>("database")
  const [resourceId, setResourceId] = React.useState("")
  const [confirmDelete, setConfirmDelete] = React.useState(false)

  const labelOf = (type: ResourceType, id: string) =>
    catalog[type].find((item) => item.id === id)?.label ?? id

  /** The group a resource is listed under, when that is not this one. */
  const mainElsewhere = (type: ResourceType, id: string) =>
    groups.find(
      (g) =>
        g.group_id !== group.group_id &&
        g.resources.some((r) => r.resource_type === type && r.resource_id === id && r.is_main)
    )?.name

  const assigned = new Set(
    group.resources.filter((r) => r.resource_type === resourceType).map((r) => r.resource_id)
  )
  const choices = catalog[resourceType].filter((item) => !assigned.has(item.id))
  const detailsChanged = name.trim() !== group.name || description.trim() !== group.description

  return (
    <section className="min-w-0 space-y-5 rounded-lg border p-4">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Group name"
            className="h-9 font-medium"
          />
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Delete ${group.name}`}
            title="Delete group"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2Icon className="size-4" />
          </Button>
        </div>
        <Input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description"
          aria-label="Group description"
          className="h-9"
        />
        {detailsChanged ? (
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setName(group.name)
                setDescription(group.description)
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={busy || !name.trim()}
              onClick={() =>
                void change(
                  () => updateDataGroup(group.group_id, { name: name.trim(), description: description.trim() }),
                  "Group saved"
                )
              }
            >
              Save
            </Button>
          </div>
        ) : null}
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-medium">
          Members
          <span className="ml-2 text-muted-foreground">{group.members.length}</span>
        </h2>
        {group.members.length === 0 ? (
          <p className="text-sm text-muted-foreground">No members yet.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {group.members.map((m) => (
              <span
                key={`${m.member_type}:${m.member_name}`}
                className="inline-flex items-center gap-1.5 rounded-full border bg-muted/40 py-1 pr-1 pl-2.5 text-xs"
              >
                <UserIcon className="size-3.5 text-muted-foreground" />
                <span className="font-mono">{m.member_name}</span>
                <button
                  type="button"
                  aria-label={`Remove ${m.member_name}`}
                  disabled={busy}
                  onClick={() =>
                    void change(() => removeDataGroupMember(group.group_id, m.member_type, m.member_name))
                  }
                  className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <XIcon className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            const value = memberName.trim()
            if (!value) return
            void (async () => {
              if (await change(() => addDataGroupMember(group.group_id, "USER", value))) {
                setMemberName("")
              }
            })()
          }}
        >
          <Input
            value={memberName}
            onChange={(e) => setMemberName(e.target.value)}
            placeholder="User name"
            aria-label="User name"
            className="h-9 w-56 font-mono"
          />
          <Button type="submit" variant="outline" disabled={busy || !memberName.trim()}>
            Add member
          </Button>
        </form>
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-medium">
          Filed under this group
          <span className="ml-2 text-muted-foreground">{group.resources.length}</span>
        </h2>
        <p className="text-xs text-muted-foreground">
          Dashboards and reports follow the group of their database or folder unless filed here
          themselves. The main group is where a resource is listed.
        </p>
        {group.resources.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing filed yet.</p>
        ) : (
          <ul className="divide-y overflow-hidden rounded-lg border">
            {RESOURCE_TYPES.flatMap((type) =>
              group.resources
                .filter((r) => r.resource_type === type)
                .map((r) => {
                  const elsewhere = r.is_main ? undefined : mainElsewhere(type, r.resource_id)
                  return (
                    <li key={`${type}:${r.resource_id}`} className="flex items-center gap-2 px-3 py-2 text-sm">
                      <span className="w-20 shrink-0 text-xs text-muted-foreground">
                        {RESOURCE_TYPE_NAMES[type]}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{labelOf(type, r.resource_id)}</span>
                      {r.is_main ? (
                        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-700 dark:text-indigo-300">
                          <StarIcon className="size-3" />
                          Main group
                        </span>
                      ) : (
                        <>
                          {elsewhere ? (
                            <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
                              Listed under {elsewhere}
                            </span>
                          ) : null}
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busy}
                            onClick={() =>
                              void change(() => assignToDataGroup(group.group_id, type, r.resource_id, true))
                            }
                          >
                            Make main
                          </Button>
                        </>
                      )}
                      <button
                        type="button"
                        aria-label={`Remove ${labelOf(type, r.resource_id)} from ${group.name}`}
                        disabled={busy}
                        onClick={() =>
                          void change(() => removeFromDataGroup(group.group_id, type, r.resource_id))
                        }
                        className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                      >
                        <XIcon className="size-4" />
                      </button>
                    </li>
                  )
                })
            )}
          </ul>
        )}
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (!resourceId) return
            void (async () => {
              if (await change(() => assignToDataGroup(group.group_id, resourceType, resourceId))) {
                setResourceId("")
              }
            })()
          }}
        >
          <select
            value={resourceType}
            onChange={(e) => {
              setResourceType(e.target.value as ResourceType)
              setResourceId("")
            }}
            aria-label="Resource type"
            className={SELECT_CLASS}
          >
            {RESOURCE_TYPES.map((type) => (
              <option key={type} value={type}>
                {RESOURCE_TYPE_NAMES[type]}
              </option>
            ))}
          </select>
          <select
            value={resourceId}
            onChange={(e) => setResourceId(e.target.value)}
            aria-label={`${RESOURCE_TYPE_NAMES[resourceType]} to add`}
            className={cn(SELECT_CLASS, "min-w-56 flex-1")}
          >
            <option value="">
              {choices.length ? `Choose a ${RESOURCE_TYPE_NAMES[resourceType].toLowerCase()}` : "Nothing left to add"}
            </option>
            {choices.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
          <Button type="submit" variant="outline" disabled={busy || !resourceId}>
            <FolderIcon className="size-4" />
            Add
          </Button>
        </form>
      </div>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete {group.name}?</DialogTitle>
            <DialogDescription>
              Its members lose access through it, and what is filed under it moves to its source&apos;s
              group or becomes ungrouped.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() =>
                void (async () => {
                  if (await change(() => deleteDataGroup(group.group_id), `Deleted ${group.name}`)) {
                    setConfirmDelete(false)
                    onDeleted()
                  }
                })()
              }
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

export default DataGroupsPage
