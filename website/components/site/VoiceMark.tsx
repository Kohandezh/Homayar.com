export function VoiceMark({ size = 20, className = "" }: { size?: number; className?: string }) {
  const bars = [
    { x: 3, h: 6 },
    { x: 7.5, h: 12 },
    { x: 12, h: 18 },
    { x: 16.5, h: 12 },
    { x: 21, h: 7 },
  ];
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} className={className} aria-hidden="true" focusable="false">
      {bars.map((b, i) => (
        <rect key={i} className="mark-bar" x={b.x - 1.1} y={12 - b.h / 2} width="2.2" height={b.h} rx="1.1" fill="currentColor" />
      ))}
    </svg>
  );
}
