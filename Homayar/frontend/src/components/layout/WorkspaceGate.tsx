import { useEffect, useRef } from "react";
import { Building2, ShieldAlert } from "lucide-react";
import { Alert, Button, Link, Surface } from "@heroui/react";
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
    <section className="platform-page max-w-2xl" aria-labelledby="workspace-gate-title">
      <header className="platform-page-header">
        <div>
          <h1
            id="workspace-gate-title"
            ref={heading}
            tabIndex={-1}
            className="rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-4"
          >
            {usable.length ? copy.chooseTitle : copy.noneTitle}
          </h1>
        </div>
      </header>

      {workspaceNotice && (
        <Alert status="danger" role="alert" className="mb-4">
          <Alert.Indicator>
            <ShieldAlert size={16} aria-hidden="true" />
          </Alert.Indicator>
          <Alert.Content>
            <Alert.Description>{copy.lost(workspaceNotice.workspaceName)}</Alert.Description>
          </Alert.Content>
        </Alert>
      )}

      {usable.length ? (
        <>
          <p className="mt-0 mb-4 text-sm leading-relaxed text-muted">{copy.chooseBody}</p>
          <ul className="m-0 grid list-none gap-2 p-0">
            {usable.map((candidate) => (
              <li key={candidate.id}>
                <Button
                  variant="outline"
                  fullWidth
                  className="h-auto min-h-13 justify-start gap-3 py-3 text-start"
                  onPress={() => setWorkspace(candidate.id)}
                >
                  <Building2 size={18} aria-hidden="true" />
                  <strong className="flex-1 font-semibold [overflow-wrap:anywhere]">{candidate.name}</strong>
                  <span className="text-xs font-normal text-muted">{roleLabel(candidate.role, lang)}</span>
                </Button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <Surface className="flex flex-col items-start gap-3 rounded-2xl p-5 text-sm leading-relaxed">
          <p className="m-0">{copy.noneBody}</p>
          <Link href="/contact">{copy.contact}</Link>
        </Surface>
      )}
    </section>
  );
}
