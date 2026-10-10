import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { LiveDemo } from "@/components/demo/LiveDemo";
import { dockPanels } from "@/components/story/ProductScreens";

// The product gallery (design-system/SCENE-RULES.md, "Product: the gallery visit").
// With WebGL, the engine hangs each exhibit on a wall of the 3D hall: it moves the HTML in 3D with the
// same camera as the scene, so every painting is always visible and the text stays real HTML.
// Without WebGL the exhibits simply stack.
export function ProductGallery({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const p = dict.product;
  const s = p.sections;
  const t = (text: string) => bidi(text, locale);
  const docks = [s.ingest, s.transcription, s.agents, s.dashboard, s.reports, s.copilot, s.admin];
  const panels = dockPanels(dict, locale);

  return (
    <section className="exhibits relative" data-exhibits data-testid="exhibits" style={{ ["--n" as string]: docks.length + 1 }}>
      <div className="html3d-layer" data-html-layer>
        <div className="html3d-camera exhibit-camera" data-html-camera>
          {docks.map((d, i) => (
            <article key={d.id} className="exhibit" data-exhibit data-testid="exhibit">
              <div className="exhibit-text">
                <div className="flex items-end gap-4">
                  <span className="outline-num" aria-hidden="true">
                    {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
                  </span>
                  <span className="pb-2 text-xs font-semibold text-accent-text">{d.label}</span>
                </div>
                <h2 className="mt-4 text-2xl font-bold md:text-3xl">{t(d.title)}</h2>
                {"text" in d ? <p className="mt-3 text-ink-2">{t(d.text)}</p> : null}
                {"bullets" in d ? (
                  <ul className="mt-4 flex flex-col gap-2 text-sm text-ink-2">
                    {d.bullets.slice(0, 3).map((b) => (
                      <li key={b} className="flex gap-3">
                        <span className="glow-dot mt-2" aria-hidden="true" />
                        <span>{t(b)}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
              <div className="exhibit-screen" aria-hidden="true">
                {panels[i]}
              </div>
            </article>
          ))}
          <article className="exhibit exhibit-demo" data-exhibit data-testid="exhibit">
            <div className="text-center">
              <p className="eyebrow justify-center">{p.console.demoEyebrow}</p>
              <h2 className="mt-2 text-2xl font-bold md:text-3xl">{p.console.demoTitle}</h2>
            </div>
            <div className="exhibit-demo-fit" data-exhibit-fit>
              <div data-exhibit-fit-inner>
                <LiveDemo demo={dict.demo} mock={dict.mock} locale={locale} brand={dict.brand} frame="window" />
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
