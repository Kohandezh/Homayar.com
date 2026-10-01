import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { Icon } from "@/components/ui/Icon";

export function WorkStepper({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const a = dict.about;
  const st = a.stepper;
  const steps = a.howWeWork.steps;
  return (
    <section className="stepper relative" data-stepper data-testid="stepper">
      <div className="stepper-pin">
        <div className="container-x stepper-grid">
          <div>
            <h2 className="display-2">{a.howWeWork.title}</h2>
            <p className="chip mt-4 stepper-hint">
              <Icon name="scan" size={13} />
              {st.hint}
            </p>
            <ol className="stepper-rail mt-8" aria-hidden="true">
              {steps.map((s, i) => (
                <li key={s.title} className={`stepper-dot ${i === 0 ? "is-active" : ""}`} data-stepper-dot>
                  <span className="stepper-dot-mark">{localizeDigits(i + 1, locale)}</span>
                  <span className="text-sm font-medium">{s.title}</span>
                </li>
              ))}
            </ol>
          </div>
          <ol className="stepper-stage">
            {steps.map((s, i) => (
              <li key={s.title} className={`stepper-step panel ${i === 0 ? "is-active" : ""}`} data-stepper-step data-testid="stepper-step">
                <span className="label text-xs text-accent-text">
                  {st.step} {localizeDigits(i + 1, locale)} {st.of} {localizeDigits(steps.length, locale)}
                </span>
                <span className="stepper-num" aria-hidden="true">
                  {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
                </span>
                <h3 className="mt-2 text-2xl font-bold md:text-3xl">{s.title}</h3>
                <p className="mt-3 text-ink-2 md:text-lg">{bidi(s.text, locale)}</p>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
