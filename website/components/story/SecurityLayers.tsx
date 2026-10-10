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
        <div className="container-x layers-head naked text-center">
          <p className="eyebrow">{l.eyebrow}</p>
          <h2 className="display-2 mx-auto mt-3 max-w-3xl">{l.title}</h2>
          <p className="layers-hint label mt-4 flex items-center justify-center gap-2 text-xs text-ink-3">
            <Icon name="scan" size={13} />
            {l.hint}
          </p>
        </div>
        <ol className="layers-stage container-x" aria-label={l.eyebrow}>
          {l.items.map((item, i) => (
            <li key={item.title} className="layer naked text-center" data-layer style={{ ["--k" as string]: i }} data-testid="layer">
              <div className="flex flex-col items-center gap-3">
                <span className="outline-num" aria-hidden="true">
                  {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
                </span>
                <Icon name={item.icon} size={24} className="text-accent-text" />
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
    <div className="mask naked text-center" data-mask data-testid="mask">
      <p className="eyebrow justify-center">{md.eyebrow}</p>
      <h2 className="display-2 mt-3">{md.title}</h2>
      <p className="mask-badge mt-4 flex items-center justify-center gap-2 text-sm font-semibold text-accent-text">
        <Icon name="shieldCheck" size={16} />
        {md.badge}
      </p>
      <p className="mask-hint label mt-3 flex items-center justify-center gap-2 text-xs text-ink-3">
        <Icon name="scan" size={13} />
        {md.hint}
      </p>
      <div className="mask-line mx-auto mt-10 max-w-2xl text-lg md:text-2xl">
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
      <div className="label mx-auto mt-6 flex max-w-2xl justify-between border-t border-border-2 pt-3 text-xs text-ink-3">
        <span>{md.original}</span>
        <span>{md.masked}</span>
      </div>
    </div>
  );
}
