import type { ReactNode } from "react";

export function Section({
  id,
  children,
  className = "",
  tone = "default",
}: {
  id?: string;
  children: ReactNode;
  className?: string;
  tone?: "default" | "sand" | "dark";
}) {
  const bg =
    tone === "sand" ? "bg-sand" : tone === "dark" ? "bg-ink-teal text-cream" : "";
  return (
    <section id={id} className={`py-14 md:py-20 ${bg} ${className}`}>
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
}: {
  eyebrow?: string;
  title: string;
  text?: string;
  align?: "start" | "center";
  as?: "h1" | "h2";
}) {
  const alignCls = align === "center" ? "mx-auto text-center items-center" : "";
  return (
    <div className={`flex max-w-3xl flex-col gap-3 ${alignCls}`}>
      {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
      <Tag className={Tag === "h1" ? "text-3xl font-bold md:text-5xl" : "text-2xl font-bold md:text-4xl"}>{title}</Tag>
      {text ? <p className="text-lg text-ink-2">{text}</p> : null}
    </div>
  );
}

export function PageHero({
  eyebrow,
  title,
  text,
  children,
}: {
  eyebrow?: string;
  title: string;
  text?: string;
  children?: ReactNode;
}) {
  return (
    <section className="border-b border-border bg-surface/60 py-12 md:py-16">
      <div className="container-x">
        <SectionHeading eyebrow={eyebrow} title={title} text={text} as="h1" />
        {children ? <div className="mt-8">{children}</div> : null}
      </div>
    </section>
  );
}
