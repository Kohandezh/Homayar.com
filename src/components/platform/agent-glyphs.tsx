import type { SVGProps } from "react";

type GlyphProps = SVGProps<SVGSVGElement>;

function base(props: GlyphProps): GlyphProps {
  return {
    width: 26,
    height: 26,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...props,
  };
}

/** Listener — incoming signal arcs meeting a receiver dot */
const ListenerGlyph = (p: GlyphProps) => (
  <svg {...base(p)}>
    <circle cx="8" cy="12" r="2" fill="currentColor" stroke="none" />
    <path d="M13 8a5.5 5.5 0 0 1 0 8" />
    <path d="M16.5 5.5a10 10 0 0 1 0 13" />
    <path d="M3 12h2.5" />
  </svg>
);

/** Scribe — timestamped transcript lines on a page */
const ScribeGlyph = (p: GlyphProps) => (
  <svg {...base(p)}>
    <rect x="4" y="3" width="16" height="18" rx="2" />
    <path d="M8 8h5M8 12h8M8 16h6" />
    <circle cx="16.5" cy="8" r="0.9" fill="currentColor" stroke="none" />
  </svg>
);

/** Judge — balance scales */
const JudgeGlyph = (p: GlyphProps) => (
  <svg {...base(p)}>
    <path d="M12 4v16M8 20h8" />
    <path d="M5 7h14" />
    <path d="M5 7l-2.5 5a3 3 0 0 0 5 0L5 7zM19 7l-2.5 5a3 3 0 0 0 5 0L19 7z" />
  </svg>
);

/** Guardian — shield with alert mark */
const GuardianGlyph = (p: GlyphProps) => (
  <svg {...base(p)}>
    <path d="M12 3l8 3v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-3z" />
    <path d="M12 8v5" />
    <circle cx="12" cy="16" r="0.9" fill="currentColor" stroke="none" />
  </svg>
);

/** Scout — radar sweep with a detected blip */
const ScoutGlyph = (p: GlyphProps) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 12V4.5" />
    <path d="M12 12l5.5 3" />
    <path d="M12 8a4 4 0 0 1 4 4" opacity="0.55" />
    <circle cx="7.5" cy="14.5" r="1.1" fill="currentColor" stroke="none" />
  </svg>
);

/** Coach — ascending steps with a rising arrow */
const CoachGlyph = (p: GlyphProps) => (
  <svg {...base(p)}>
    <path d="M3 20h5v-4h5v-4h5V8" />
    <path d="M13 5h6v6" />
    <path d="M19 5l-7 7" />
  </svg>
);

/** Analyst — bar chart with trend line */
const AnalystGlyph = (p: GlyphProps) => (
  <svg {...base(p)}>
    <path d="M4 20h16" />
    <path d="M6 20v-5M11 20v-8M16 20v-11" />
    <path d="M5 11l5-4 4 2 5-5" opacity="0.7" />
    <circle cx="19" cy="4" r="1" fill="currentColor" stroke="none" />
  </svg>
);

const GLYPHS: Record<string, (p: GlyphProps) => React.ReactElement> = {
  Listener: ListenerGlyph,
  Scribe: ScribeGlyph,
  Judge: JudgeGlyph,
  Guardian: GuardianGlyph,
  Scout: ScoutGlyph,
  Coach: CoachGlyph,
  Analyst: AnalystGlyph,
};

export function AgentGlyph({
  name,
  ...props
}: GlyphProps & { name: string }) {
  const Render = GLYPHS[name] ?? ListenerGlyph;
  return <Render {...props} />;
}
