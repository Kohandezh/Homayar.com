import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function base(props: IconProps): IconProps {
  return {
    width: 24,
    height: 24,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...props,
  };
}

export const MenuIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </svg>
);

export const CloseIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);

export const CartIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M3 4h2l2.4 12.2a1.5 1.5 0 0 0 1.47 1.2h7.96a1.5 1.5 0 0 0 1.47-1.18L20.5 8H6" />
    <circle cx="9.5" cy="20.5" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="16.5" cy="20.5" r="1.2" fill="currentColor" stroke="none" />
  </svg>
);

export const ArrowRightIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M4 12h16m0 0-6-6m6 6-6 6" />
  </svg>
);

export const CheckIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M4.5 12.5l5 5 10-11" />
  </svg>
);

export const ChevronDownIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M6 9l6 6 6-6" />
  </svg>
);

export const MinusIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M5 12h14" />
  </svg>
);

export const PlusIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export const TrashIcon = (p: IconProps) => (
  <svg {...base(p)}>
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13a1 1 0 0 0 1 .9h8a1 1 0 0 0 1-.9l1-13M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
  </svg>
);

/* ── Category icon set (keys match categories.json "icon" values) ── */

const CATEGORY_ICONS: Record<string, (p: IconProps) => React.ReactElement> = {
  waveform: (p) => (
    <svg {...base(p)}>
      <path d="M3 12h2m2-4v8m3-12v16m3-11v6m3-9v12m3-8v4m3-2h-1" />
    </svg>
  ),
  gauge: (p) => (
    <svg {...base(p)}>
      <path d="M5 19a9 9 0 1 1 14 0" />
      <path d="M12 13l4-4" />
      <circle cx="12" cy="14" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  ),
  heart: (p) => (
    <svg {...base(p)}>
      <path d="M12 20s-7-4.5-9-9a5 5 0 0 1 9-3 5 5 0 0 1 9 3c-2 4.5-9 9-9 9z" />
    </svg>
  ),
  trend: (p) => (
    <svg {...base(p)}>
      <path d="M3 17l6-6 4 4 8-8" />
      <path d="M15 7h6v6" />
    </svg>
  ),
  eye: (p) => (
    <svg {...base(p)}>
      <path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z" />
      <circle cx="12" cy="12" r="2.5" />
    </svg>
  ),
  server: (p) => (
    <svg {...base(p)}>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </svg>
  ),
  shield: (p) => (
    <svg {...base(p)}>
      <path d="M12 3l8 3v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-3z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
  chart: (p) => (
    <svg {...base(p)}>
      <path d="M4 20V4" />
      <path d="M4 20h16" />
      <rect x="7" y="12" width="3" height="5" rx="0.5" />
      <rect x="12" y="8" width="3" height="9" rx="0.5" />
      <rect x="17" y="5" width="3" height="12" rx="0.5" />
    </svg>
  ),
  graduation: (p) => (
    <svg {...base(p)}>
      <path d="M12 4L2 9l10 5 10-5-10-5z" />
      <path d="M6 11.5V16c0 1.5 2.7 3 6 3s6-1.5 6-3v-4.5" />
    </svg>
  ),
  plug: (p) => (
    <svg {...base(p)}>
      <path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-5 5 5 5 0 0 1-5-5V8z" />
      <path d="M12 16v5" />
    </svg>
  ),
  layers: (p) => (
    <svg {...base(p)}>
      <path d="M12 3l9 5-9 5-9-5 9-5z" />
      <path d="M3 13l9 5 9-5" />
    </svg>
  ),
};

export function CategoryIcon({
  icon,
  ...props
}: IconProps & { icon: string }) {
  const Render = CATEGORY_ICONS[icon] ?? CATEGORY_ICONS.layers;
  return <Render {...props} />;
}
