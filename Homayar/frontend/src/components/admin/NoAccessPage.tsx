import { Link } from "react-router-dom";
import { ArrowLeft, ShieldOff } from "lucide-react";
import { Surface, buttonVariants } from "@heroui/react";
import { useI18n } from "@/lib/i18n-context";

/**
 * Shown by the admin route guard in place of a page the selected workspace
 * may not open. The URL stays as it is, so switching to a workspace that has
 * the permission shows the page without another visit.
 */
export default function NoAccessPage() {
  const { lang } = useI18n();
  const isFa = lang === "fa";

  return (
    <section className="platform-page max-w-2xl" aria-labelledby="no-access-title">
      <Surface className="flex flex-col items-start gap-4 rounded-2xl p-6">
        <span className="grid size-10 place-items-center rounded-full bg-danger/10 text-danger">
          <ShieldOff size={20} aria-hidden="true" />
        </span>
        <div className="flex flex-col gap-2">
          <h1 id="no-access-title" className="m-0 text-xl font-semibold text-foreground">
            {isFa ? "دسترسی ندارید" : "No access"}
          </h1>
          <p className="m-0 text-sm text-muted">
            {isFa
              ? "نقش شما در این فضای کاری اجازهٔ باز کردن این صفحه را نمی‌دهد. اگر به آن نیاز دارید، از مالک یا مدیر فضای کاری بخواهید."
              : "Your role in this workspace does not allow this page. If you need it, ask a workspace owner or administrator."}
          </p>
        </div>
        <Link to="/" className={buttonVariants({ variant: "secondary", className: "gap-2" })}>
          <ArrowLeft size={16} aria-hidden="true" className="rtl-mirror" />
          {isFa ? "بازگشت به نمای کلی" : "Back to overview"}
        </Link>
      </Surface>
    </section>
  );
}
