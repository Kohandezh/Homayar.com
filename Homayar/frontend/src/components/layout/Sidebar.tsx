import { useId, useState } from "react";
import { NavLink, useNavigate } from "react-router";
import { AudioLines, LogOut } from "lucide-react";
import { Button, Separator, Spinner, buttonVariants } from "@heroui/react";
import { useAuth } from "@/lib/auth-context";
import { CONSOLE_BUILD } from "@/lib/build-info";
import { describeApiError } from "@/lib/error-messages";
import { isolateLtr } from "@/lib/format";
import { useI18n, type Lang } from "@/lib/i18n-context";
import { ADMIN_NAV, MAIN_NAV, type MainNavEntry } from "@/lib/navigation";
import { canOpenAdminEntry, useCan } from "@/lib/use-can";
import OrgSidebarSection from "@/components/org/OrgSidebarSection";

function NavItem({ entry, lang, onNavigate }: { entry: MainNavEntry; lang: Lang; onNavigate?: () => void }) {
  return (
    <NavLink
      to={entry.path}
      // "/" and "/admin" are parents of other entries; only an exact match marks them.
      end={entry.path === "/" || entry.path === "/admin"}
      onClick={onNavigate}
      className={({ isActive }) =>
        buttonVariants({
          variant: isActive ? "secondary" : "ghost",
          fullWidth: true,
          className: "justify-start gap-3 font-normal",
        })
      }
    >
      <entry.icon size={16} aria-hidden="true" />
      <span className="truncate">{entry.label[lang]}</span>
    </NavLink>
  );
}

/**
 * The console navigation: the desktop sidebar and the body of the mobile
 * drawer. Admin entries appear only when they open (canOpenAdminEntry), the
 * same check the route guard makes.
 */
export default function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const { signOut } = useAuth();
  const { lang } = useI18n();
  const can = useCan();
  const adminHeadingId = useId();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const isFa = lang === "fa";
  const adminEntries = ADMIN_NAV.filter((entry) => canOpenAdminEntry(can, entry));

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
          fallback: isFa ? "خروج انجام نشد؛ دوباره تلاش کنید." : "Unable to sign out; try again.",
        }).text,
      );
    } finally {
      setIsLoggingOut(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex items-center gap-3 px-2">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
          <AudioLines size={18} aria-hidden="true" />
        </span>
        <span className="flex min-w-0 flex-col">
          <strong className="text-sm font-semibold text-foreground">Homayar</strong>
          <span className="truncate text-xs text-muted">{isFa ? "هوشمندی مکالمه" : "Conversation intelligence"}</span>
        </span>
      </div>

      <nav aria-label={isFa ? "ناوبری اصلی" : "Main navigation"} className="-m-1 flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-1">
        {MAIN_NAV.map((entry) => (
          <NavItem key={entry.path} entry={entry} lang={lang} onNavigate={onNavigate} />
        ))}

        {adminEntries.length > 0 && (
          <>
            <Separator className="my-2" />
            <div role="group" aria-labelledby={adminHeadingId} className="flex flex-col gap-1">
              <span id={adminHeadingId} className="px-3 pb-1 text-xs font-medium text-muted">
                {isFa ? "مدیریت" : "Administration"}
              </span>
              {adminEntries.map((entry) => (
                <NavItem key={entry.path} entry={entry} lang={lang} onNavigate={onNavigate} />
              ))}
            </div>
          </>
        )}

        <OrgSidebarSection onNavigate={onNavigate} />
      </nav>

      <div className="flex flex-col gap-2 border-t border-separator pt-3">
        {/* The build version line (HOM-232 V4): one muted line for everyone
            who sees the Administration section, so any user of the admin
            area can read what build they are on. Only the text is isolated
            left to right, so in Persian the line still aligns with the other
            footer text. */}
        {adminEntries.length > 0 && (
          <p className="m-0 px-3 text-xs text-muted">
            {isolateLtr(`Homayar ${CONSOLE_BUILD.version} · ${CONSOLE_BUILD.sha ?? (isFa ? "منتشرنشده" : "unreleased")}`)}
          </p>
        )}
        <Button
          variant="ghost"
          fullWidth
          className="justify-start gap-3 font-normal"
          isPending={isLoggingOut}
          onPress={() => void handleLogout()}
        >
          {/* The button text already says "Signing out"; the spinner stays out of the accessibility tree. */}
          {isLoggingOut
            ? <Spinner size="sm" color="current" role={undefined} aria-label={undefined} aria-hidden="true" />
            : <LogOut size={16} aria-hidden="true" />}
          <span>
            {isLoggingOut
              ? isFa ? "در حال خروج" : "Signing out"
              : isFa ? "خروج" : "Sign out"}
          </span>
        </Button>
        {logoutError && <p className="m-0 px-3 text-xs text-danger" role="alert">{logoutError}</p>}
      </div>
    </div>
  );
}
