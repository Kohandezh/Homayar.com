import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Globe, Menu, Search } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";

export default function Header({ onOpenNavigation }: { onOpenNavigation: () => void }) {
  const navigate = useNavigate();
  const { user, workspace, workspaces, unavailableWorkspaceIds, setWorkspace } = useAuth();
  const { lang, setLang } = useI18n();
  const [query, setQuery] = useState("");

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const value = query.trim();
    if (value.length >= 2 && workspace) navigate(`/search?q=${encodeURIComponent(value)}`);
  }

  return (
    <header className="workspace-header">
      <button
        className="workspace-menu-button"
        onClick={onOpenNavigation}
        aria-label={lang === "fa" ? "باز کردن منو" : "Open navigation"}
      >
        <Menu size={19} />
      </button>
      <form className="workspace-search" onSubmit={submitSearch} role="search">
        <Search size={15} />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={
            lang === "fa" ? "جست‌وجوی جلسه‌ها و متن‌ها" : "Search meetings and transcripts"
          }
          aria-label={lang === "fa" ? "جست‌وجوی جلسه‌ها" : "Search meetings"}
          disabled={!workspace}
        />
      </form>

      <div className="workspace-header-actions">
        {workspaces.length > 1 ? (
          <select
            className="workspace-selector"
            value={workspace?.id ?? ""}
            onChange={(event) => setWorkspace(event.target.value)}
            aria-label={lang === "fa" ? "فضای کاری" : "Workspace"}
          >
            {!workspace && (
              <option value="" disabled>{lang === "fa" ? "انتخاب فضای کاری" : "Choose a workspace"}</option>
            )}
            {workspaces.map((item) => (
              <option key={item.id} value={item.id} disabled={unavailableWorkspaceIds.includes(item.id)}>
                {item.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="workspace-current-name">{workspace?.name}</span>
        )}
        <button
          onClick={() => setLang(lang === "fa" ? "en" : "fa")}
          className="workspace-icon-command"
          title={lang === "fa" ? "English" : "فارسی"}
          aria-label={lang === "fa" ? "Switch to English" : "تغییر به فارسی"}
        >
          <Globe size={14} />
          <span>{lang === "fa" ? "EN" : "FA"}</span>
        </button>
        <div className="workspace-account">
          <div className="workspace-avatar">
            {(user?.display_name || user?.email)?.[0]?.toUpperCase() || "H"}
          </div>
          <span>{user?.display_name || user?.email || user?.phone_e164}</span>
        </div>
      </div>
    </header>
  );
}
