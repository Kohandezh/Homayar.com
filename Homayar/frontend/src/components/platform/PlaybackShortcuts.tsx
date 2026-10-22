/**
 * The keyboard shortcut list under the player (HOM-247): which keys drive
 * playback and turn navigation. The keys are physical (`event.code`), so they
 * are wrapped LTR even on the Persian, right-to-left page.
 */
const SHORTCUTS_COPY: Record<"fa" | "en", { title: string; keys: [string, string][] }> = {
  en: {
    title: "Keyboard shortcuts",
    keys: [
      ["K", "Play or pause"],
      ["J", "Back 5 seconds"],
      ["L", "Forward 5 seconds"],
      ["N", "Next turn"],
      ["P", "Previous turn"],
    ],
  },
  fa: {
    title: "کلیدهای میان‌بر",
    keys: [
      ["K", "پخش یا توقف"],
      ["J", "۵ ثانیه عقب"],
      ["L", "۵ ثانیه جلو"],
      ["N", "بخش بعدی"],
      ["P", "بخش قبلی"],
    ],
  },
};

export default function PlaybackShortcuts({ lang }: { lang: "fa" | "en" }) {
  const copy = SHORTCUTS_COPY[lang];
  return (
    <section className="playback-shortcuts" aria-label={copy.title}>
      {copy.keys.map(([key, label]) => (
        <div key={key}>
          <kbd dir="ltr">{key}</kbd>
          <span>{label}</span>
        </div>
      ))}
    </section>
  );
}
