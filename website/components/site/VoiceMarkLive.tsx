const HEIGHTS = [0.3, 0.55, 0.8, 1, 0.7, 1, 0.8, 0.55, 0.3];

export function VoiceMarkLive({ label }: { label: string }) {
  return (
    <div className="voice-live" data-voice-react role="img" aria-label={label} data-testid="voice-live">
      <svg viewBox="0 0 120 80" className="h-full w-full" aria-hidden="true" focusable="false">
        {HEIGHTS.map((h, i) => (
          <rect key={i} className="voice-live-bar" x={8 + i * 12.5} y={40 - (h * 60) / 2} width="6" height={h * 60} rx="3" fill="currentColor" style={{ ["--vh" as string]: h }} />
        ))}
      </svg>
    </div>
  );
}
