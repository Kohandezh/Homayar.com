import type { ReactNode } from "react";

export function Section({
  id,
  children,
  className = "",
  tone = "base",
}: {
  id?: string;
  children: ReactNode;
  className?: string;
  tone?: "base" | "raised" | "glow";
}) {
  const bg = tone === "raised" ? "bg-bg-2 hairline-y" : tone === "glow" ? "relative isolate overflow-hidden" : "";
  return (
    <section id={id} className={`py-16 md:py-24 ${bg} ${className}`}>
      {tone === "glow" ? <div aria-hidden="true" className="glow-amber pointer-events-none absolute -top-40 start-1/2 -z-10 h-[520px] w-[720px] -translate-x-1/2 rtl:translate-x-1/2" /> : null}
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
  text?: string;
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

export function PageHero({
  eyebrow,
  title,
  text,
  children,
  aside,
}: {
  eyebrow?: string;
  title: string;
  text?: string;
  children?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="relative isolate overflow-hidden border-b border-border pb-12 pt-14 md:pb-16 md:pt-24">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute inset-0 grid-lines" />
        <div className="glow-amber absolute -top-64 -end-40 h-[560px] w-[560px]" data-parallax="8" />
      </div>
      <div className={`container-x ${aside ? "grid gap-10 lg:grid-cols-[1.1fr_1fr] lg:items-center" : ""}`}>
        <div>
          <SectionHeading eyebrow={eyebrow} title={title} text={text} as="h1" split />
          {children ? <div className="mt-8" data-reveal>{children}</div> : null}
        </div>
        {aside ? <div data-reveal="0.15">{aside}</div> : null}
      </div>
    </section>
  );
}
