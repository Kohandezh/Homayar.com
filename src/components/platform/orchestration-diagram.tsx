const TONE_TEXT = [
  "text-accent",
  "text-violet-soft",
  "text-amber",
  "text-teal",
] as const;

const NODES = [
  { name: "Listener", x: 240, y: 60 },
  { name: "Scribe", x: 357, y: 117 },
  { name: "Judge", x: 386, y: 243 },
  { name: "Guardian", x: 305, y: 345 },
  { name: "Scout", x: 175, y: 345 },
  { name: "Coach", x: 94, y: 243 },
  { name: "Analyst", x: 123, y: 117 },
] as const;

export function OrchestrationDiagram() {
  return (
    <div className="card-surface rounded-2xl p-4 sm:p-8">
      <svg
        viewBox="0 0 480 420"
        role="img"
        aria-label="Orchestration diagram: seven Homayar agents — Listener, Scribe, Judge, Guardian, Scout, Coach, and Analyst — connected around a single live call at the center."
        className="mx-auto h-auto w-full max-w-xl"
      >
        {/* connector lines */}
        {NODES.map((node) => (
          <line
            key={`line-${node.name}`}
            x1={240}
            y1={210}
            x2={node.x}
            y2={node.y}
            strokeDasharray="3 5"
            className="stroke-line-strong"
            strokeWidth={1.2}
          />
        ))}

        {/* central call node */}
        <circle
          cx={240}
          cy={210}
          r={64}
          fill="none"
          strokeDasharray="2 6"
          className="stroke-line-strong"
          strokeWidth={1}
        />
        <circle
          cx={240}
          cy={210}
          r={52}
          className="fill-raised stroke-accent/50"
          strokeWidth={1.4}
        />
        <g
          className="stroke-accent"
          strokeWidth={2.4}
          strokeLinecap="round"
          fill="none"
        >
          <path d="M222 199v10" />
          <path d="M228 195v18" />
          <path d="M234 191v26" />
          <path d="M240 187v34" />
          <path d="M246 192v24" />
          <path d="M252 196v16" />
          <path d="M258 200v8" />
        </g>
        <text
          x={240}
          y={244}
          textAnchor="middle"
          fontSize={10}
          letterSpacing={2}
          className="fill-ink-muted font-mono"
        >
          ONE CALL
        </text>

        {/* agent nodes */}
        {NODES.map((node, i) => (
          <g key={node.name} className={TONE_TEXT[i % TONE_TEXT.length]}>
            <rect
              x={node.x - 47}
              y={node.y - 16}
              width={94}
              height={32}
              rx={16}
              fill="currentColor"
              fillOpacity={0.1}
              stroke="currentColor"
              strokeOpacity={0.55}
              strokeWidth={1.2}
            />
            <circle cx={node.x - 32} cy={node.y} r={3} fill="currentColor" />
            <text
              x={node.x + 5}
              y={node.y}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={12.5}
              fontWeight={600}
              className="fill-ink"
            >
              {node.name}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
