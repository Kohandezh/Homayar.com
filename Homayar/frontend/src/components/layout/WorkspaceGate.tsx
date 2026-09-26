import { useEffect, useRef } from "react";
import { Building2, ShieldAlert } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { roleLabel } from "@/lib/roles";

const COPY = {
  en: {
    chooseTitle: "Choose a workspace",
    chooseBody: "You belong to more than one workspace. Pick the one to work in; you can switch later from the header.",
    noneTitle: "No workspace available",
    noneBody:
      "Your account is not a member of any active workspace. Ask a workspace administrator to invite you, or contact support.",
    lost: (name: string | null) =>
      name
        ? `Your access to “${name}” was removed, expired or suspended. Nothing from it is shown any more.`
        : "Your access to the previous workspace was removed, expired or suspended. Nothing from it is shown any more.",
    contact: "Contact support",
  },
  fa: {
    chooseTitle: "انتخاب فضای کاری",
    chooseBody: "شما عضو بیش از یک فضای کاری هستید. فضای کاری مورد نظر را انتخاب کنید؛ بعداً از بالای صفحه می‌توانید آن را عوض کنید.",
    noneTitle: "فضای کاری در دسترس نیست",
    noneBody:
      "حساب شما عضو هیچ فضای کاری فعالی نیست. از مدیر فضای کاری بخواهید شما را دعوت کند یا با پشتیبانی تماس بگیرید.",
    lost: (name: string | null) =>
      name
        ? `دسترسی شما به «${name}» حذف، منقضی یا معلق شده است. دیگر چیزی از آن نمایش داده نمی‌شود.`
        : "دسترسی شما به فضای کاری قبلی حذف، منقضی یا معلق شده است. دیگر چیزی از آن نمایش داده نمی‌شود.",
    contact: "تماس با پشتیبانی",
  },
} as const;

/** Shown instead of workspace pages while no workspace is selected. */
export default function WorkspaceGate() {
  const { workspaces, unavailableWorkspaceIds, workspaceNotice, setWorkspace } = useAuth();
  const { lang } = useI18n();
  const copy = COPY[lang];
  const heading = useRef<HTMLHeadingElement>(null);
  const usable = workspaces.filter((candidate) => !unavailableWorkspaceIds.includes(candidate.id));

  // Move focus here when the page under the user was replaced by this gate.
  useEffect(() => {
    heading.current?.focus();
  }, [workspaceNotice]);

  return (
    <section className="platform-page workspace-gate" aria-labelledby="workspace-gate-title">
      <header className="platform-page-header">
        <div>
          <h1 id="workspace-gate-title" ref={heading} tabIndex={-1}>
            {usable.length ? copy.chooseTitle : copy.noneTitle}
          </h1>
        </div>
      </header>

      {workspaceNotice && (
        <div className="workspace-gate-notice" role="alert">
          <ShieldAlert size={16} aria-hidden="true" />
          <span>{copy.lost(workspaceNotice.workspaceName)}</span>
        </div>
      )}

      {usable.length ? (
        <>
          <p className="workspace-gate-body">{copy.chooseBody}</p>
          <ul className="workspace-gate-list">
            {usable.map((candidate) => (
              <li key={candidate.id}>
                <button type="button" onClick={() => setWorkspace(candidate.id)}>
                  <Building2 size={18} aria-hidden="true" />
                  <strong>{candidate.name}</strong>
                  <span>{roleLabel(candidate.role, lang)}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <div className="platform-surface workspace-gate-empty">
          <p>{copy.noneBody}</p>
          <a href="/contact">{copy.contact}</a>
        </div>
      )}
    </section>
  );
}
