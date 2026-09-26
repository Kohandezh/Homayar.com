import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  AudioLines,
  Gauge,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Search,
  Settings,
  Users,
  X,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { describeApiError } from "@/lib/error-messages";
import { useI18n } from "@/lib/i18n-context";

const navItems = [
  { to: ".", label: "Overview", labelFa: "نمای کلی", icon: LayoutDashboard },
  { to: "meetings", label: "Meetings", labelFa: "جلسه‌ها", icon: AudioLines },
  { to: "search", label: "Search", labelFa: "جست‌وجو", icon: Search },
  { to: "members", label: "Members", labelFa: "اعضا", icon: Users },
  { to: "usage", label: "Usage", labelFa: "مصرف", icon: Gauge, adminOnly: true },
  { to: "settings", label: "Settings", labelFa: "تنظیمات", icon: Settings, adminOnly: true },
];

export default function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { signOut, user } = useAuth();
  const { lang } = useI18n();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");

  async function handleLogout() {
    setLogoutError("");
    setIsLoggingOut(true);
    try {
      await signOut();
      navigate("/login", { replace: true });
    } catch (error) {
      setLogoutError(
        describeApiError(error, {
          lang,
          fallback: lang === "fa" ? "خروج انجام نشد؛ دوباره تلاش کنید." : "Unable to sign out; try again.",
        }).text,
      );
    } finally {
      setIsLoggingOut(false);
    }
  }

  const canAdminister = user?.role === "owner" || user?.role === "admin";

  return (
    <aside className={`workspace-sidebar ${open ? "is-open" : ""}`}>
      <div className="workspace-brand">
        <div className="workspace-brand-mark"><AudioLines size={18} /></div>
        <div>
          <strong>Homayar</strong>
          <span>{lang === "fa" ? "هوشمندی مکالمه" : "Conversation intelligence"}</span>
        </div>
        <button
          className="workspace-sidebar-close"
          onClick={onClose}
          aria-label={lang === "fa" ? "بستن منو" : "Close navigation"}
        >
          <X size={18} />
        </button>
      </div>

      <nav className="workspace-navigation">
        {navItems
          .filter((item) => !item.adminOnly || canAdminister)
          .map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "."}
              onClick={onClose}
              className={({ isActive }) =>
                `workspace-navigation-link ${isActive ? "is-active" : ""}`
              }
            >
              <item.icon size={16} />
              <span>{lang === "fa" ? item.labelFa : item.label}</span>
            </NavLink>
          ))}
      </nav>

      <div className="workspace-sidebar-footer">
        <button onClick={handleLogout} disabled={isLoggingOut} className="workspace-logout">
          {isLoggingOut ? <LoaderCircle size={16} className="animate-spin" /> : <LogOut size={16} />}
          <span>
            {isLoggingOut
              ? lang === "fa" ? "در حال خروج" : "Signing out"
              : lang === "fa" ? "خروج" : "Sign out"}
          </span>
        </button>
        {logoutError && <p className="workspace-sidebar-error" role="alert">{logoutError}</p>}
      </div>
    </aside>
  );
}
