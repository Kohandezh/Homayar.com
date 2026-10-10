import Link from "next/link";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";

// Pricing: the camera rides a lift straight up. One plan waits on each floor (design-system/SCENE-RULES.md).
// With WebGL the engine hangs each plan at its floor. Without WebGL the plans simply stack.
export function PlanLift({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const p = dict.pricing;
  return (
    <section className="floors relative" data-floors data-testid="floors" style={{ ["--n" as string]: p.plans.length }}>
      <div className="html3d-layer" data-html-layer>
        <div className="html3d-camera floors-list" data-html-camera>
          {p.plans.map((plan, i) => (
            <article key={plan.name} className="plan-panel" data-plan data-testid="plan">
              <span className="outline-num" aria-hidden="true">
                {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
              </span>
              <h2 className={`mt-4 text-3xl font-bold ${i === 2 ? "text-accent-text" : ""}`}>{plan.name}</h2>
              <p className="mt-2 text-ink-2">{plan.text}</p>
              <p className="mt-8 border-t border-border-2 pt-4 text-lg font-semibold">{plan.amount}</p>
              <Link href={`/${locale}/contact`} className={`btn mt-8 ${i === 2 ? "btn-primary" : "btn-ghost"}`}>
                {p.cta}
              </Link>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
