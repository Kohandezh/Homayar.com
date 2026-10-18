import { Fragment, type ReactNode } from "react";
import { Button, Chip } from "@heroui/react";
import { RotateCcw } from "lucide-react";

import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { ErrorState } from "@/components/platform/Ui";
import { lastIncludedIso } from "@/components/org/org-model";
import { formatDay } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { orgRoleLabel } from "@/lib/org-roles";

/**
 * The page header of the org pages. Same markup and classes as `PageHeader`
 * in platform/Ui.tsx. One of the eyebrow and the title is the organization's
 * name (`name` says which); only that one gets `dir="auto"`.
 */
export function OrgPageHeader({
  eyebrow,
  title,
  name,
  badge,
  actions,
}: {
  eyebrow: string;
  title: string;
  name: "eyebrow" | "title";
  badge?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="platform-page-header">
      <div>
        <p dir={name === "eyebrow" ? "auto" : undefined}>{eyebrow}</p>
        <div className="flex flex-wrap items-center gap-2">
          <h1 dir={name === "title" ? "auto" : undefined}>{title}</h1>
          {badge}
        </div>
      </div>
      {actions && <div className="platform-page-actions">{actions}</div>}
    </header>
  );
}

export function OrgRoleChip({ role }: { role: string }) {
  const { lang } = useI18n();
  return (
    <Chip color={role === "owner" || role === "admin" ? "accent" : "default"} variant="soft" size="sm">
      <Chip.Label>{orgRoleLabel(role, lang)}</Chip.Label>
    </Chip>
  );
}

/** A failed read, described by its error code, with a way to ask again. */
export function OrgReadError({ error, fallback, onRetry }: { error: unknown; fallback: string; onRetry: () => void }) {
  const { lang } = useI18n();
  return (
    <div className="space-y-3">
      <ErrorState error={error} codes={ORG_ERROR_CODES} fallback={fallback} />
      <Button size="sm" variant="secondary" onPress={onRetry}>
        <RotateCcw aria-hidden="true" size={14} />
        {ORG_COPY[lang].retry}
      </Button>
    </div>
  );
}

/** A calendar date; Latin digits read left to right inside a Persian page. */
export function DayText({ value }: { value: string }) {
  const { lang } = useI18n();
  return <span dir={lang === "en" ? "ltr" : undefined}>{formatDay(value, lang)}</span>;
}

/** The last included day of a stored `valid_until` (the midnight after it, minus 1 ms). */
export function EndDayText({ value }: { value: string }) {
  return <DayText value={lastIncludedIso(value)} />;
}

/**
 * A sentence from a copy template such as "{name} stops seeing {workspace}
 * now." Each `{key}` becomes its name inside `<bdi dir="auto">`: only the
 * name picks its own direction, never the whole sentence.
 */
export function NamedText({ template, names }: { template: string; names: Record<string, string> }) {
  const parts = template.split(/\{(\w+)\}/);
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 && Object.hasOwn(names, part) ? (
          <bdi key={index} dir="auto">
            {names[part]}
          </bdi>
        ) : (
          <Fragment key={index}>{part}</Fragment>
        ),
      )}
    </>
  );
}
