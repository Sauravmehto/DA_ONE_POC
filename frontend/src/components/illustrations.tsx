import type { SVGProps } from "react"

/**
 * Small line illustrations for empty states. Drawn in `currentColor` (the
 * wrapper sets it to the brand blue) over a soft brand tint, so they follow
 * light and dark mode and weigh a few hundred bytes instead of an image.
 * Strokes use pathLength=1 so the `animate-draw` utility can draw them in.
 */

const base: SVGProps<SVGSVGElement> = {
  viewBox: "0 0 160 120",
  width: 160,
  height: 120,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
}

const SOFT = "var(--brand-soft)"

/** No folders yet: a folder with papers rising out of it. */
export function NoFoldersIllustration(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <ellipse cx="80" cy="104" rx="46" ry="5" fill="currentColor" opacity="0.1" stroke="none" />
      <g className="origin-bottom -rotate-6">
        <rect x="52" y="30" width="46" height="52" rx="4" fill="var(--background)" stroke="none" />
        <rect x="52" y="30" width="46" height="52" rx="4" fill={SOFT} />
      </g>
      <rect x="62" y="24" width="46" height="52" rx="4" fill="var(--background)" />
      <path d="M70 38h28M70 47h28M70 56h18" pathLength="1" className="animate-draw [animation-delay:250ms]" />
      <path
        d="M30 52a6 6 0 0 1 6-6h24l8 9h56a6 6 0 0 1 6 6v36a6 6 0 0 1-6 6H36a6 6 0 0 1-6-6V52Z"
        fill="var(--background)"
        stroke="none"
      />
      <path
        d="M30 52a6 6 0 0 1 6-6h24l8 9h56a6 6 0 0 1 6 6v36a6 6 0 0 1-6 6H36a6 6 0 0 1-6-6V52Z"
        fill={SOFT}
      />
      <path d="M30 66h104" opacity="0.5" />
      <circle cx="124" cy="30" r="2.5" fill="currentColor" stroke="none" opacity="0.5" />
      <circle cx="38" cy="30" r="2" fill="currentColor" stroke="none" opacity="0.35" />
    </svg>
  )
}

/** No dashboards yet: a card with bars and a line that draws itself. */
export function NoDashboardsIllustration(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <ellipse cx="80" cy="106" rx="48" ry="5" fill="currentColor" opacity="0.1" stroke="none" />
      <rect x="30" y="14" width="100" height="82" rx="8" fill="var(--background)" />
      <path d="M30 32h100" opacity="0.5" />
      <circle cx="40" cy="23" r="2" fill="currentColor" stroke="none" opacity="0.5" />
      <circle cx="48" cy="23" r="2" fill="currentColor" stroke="none" opacity="0.35" />
      <rect x="42" y="64" width="12" height="20" rx="2" fill={SOFT} />
      <rect x="62" y="54" width="12" height="30" rx="2" fill={SOFT} />
      <rect x="82" y="60" width="12" height="24" rx="2" fill={SOFT} />
      <rect x="102" y="44" width="12" height="40" rx="2" fill={SOFT} />
      <path d="M42 56 62 46l20 8 32-18" pathLength="1" className="animate-draw [animation-delay:300ms]" stroke="var(--success)" />
      <circle cx="114" cy="36" r="3.5" fill="var(--success)" stroke="none" />
    </svg>
  )
}

/** Connect a source: nodes joining a central hub. */
export function ConnectIllustration(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <ellipse cx="80" cy="108" rx="40" ry="4" fill="currentColor" opacity="0.1" stroke="none" />
      <path d="M52 40 70 56M108 40 90 56M80 98V76M50 84l22-14M110 84 88 70" pathLength="1" className="animate-draw" opacity="0.7" />
      <circle cx="80" cy="64" r="16" fill="var(--background)" stroke="none" />
      <circle cx="80" cy="64" r="16" fill={SOFT} />
      <circle cx="80" cy="64" r="5" fill="currentColor" stroke="none" />
      <circle cx="46" cy="34" r="9" fill="var(--background)" />
      <circle cx="114" cy="34" r="9" fill="var(--background)" />
      <circle cx="80" cy="102" r="8" fill="var(--background)" />
      <circle cx="42" cy="88" r="7" fill="var(--background)" />
      <circle cx="118" cy="88" r="7" fill="var(--background)" />
    </svg>
  )
}
