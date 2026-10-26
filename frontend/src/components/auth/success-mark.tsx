/**
 * A ring that draws and a tick that lands: the "it worked" moment for sent
 * emails. Reuses the publish animation's keyframes (index.css), so the whole
 * product celebrates the same way.
 */
export function SuccessMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 48 48"
      className={className ?? "size-14 text-success"}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="24" cy="24" r="20" pathLength="1" className="animate-publish-ring" opacity="0.9" />
      <path
        d="m15 25 6.5 6.5L33 19"
        className="animate-publish-tick origin-center"
        style={{ transformBox: "fill-box" }}
      />
    </svg>
  )
}
