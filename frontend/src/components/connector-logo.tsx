import snowflakeSvg from "simple-icons/icons/snowflake.svg?raw"

import { FolderGlyph } from "@/components/file-type-icon"
import { cn } from "@/lib/utils"

/** The single path simple-icons draws each logo with. */
const SNOWFLAKE_PATH = /\sd="([^"]+)"/.exec(snowflakeSvg)?.[1] ?? ""

export type ConnectorKind = "snowflake" | "sqlserver" | "files"

/**
 * A connector's logo.
 *
 * Snowflake's is the official mark from simple-icons, in its brand colour.
 * simple-icons does not carry Microsoft SQL Server's logo — Microsoft had its
 * marks removed — so SQL Server is a plain database drawing in SQL Server red
 * until an official file is added.
 */
export function ConnectorLogo({
  kind,
  className,
}: {
  kind: ConnectorKind
  className?: string
}) {
  if (kind === "snowflake") {
    return (
      <svg
        viewBox="0 0 24 24"
        role="img"
        aria-label="Snowflake"
        className={cn("size-8 shrink-0", className)}
        fill="#29B5E8"
      >
        <path d={SNOWFLAKE_PATH} />
      </svg>
    )
  }
  if (kind === "sqlserver") {
    return (
      <svg
        viewBox="0 0 24 24"
        role="img"
        aria-label="SQL Server"
        className={cn("size-8 shrink-0", className)}
        fill="none"
        stroke="#CC2927"
        strokeWidth={1.8}
        strokeLinecap="round"
      >
        <ellipse cx="12" cy="5.5" rx="7.5" ry="2.8" />
        <path d="M4.5 5.5v13c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8v-13" />
        <path d="M4.5 12c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8" />
      </svg>
    )
  }
  return <FolderGlyph className={cn("size-8", className)} />
}
