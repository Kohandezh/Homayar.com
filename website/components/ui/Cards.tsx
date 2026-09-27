import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function FeatureCard({
  icon,
  title,
  text,
  children,
}: {
  icon?: string;
  title: string;
  text?: string;
  children?: ReactNode;
}) {
  return (
    <div className="card flex h-full flex-col gap-3 p-5 md:p-6">
      {icon ? (
        <span className="inline-grid size-11 place-items-center rounded-xl bg-primary-soft text-primary">
          <Icon name={icon} size={22} />
        </span>
      ) : null}
      <h3 className="text-lg font-semibold">{title}</h3>
      {text ? <p className="text-ink-2">{text}</p> : null}
      {children}
    </div>
  );
}

export function CheckList({ items, tone = "light" }: { items: string[]; tone?: "light" | "dark" }) {
  const iconCls = tone === "dark" ? "bg-cream/15 text-cream" : "bg-primary-soft text-primary";
  return (
    <ul className="flex flex-col gap-3">
      {items.map((item) => (
        <li key={item} className="flex items-start gap-3">
          <span className={`mt-1 inline-grid size-5 shrink-0 place-items-center rounded-full ${iconCls}`}>
            <Icon name="check" size={13} strokeWidth={2.5} />
          </span>
          <span className={tone === "dark" ? "text-cream/90" : "text-ink-2"}>{item}</span>
        </li>
      ))}
    </ul>
  );
}

export function Chip({ children, tone = "sand" }: { children: ReactNode; tone?: "sand" | "amber" | "teal" }) {
  const cls =
    tone === "amber"
      ? "bg-accent text-ink"
      : tone === "teal"
        ? "bg-primary-soft text-primary"
        : "bg-sand text-ink-2";
  return <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${cls}`}>{children}</span>;
}

export function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div role="note" className="rounded-2xl border border-accent/50 bg-[#fff6e5] p-5 md:p-6">
      <p className="mb-2 flex items-center gap-2 font-semibold text-accent-text">
        <Icon name="alert" size={18} />
        {title}
      </p>
      <div className="text-ink-2">{children}</div>
    </div>
  );
}
