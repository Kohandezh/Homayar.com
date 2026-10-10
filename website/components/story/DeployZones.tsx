import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { Icon } from "@/components/ui/Icon";

function Block({ label, halves, split = false }: { label: string; halves: [string, string]; split?: boolean }) {
  return (
    <div className={`zblock ${split ? "is-split" : ""}`}>
      {[0, 1].map((k) => (
        <div key={k} className="zblock-half" data-half={k}>
          <span className="zblock-face">
            <Icon name="server" size={18} />
            <span className="zblock-bars" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </span>
          <span className="zblock-tag">{halves[k]}</span>
        </div>
      ))}
      <span className="zblock-label">{label}</span>
    </div>
  );
}

export function DeployZones({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const z = dict.deployment.zones;
  const halves: [string, string] = [z.halves[0], z.halves[1]];
  const icons = ["cloud", "server", "layers"];
  return (
    <section className="zones relative" data-zones data-testid="zones">
      <div className="zones-pin">
        <div className="container-x">
          <div className="naked flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="eyebrow">{z.eyebrow}</p>
              <h2 className="display-2 mt-3">{z.title}</h2>
              <p className="mt-3 max-w-2xl text-ink-2">{z.text}</p>
            </div>
            <p className="zones-hint label flex items-center gap-2 text-xs text-ink-3">
              <Icon name="scan" size={13} />
              {z.hint}
            </p>
          </div>
          <div className="zones-grid mt-8">
            {z.items.map((item, i) => (
              <div key={item.name} className={`zone ${i === 0 ? "is-active" : ""}`} data-zone={i} data-testid="zone">
                <div className="flex items-center justify-between">
                  <span className="label text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                  <Icon name={icons[i]} size={18} className="text-accent-text" />
                </div>
                <h3 className="mt-3 text-xl font-bold">{item.name}</h3>
                <p className="mt-1 text-sm text-ink-2">{bidi(item.note, locale)}</p>
                <div className="zone-slot" data-zone-slot={i}>
                  <div className="zone-static" aria-hidden="true">
                    <Block label={z.block} halves={halves} split={i === 2} />
                  </div>
                  {i === 1 ? (
                    <div className="zone-wall" data-wall aria-hidden="true">
                      <span className="label">{z.wall}</span>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
            <div className="zones-block" data-block aria-hidden="true">
              <Block label={z.block} halves={halves} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
