import { useId } from "react";
import { NavLink, matchPath, useLocation, useNavigate } from "react-router";
import { Building2, KeyRound, LayoutDashboard, Users, type LucideIcon } from "lucide-react";
import { ListBox, Select, Separator, buttonVariants } from "@heroui/react";

import { ORG_COPY, type OrgCopy } from "@/components/org/org-copy";
import { useI18n } from "@/lib/i18n-context";
import { useOrganizations } from "@/lib/org-api";
import { orgPermissionsFor, type OrgPermission } from "@/lib/org-roles";

interface OrgNavEntry {
  /** Appended to `/org/<id>`. */
  suffix: string;
  label: (copy: OrgCopy) => string;
  icon: LucideIcon;
  permission: OrgPermission;
}

const ORG_NAV: readonly OrgNavEntry[] = [
  { suffix: "", label: (copy) => copy.navOverview, icon: LayoutDashboard, permission: "organization:analytics:read" },
  { suffix: "/members", label: (copy) => copy.navMembers, icon: Users, permission: "organization:member:read" },
  { suffix: "/workspaces", label: (copy) => copy.navWorkspaces, icon: Building2, permission: "organization:workspace:read" },
  { suffix: "/scope-grants", label: (copy) => copy.navScopeGrants, icon: KeyRound, permission: "organization:member:read" },
];

/** The organization id of an `/org/<id>` path, decoded (`matchPath` leaves it as in the URL). */
function currentOrganizationId(pathname: string): string | null {
  const raw = matchPath({ path: "/org/:organizationId", end: false }, pathname)?.params.organizationId;
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    // A malformed escape: no organization can have that id.
    return raw;
  }
}

/**
 * The organization part of the sidebar: a switcher, shown only when the user
 * belongs to at least one organization, and, under `/org/<id>`, a group of
 * that organization's pages. The links follow the role in the switcher list;
 * that is display only, the route guard asks the server again.
 */
export default function OrgSidebarSection({ onNavigate }: { onNavigate?: () => void }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const headingId = useId();
  const organizationsQuery = useOrganizations();
  const organizations = organizationsQuery.data?.items ?? [];

  // Loading, a failure and an empty list all render nothing.
  if (organizations.length === 0) return null;

  const currentId = currentOrganizationId(pathname);
  const current = organizations.find((organization) => organization.id === currentId) ?? null;
  const permissions = orgPermissionsFor(current?.role);
  const links = currentId ? ORG_NAV.filter((entry) => permissions.has(entry.permission)) : [];
  const base = currentId ? `/org/${encodeURIComponent(currentId)}` : "";

  return (
    <>
      <Separator className="my-2" />
      <Select
        aria-label={copy.organization}
        placeholder={copy.chooseOrganization}
        value={current?.id ?? null}
        onChange={(key) => {
          if (typeof key !== "string" || key === current?.id) return;
          navigate(`/org/${encodeURIComponent(key)}`);
          onNavigate?.();
        }}
        fullWidth
        variant="secondary"
        // As in Header.tsx: layout containment keeps React Aria's hidden native
        // <select> from widening the page by 1px in right to left.
        className="px-1 [contain:layout]"
      >
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            {organizations.map((organization) => (
              <ListBox.Item key={organization.id} id={organization.id} textValue={organization.name}>
                <span dir="auto">{organization.name}</span>
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      {links.length > 0 && (
        <div role="group" aria-labelledby={headingId} className="mt-2 flex flex-col gap-1">
          <span id={headingId} className="px-3 pb-1 text-xs font-medium text-muted">
            {copy.organization}
          </span>
          {links.map((entry) => (
            <NavLink
              key={entry.suffix}
              to={`${base}${entry.suffix}`}
              // The overview is the parent of the other three.
              end={entry.suffix === ""}
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
              <span className="truncate">{entry.label(copy)}</span>
            </NavLink>
          ))}
        </div>
      )}
    </>
  );
}
