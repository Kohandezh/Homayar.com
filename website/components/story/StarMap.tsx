import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { Icon } from "@/components/ui/Icon";

// About: the visitor looks up at the night sky. Each principle is a star, and scrolling draws the
// constellation from star to star (design-system/SCENE-RULES.md). With WebGL the engine hangs each
// principle next to its star. Without WebGL the principles simply stack.
export function StarMap({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const a = dict.about;
  const items = a.principles.items;
  return (
    <section className="stars relative" data-stars data-testid="stars" style={{ ["--n" as string]: items.length }}>
      <div className="stars-head naked text-center">
        <p className="eyebrow justify-center">{a.constellation.eyebrow}</p>
        <h2 className="mt-2 text-2xl font-bold md:text-3xl">{a.principles.title}</h2>
      </div>
      <div className="html3d-layer" data-html-layer>
        <ol className="html3d-camera stars-list" data-html-camera>
          {items.map((p, i) => (
            <li key={p.title} className="star-panel naked" data-star data-testid="star">
              <div className="flex items-center gap-3">
                <span className="text-xs font-bold text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                <Icon name={p.icon} size={18} className="text-accent-text" />
              </div>
              <h3 className="mt-3 text-2xl font-bold">{p.title}</h3>
              <p className="mt-2 text-ink-2">{bidi(p.text, locale)}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
