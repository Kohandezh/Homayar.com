import Link from "next/link";

/**
 * Homayar mark: an abstract Homa-bird wing rising over a waveform —
 * the companion that listens. Original artwork, no third-party assets.
 */
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden
    >
      <defs>
        <linearGradient id="homayar-mark" x1="4" y1="28" x2="28" y2="4">
          <stop offset="0%" stopColor="#06b6d4" />
          <stop offset="60%" stopColor="#22d3ee" />
          <stop offset="100%" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="#101a30" />
      <rect
        width="32"
        height="32"
        rx="8"
        stroke="url(#homayar-mark)"
        strokeOpacity="0.45"
      />
      {/* wing arc */}
      <path
        d="M6 21c4-9 14-13 20-12-3 1.5-4.5 3-5.5 5.5C17 20 11.5 22.5 6 21z"
        fill="url(#homayar-mark)"
      />
      {/* waveform under the wing */}
      <g stroke="url(#homayar-mark)" strokeWidth="1.8" strokeLinecap="round">
        <path d="M8 25v.01" />
        <path d="M12 23.5v3" />
        <path d="M16 22.5v5" />
        <path d="M20 23.5v3" />
        <path d="M24 25v.01" />
      </g>
    </svg>
  );
}

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <Link
      href="/"
      className="flex items-center gap-2.5 shrink-0"
      aria-label="Homayar — home"
    >
      <LogoMark size={size} />
      <span className="font-display text-lg font-semibold tracking-tight text-ink">
        Homayar
      </span>
    </Link>
  );
}
