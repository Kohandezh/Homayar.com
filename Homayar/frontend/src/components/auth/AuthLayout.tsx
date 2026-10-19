import { AudioLines, Globe, LockKeyhole } from "lucide-react";
import { Link, Outlet } from "react-router";
import { useI18n } from "@/lib/i18n-context";
import { useAuthCopy } from "@/components/auth/auth-copy";

const waveHeights = [28, 44, 68, 38, 82, 54, 94, 62, 76, 46, 88, 58, 72, 40, 64, 32];

function LanguageToggle() {
  const { lang, setLang } = useI18n();
  const { copy } = useAuthCopy();
  return (
    <button
      type="button"
      className="auth-language-toggle"
      onClick={() => setLang(lang === "fa" ? "en" : "fa")}
      aria-label={copy.layout.switchLanguage}
      lang={lang === "fa" ? "en" : "fa"}
    >
      <Globe aria-hidden="true" />
      <span>{copy.layout.switchLanguageShort}</span>
    </button>
  );
}

export default function AuthLayout() {
  const { copy } = useAuthCopy();
  return (
    <div className="auth-shell">
      <aside className="auth-brand-panel">
        <Link to="login" className="auth-brand-link" aria-label={copy.layout.homeLabel}>
          <span className="auth-brand-mark"><AudioLines aria-hidden="true" /></span>
          <span>
            <strong>Homayar</strong>
            <small>{copy.layout.tagline}</small>
          </span>
        </Link>

        <div className="auth-signal" aria-hidden="true">
          <div className="auth-signal-lines">
            {Array.from({ length: 5 }, (_, index) => <span key={index} />)}
          </div>
          <div className="auth-wave">
            {waveHeights.map((height, index) => (
              <span key={index} style={{ height: `${height}%` }} />
            ))}
          </div>
          <div className="auth-signal-caption">
            <span>HMYR / ACCESS</span>
            <span>01</span>
          </div>
        </div>

        <div className="auth-session-note">
          <LockKeyhole aria-hidden="true" />
          <span>{copy.layout.privateWorkspace}</span>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-topbar">
          <Link to="login" className="auth-mobile-brand" aria-label={copy.layout.homeLabel}>
            <span className="auth-brand-mark"><AudioLines aria-hidden="true" /></span>
            <strong>Homayar</strong>
          </Link>
          <div className="auth-topbar-actions">
            {/* The public website is another application on the same origin. */}
            <a href="/" className="auth-site-link">{copy.layout.website}</a>
            <LanguageToggle />
          </div>
        </div>
        <Outlet />
      </main>
    </div>
  );
}
