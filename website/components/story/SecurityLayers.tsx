import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { Icon } from "@/components/ui/Icon";

export function SecurityLayers({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const l = dict.security.layers;
  return (
    <section className="layers relative" data-layers data-testid="layers">
      <div className="layers-pin">
        <div className="container-x layers-head">
          <p className="eyebrow">{l.eyebrow}</p>
          <h2 className="display-2 mt-3 max-w-3xl">{l.title}</h2>
          <p className="chip mt-4 layers-hint">
            <Icon name="scan" size={13} />
            {l.hint}
          </p>
        </div>
        <ol className="layers-stage container-x" aria-label={l.eyebrow}>
          {l.items.map((item, i) => (
            <li key={item.title} className="layer panel-glass" data-layer style={{ ["--k" as string]: i }} data-testid="layer">
              <div className="flex items-center justify-between">
                <span className="label text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                <span className="inline-grid size-11 place-items-center rounded-full border border-accent/40 bg-accent/10 text-accent-text">
                  <Icon name={item.icon} size={20} />
                </span>
              </div>
              <h3 className="mt-5 text-2xl font-bold md:text-3xl">{item.title}</h3>
              <p className="mt-3 text-ink-2 md:text-lg">{bidi(item.text, locale)}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function splitAround(text: string, re: RegExp): [string, string, string] {
  const m = text.match(re);
  if (!m || m.index === undefined) return [text, "", ""];
  return [text.slice(0, m.index), m[0], text.slice(m.index + m[0].length)];
}

export function MaskScrub({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const md = dict.security.maskDemo;
  const [pre, number, post] = splitAround(md.before, /[0-9۰-۹][0-9۰-۹ ]*[0-9۰-۹]/);
  const [, placeholder] = splitAround(md.after, /\[[A-Z_0-9]+\]/);
  const chars = Array.from(number);
  return (
    <div className="mask panel panel-glow p-5 md:p-7" data-mask data-testid="mask">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="eyebrow !text-[11px]">{md.eyebrow}</p>
        <span className="mask-badge chip chip-accent !text-[11px]">
          <Icon name="shieldCheck" size={12} />
          {md.badge}
        </span>
      </div>
      <h2 className="mt-3 text-xl font-semibold md:text-2xl">{md.title}</h2>
      <p className="chip mt-3 mask-hint">
        <Icon name="scan" size={13} />
        {md.hint}
      </p>
      <div className="mask-line mt-5 rounded-xl border border-accent/40 bg-bg-2 p-4 text-base md:text-lg">
        <span className="label mb-1 block text-[10px] font-semibold text-accent-text">{md.who}</span>
        <p className="text-ink">
          {pre}
          <span className="mask-slot" dir="ltr">
            <span className="mask-digits" aria-hidden="true">
              {chars.map((ch, i) => (
                <span key={i} className="mask-ch">
                  {ch === " " ? " " : ch}
                </span>
              ))}
            </span>
            <span className="mask-placeholder" data-testid="mask-placeholder">
              {placeholder || "[PHONE_1]"}
            </span>
          </span>
          {bidi(post, locale)}
        </p>
      </div>
      <div className="label mt-3 flex justify-between text-[10px] text-ink-3">
        <span>{md.original}</span>
        <span>{md.masked}</span>
      </div>
    </div>
  );
}
