import type { SceneKind } from "./bus";

const posters: Record<SceneKind, string> = {
  tunnel: "tunnel-wave",
  warp: "tunnel-warp",
  brain: "brain",
  field: "tunnel-wave",
};

export function SceneBackdrop({ kind, priority = false }: { kind: SceneKind; priority?: boolean }) {
  const name = posters[kind];
  return (
    <picture className="scene-poster" data-poster={kind}>
      <source type="image/avif" srcSet={`/media/${name}-960.avif 960w, /media/${name}-1920.avif 1920w`} sizes="100vw" />
      <source type="image/webp" srcSet={`/media/${name}-960.webp 960w, /media/${name}-1920.webp 1920w`} sizes="100vw" />
      <img src={`/media/${name}-1920.webp`} alt="" width={1920} height={1072} loading={priority ? "eager" : "lazy"} decoding="async" fetchPriority={priority ? "high" : "low"} />
    </picture>
  );
}
