import * as React from "react"
import { Link, useSearchParams } from "react-router-dom"
import { CircleIcon, PencilIcon } from "lucide-react"

import { Skeleton } from "@/components/ui/skeleton"
import { fetchLayers, type LayerSummary } from "@/lib/semantic-api"
import { cn } from "@/lib/utils"

/**
 * Every database that can have a layer, from every connector, documented or
 * not.
 *
 * The undocumented ones are the point: they are invisible in chat today,
 * because the agent endpoint rejects any database without a layer. This list
 * is where an admin finds them.
 */

/** What to call the connector a source comes from. Uploaded files are not
 *  here: a spreadsheet has no schema to document beyond its own columns, so
 *  the Builder does not offer them. */
const SOURCE_LABELS: Record<string, string> = {
  snowflake: "Snowflake",
  sqlserver: "SQL Server",
}
export function SemanticLayersPage() {
  const [params] = useSearchParams()
  const connector = params.get("connector") ?? undefined
  const connection = params.get("connection") ?? undefined

  const [layers, setLayers] = React.useState<LayerSummary[]>([])
  const [isLoading, setIsLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    let cancelled = false
    setIsLoading(true)
    void (async () => {
      try {
        const list = await fetchLayers({ connector, connection })
        if (!cancelled) setLayers(list)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load")
        }
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [connector, connection])

  const published = layers.filter((l) => l.is_published)
  const undocumented = layers.filter((l) => !l.is_published)

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-3 p-6">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    )
  }

  return (
    <div className="scrollbar-slim h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl space-y-6 p-6">
        <div>
          <h1 className="text-xl font-medium">Semantic layers</h1>
          <p className="text-sm text-muted-foreground">
            What each database <em>means</em>: column definitions, join rules
            and date roles. A database becomes available in chat once its layer
            is published.
          </p>
          {connector ? (
            // No way out to "every source": a layer belongs inside its own
            // database's boundary, and the databases of one connection have
            // nothing to say about another's.
            <p className="mt-1 text-sm text-muted-foreground">
              {SOURCE_LABELS[connector] ?? connector} databases
            </p>
          ) : null}
        </div>

        {error ? (
          <p className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <Section
          title="Published"
          empty="No layers published yet."
          layers={published}
        />
        <Section
          title="Not documented"
          description="Not selectable in chat until a layer is published."
          empty="Every visible database has a layer."
          layers={undocumented}
        />
      </div>
    </div>
  )
}

function Section({
  title,
  description,
  empty,
  layers,
}: {
  title: string
  description?: string
  empty: string
  layers: LayerSummary[]
}) {
  return (
    <section className="space-y-2">
      <div>
        <h2 className="text-sm font-medium">
          {title}
          <span className="ml-2 text-muted-foreground">{layers.length}</span>
        </h2>
        {description ? (
          <p className="text-xs text-muted-foreground">{description}</p>
        ) : null}
      </div>

      {layers.length === 0 ? (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          {empty}
        </p>
      ) : (
        <ul className="divide-y overflow-hidden rounded-lg border">
          {layers.map((layer) => (
            <li key={layer.database}>
              <Link
                to={`/tools/semantic/${encodeURIComponent(layer.database)}`}
                className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50"
              >
                <CircleIcon
                  className={cn(
                    "size-2.5 shrink-0",
                    layer.is_published
                      ? "fill-emerald-500 text-emerald-500"
                      : "fill-muted-foreground/30 text-muted-foreground/30"
                  )}
                  aria-hidden
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {layer.display_name || layer.database}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {SOURCE_LABELS[layer.connector] ?? layer.connector}
                    {layer.is_published
                      ? ` · ${layer.documented_tables} tables documented`
                      : ""}
                    {layer.can_edit ? "" : " · read-only"}
                  </p>
                </div>

                {layer.has_draft ? (
                  <span className="flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                    <PencilIcon className="size-3" />
                    Draft
                  </span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default SemanticLayersPage
