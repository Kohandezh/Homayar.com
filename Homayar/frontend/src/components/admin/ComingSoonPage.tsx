import { Hourglass } from "lucide-react";
import { Surface } from "@heroui/react";
import { useI18n } from "@/lib/i18n-context";
import type { AdminNavEntry } from "@/lib/navigation";

/** Placeholder for an admin page whose ticket has not landed yet. */
export default function ComingSoonPage({ entry }: { entry: AdminNavEntry }) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const Icon = entry.icon;

  return (
    <section className="platform-page" aria-labelledby="coming-soon-title">
      <header className="platform-page-header">
        <div>
          <p>{isFa ? "مدیریت" : "Administration"}</p>
          <h1 id="coming-soon-title" className="flex items-center gap-2">
            <Icon size={20} aria-hidden="true" />
            {entry.label[lang]}
          </h1>
        </div>
      </header>
      <Surface className="flex items-center gap-3 rounded-2xl p-6 text-muted">
        <Hourglass size={18} aria-hidden="true" />
        <p className="m-0 text-sm">{isFa ? "به‌زودی" : "Coming soon"}</p>
      </Surface>
    </section>
  );
}
