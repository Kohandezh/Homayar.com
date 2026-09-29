import type { ReactNode } from "react";
import { SceneBackdrop } from "@/components/scene/SceneBackdrop";
import type { SceneKind } from "@/components/scene/bus";

export function Section({
  id,
  children,
  className = "",
  tone = "base",
  scene,
}: {
  id?: string;
  children: ReactNode;
  className?: string;
  tone?: "base" | "raised" | "glow";
  scene?: SceneKind;
}) {
  const bg = tone === "raised" ? "bg-bg-deep hairline-y" : tone === "glow" || scene ? "relative isolate overflow-hidden" : "";
  return (
    <section id={id} className={`py-16 md:py-24 ${bg} ${className}`} data-scene={scene}>
      {scene ? <SceneBackdrop kind={scene} /> : null}
      {tone === "glow" ? <div aria-hidden="true" className="glow-accent pointer-events-none absolute -top-40 start-1/2 -z-10 h-[520px] w-[720px] -translate-x-1/2 rtl:translate-x-1/2" /> : null}
      <div className="container-x">{children}</div>
    </section>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  text,
  align = "start",
  as: Tag = "h2",
  split = false,
}: {
  eyebrow?: string;
  title: string;
  text?: ReactNode;
  align?: "start" | "center";
  as?: "h1" | "h2";
  split?: boolean;
}) {
  const alignCls = align === "center" ? "mx-auto text-center items-center" : "";
  return (
    <div className={`flex max-w-3xl flex-col gap-4 ${alignCls}`}>
      {eyebrow ? <p className="eyebrow" data-reveal>{eyebrow}</p> : null}
      <Tag className={Tag === "h1" ? "display" : "display-2"} data-split={split ? "" : undefined} data-reveal={split ? undefined : ""}>
        {title}
      </Tag>
      {text ? (
        <p className="text-lg text-ink-2 md:text-xl" data-reveal>
          {text}
        </p>
      ) : null}
    </div>
  );
}

/** Inner-page hero: a short flight through the grid (or into the warp) behind the page title. */
export function PageHero({
  eyebrow,
  title,
  text,
  children,
  aside,
  scene = "tunnel",
  back,
}: {
  eyebrow?: string;
  title: string;
  text?: ReactNode;
  children?: ReactNode;
  aside?: ReactNode;
  scene?: SceneKind;
  back?: ReactNode;
}) {
  return (
    <section className="relative isolate overflow-hidden border-b border-border" data-scene={scene} data-hero-scene>
      <SceneBackdrop kind={scene} priority />
      <div className="scene-fade-bottom" aria-hidden="true" />
      <div className={`container-x flex min-h-[68svh] flex-col justify-center pb-14 pt-14 md:pb-20 md:pt-20 ${aside ? "lg:grid lg:grid-cols-[1.1fr_1fr] lg:items-center lg:gap-10" : ""}`} data-hero-content>
        <div>
          {back}
          <SectionHeading eyebrow={eyebrow} title={title} text={text} as="h1" split />
          {children ? <div className="mt-8" data-reveal>{children}</div> : null}
        </div>
        {aside ? <div className="mt-10 lg:mt-0" data-depth="260">{aside}</div> : null}
      </div>
    </section>
  );
}
