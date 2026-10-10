import { SceneBackdrop } from "@/components/scene/SceneBackdrop";

export function WarpBand({ eyebrow, title, text, compact = false }: { eyebrow: string; title: string; text?: React.ReactNode; compact?: boolean }) {
  return (
    <section className={`warp-band relative isolate overflow-hidden ${compact ? "py-16 md:py-24" : "py-24 md:py-32"}`} data-scene="warp" data-warp data-testid="warp-band">
      <SceneBackdrop kind="warp" />
      <div className="warp-text container-x">
        <div className="mx-auto max-w-2xl text-center" data-warp-text>
          <p className="eyebrow justify-center">{eyebrow}</p>
          <h2 className={`${compact ? "text-3xl md:text-4xl font-bold" : "display-2"} mt-4`}>{title}</h2>
          {text ? <p className="mt-4 text-lg text-ink-2">{text}</p> : null}
        </div>
      </div>
    </section>
  );
}
