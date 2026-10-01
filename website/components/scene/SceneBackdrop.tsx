import type { SceneKind } from "./bus";

export function SceneBackdrop({ kind }: { kind: SceneKind; priority?: boolean }) {
  return (
    <svg className="scene-poster" data-poster={kind} aria-hidden="true" focusable="false" preserveAspectRatio="xMidYMid slice" viewBox="0 0 1920 1080">
      <use href={`#poster-${kind}`} />
    </svg>
  );
}
