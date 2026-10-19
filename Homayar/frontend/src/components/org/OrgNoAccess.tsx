import { Link } from "react-router";
import { ArrowLeft, ShieldOff } from "lucide-react";
import { Surface, buttonVariants } from "@heroui/react";

import { ORG_COPY } from "@/components/org/org-copy";
import { useI18n } from "@/lib/i18n-context";

/**
 * Shown instead of an org page when the organization does not exist, the
 * user is not (or no longer) a member, or their org role lacks the page's
 * permission. The backend never tells these apart, so neither does this. The
 * URL stays as it is.
 */
export default function OrgNoAccess() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];

  return (
    <section className="platform-page max-w-2xl" aria-labelledby="org-no-access-title">
      <Surface className="flex flex-col items-start gap-4 rounded-2xl p-6">
        <span className="grid size-10 place-items-center rounded-full bg-danger/10 text-danger">
          <ShieldOff size={20} aria-hidden="true" />
        </span>
        <div className="flex flex-col gap-2">
          <h1 id="org-no-access-title" className="m-0 text-xl font-semibold text-foreground">
            {copy.noAccessTitle}
          </h1>
          <p className="m-0 text-sm text-muted">{copy.noAccessBody}</p>
        </div>
        <Link to="/" className={buttonVariants({ variant: "secondary", className: "gap-2" })}>
          <ArrowLeft size={16} aria-hidden="true" className="rtl-mirror" />
          {copy.backToOverview}
        </Link>
      </Surface>
    </section>
  );
}
