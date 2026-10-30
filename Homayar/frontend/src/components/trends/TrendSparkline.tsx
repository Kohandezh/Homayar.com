import type { TrendCell } from "@/types/trends";

const WIDTH = 96;
const HEIGHT = 28;
const PAD = 3;

/** The computed periods of a row, by period index; every other cell is left out of the line. */
function computedPeriods(cells: Array<TrendCell | undefined>) {
  return cells.flatMap((cell, index) =>
    cell && cell.status === "computed" && cell.ratio !== null ? [{ index, ratio: cell.ratio }] : [],
  );
}

/**
 * A row's line over its periods, drawn only from computed cells and only
 * when there are at least two of them: a small sample is a warning in its
 * cell, never a trend. The caller renders nothing for fewer points.
 */
export default function TrendSparkline({
  cells,
  label,
}: {
  cells: Array<TrendCell | undefined>;
  label: string;
}) {
  const points = computedPeriods(cells);
  if (points.length < 2) return null;
  const span = Math.max(1, cells.length - 1);
  const coords = points.map(({ index, ratio }) => ({
    x: PAD + (index / span) * (WIDTH - 2 * PAD),
    y: PAD + (1 - Math.min(1, Math.max(0, ratio))) * (HEIGHT - 2 * PAD),
  }));

  return (
    // Periods run in reading order, so the line is mirrored in Persian like the columns.
    <svg
      role="img"
      aria-label={label}
      width={WIDTH}
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="rtl-mirror text-accent"
    >
      <polyline
        points={coords.map(({ x, y }) => `${x},${y}`).join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      {coords.map(({ x, y }) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r={2} fill="currentColor" />
      ))}
    </svg>
  );
}
