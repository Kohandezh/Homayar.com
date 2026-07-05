import Link from "next/link";
import type { ReactNode } from "react";

const BUTTON_VARIANTS = {
  primary:
    "bg-accent text-bg hover:bg-accent-strong font-semibold transition-[background-color,box-shadow,transform] duration-300 hover:cta-glow hover:-translate-y-0.5",
  secondary:
    "border border-line-strong text-ink hover:border-accent hover:text-accent hover:glow-soft transition-[color,border-color,box-shadow] duration-300",
  ghost: "text-ink-soft hover:text-ink transition-colors",
} as const;

const BUTTON_SIZES = {
  md: "h-10 px-4 text-sm rounded-lg",
  lg: "h-12 px-6 text-base rounded-xl",
} as const;

export function ButtonLink({
  href,
  variant = "primary",
  size = "md",
  className = "",
  children,
}: {
  href: string;
  variant?: keyof typeof BUTTON_VARIANTS;
  size?: keyof typeof BUTTON_SIZES;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`inline-flex items-center justify-center gap-2 ${BUTTON_SIZES[size]} ${BUTTON_VARIANTS[variant]} ${className}`}
    >
      {children}
    </Link>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-accent">
      {children}
    </p>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  center = false,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  center?: boolean;
}) {
  return (
    <div className={`max-w-2xl ${center ? "mx-auto text-center" : ""}`}>
      {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
      <h2 className="font-display text-fluid-h2 font-semibold text-ink">
        {title}
      </h2>
      {description && (
        <p className="mt-4 text-lg leading-relaxed text-ink-muted">
          {description}
        </p>
      )}
    </div>
  );
}

export function PageHero({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <section className="relative overflow-hidden border-b border-line">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(60% 80% at 70% 0%, rgba(34,211,238,0.10), transparent 60%), radial-gradient(50% 70% at 20% 10%, rgba(139,92,246,0.10), transparent 60%)",
        }}
      />
      <div className="container-site relative py-16 sm:py-24">
        <div className="max-w-3xl">
          {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
          <h1 className="font-display text-fluid-display font-semibold text-ink">
            {title}
          </h1>
          {description && (
            <p className="mt-5 max-w-2xl text-lg leading-relaxed text-ink-soft">
              {description}
            </p>
          )}
          {children && <div className="mt-8">{children}</div>}
        </div>
      </div>
    </section>
  );
}

export function Badge({
  children,
  tone = "accent",
}: {
  children: ReactNode;
  tone?: "accent" | "violet" | "amber" | "neutral";
}) {
  const tones = {
    accent: "border-accent/40 bg-accent/10 text-accent",
    violet: "border-violet/40 bg-violet/10 text-violet-soft",
    amber: "border-amber/40 bg-amber/10 text-amber",
    neutral: "border-line bg-raised text-ink-muted",
  } as const;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
