import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { CalendarClock, Mail, ShieldCheck, Trash2, UserPlus, Users } from "lucide-react";
import {
  AlertDialog,
  Avatar,
  Button,
  Card,
  Description,
  Input,
  Label,
  ListBox,
  Modal,
  NumberField,
  SearchField,
  Select,
  Table,
  Tabs,
  TextField,
  toast,
} from "@heroui/react";

import { useAuth } from "@/lib/auth-context";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import {
  useChangeMemberRole,
  useInvitations,
  useInviteMember,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
} from "@/lib/platform-api";
import { roleLabel } from "@/lib/roles";
import { statusLabel } from "@/lib/status-labels";
import { useCan } from "@/lib/use-can";
import type {
  AssignableWorkspaceRole,
  InvitationStatus,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRole,
} from "@/types/platform";
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/platform/Ui";

const ALL_ASSIGNABLE_ROLES: AssignableWorkspaceRole[] = ["admin", "member", "viewer"];
const ADMIN_ASSIGNABLE_ROLES: AssignableWorkspaceRole[] = ["member", "viewer"];
const FILTER_ROLES: WorkspaceRole[] = ["owner", "admin", "member", "viewer"];
const INVITATION_TABS: InvitationStatus[] = ["pending", "accepted", "revoked", "expired"];

const MIN_EXPIRY_DAYS = 1;
const MAX_EXPIRY_DAYS = 30;
const DEFAULT_EXPIRY_DAYS = 7;

/** Error codes of backend/routers/v1/members.py and services/member_service.py. */
const MEMBER_ERROR_CODES: ErrorCodeMessages = {
  member_not_found: {
    fa: "این عضو دیگر در این فضای کاری نیست؛ فهرست به‌روز شد.",
    en: "This member is no longer in the workspace; the list was refreshed.",
  },
  member_operation_forbidden: {
    fa: "شما اجازهٔ این تغییر را ندارید. مدیرها فقط نقش «عضو» و «بیننده» را مدیریت می‌کنند و نقش مالک تغییرپذیر نیست.",
    en: "You are not allowed to make this change. Administrators manage only members and viewers, and the owner cannot be changed.",
  },
  member_conflict: {
    fa: "این ایمیل از قبل عضو این فضای کاری است.",
    en: "This email already belongs to a member of this workspace.",
  },
  invitation_pending: {
    fa: "برای این ایمیل یک دعوت فعال وجود دارد؛ آن را لغو کنید یا تا پایان اعتبارش صبر کنید.",
    en: "This email already has an active invitation; revoke it or wait until it expires.",
  },
  invitation_delivery_failed: {
    fa: "ایمیل دعوت ارسال نشد و این دعوت به‌طور خودکار لغو شد. کمی بعد دوباره تلاش کنید؛ اگر تکرار شد با پشتیبانی تماس بگیرید.",
    en: "The invitation email could not be sent, so the invitation was cancelled automatically. Try again later; contact support if it keeps happening.",
  },
  invitation_not_found: {
    fa: "این دعوت پیدا نشد؛ فهرست به‌روز شد.",
    en: "This invitation was not found; the list was refreshed.",
  },
  invitation_revoked: {
    fa: "این دعوت قبلاً لغو شده است.",
    en: "This invitation was already revoked.",
  },
  invitation_expired: {
    fa: "این دعوت منقضی شده است.",
    en: "This invitation has expired.",
  },
  invitation_already_accepted: {
    fa: "این دعوت پیش‌تر پذیرفته شده است؛ برای قطع دسترسی، عضو را حذف کنید.",
    en: "This invitation was already accepted; remove the member to end their access.",
  },
  validation_error: {
    fa: "نشانی ایمیل یا مدت اعتبار پذیرفته نشد؛ آن‌ها را بررسی کنید.",
    en: "The email address or the expiry was not accepted; check them.",
  },
};

const ADMIN_CONSEQUENCE = {
  fa: "مدیر می‌تواند اعضا، تنظیمات و لاگ ممیزی را مدیریت کند.",
  en: "An administrator can manage members, settings and the audit log.",
};

function canManageTarget(
  actorRole: WorkspaceRole,
  actorUserId: string | undefined,
  member: WorkspaceMember,
): boolean {
  if (member.user_id === actorUserId || member.role === "owner") return false;
  if (actorRole === "owner") return true;
  return actorRole === "admin" && (member.role === "member" || member.role === "viewer");
}

function memberName(member: WorkspaceMember, isFa: boolean): string {
  return member.display_name || member.email || member.phone_e164 || (isFa ? "این عضو" : "this member");
}

function memberContact(member: WorkspaceMember, isFa: boolean): string {
  return member.email ?? member.phone_e164 ?? (isFa ? "بدون اطلاعات تماس" : "No contact information");
}

function assignableRoles(actorRole: WorkspaceRole): AssignableWorkspaceRole[] {
  return actorRole === "owner" ? ALL_ASSIGNABLE_ROLES : ADMIN_ASSIGNABLE_ROLES;
}

/**
 * A single-choice HeroUI Select over a fixed list of options. With `showLabel`
 * the text is a real `<Label>`: clicking it focuses the trigger. Without it the
 * same text is only the accessible name, for the tight cells of the table.
 */
function OptionSelect({
  label,
  showLabel,
  value,
  options,
  isDisabled,
  describedBy,
  onChange,
}: {
  label: string;
  showLabel?: boolean;
  value: string;
  options: { id: string; text: string }[];
  isDisabled?: boolean;
  describedBy?: string;
  onChange: (value: string) => void;
}) {
  return (
    <Select
      aria-label={showLabel ? undefined : label}
      aria-describedby={describedBy}
      value={value}
      isDisabled={isDisabled}
      onChange={(next) => {
        if (typeof next === "string") onChange(next);
      }}
    >
      {showLabel && <Label>{label}</Label>}
      <Select.Trigger>
        <Select.Value />
        <Select.Indicator />
      </Select.Trigger>
      <Select.Popover>
        <ListBox>
          {options.map((option) => (
            <ListBox.Item key={option.id} id={option.id} textValue={option.text}>
              {option.text}
              <ListBox.ItemIndicator />
            </ListBox.Item>
          ))}
        </ListBox>
      </Select.Popover>
    </Select>
  );
}

interface RowActionsProps {
  member: WorkspaceMember;
  actorRole: WorkspaceRole;
  isSelf: boolean;
  manageable: boolean;
  busy: boolean;
  removeError: Error | null;
  isFa: boolean;
  lang: "fa" | "en";
  onApplyRole: (member: WorkspaceMember, role: AssignableWorkspaceRole) => Promise<boolean>;
  onRemove: (member: WorkspaceMember) => Promise<boolean>;
  onActionStarted: () => void;
}

function MemberRowActions({
  member,
  actorRole,
  isSelf,
  manageable,
  busy,
  removeError,
  isFa,
  lang,
  onApplyRole,
  onRemove,
  onActionStarted,
}: RowActionsProps) {
  const name = memberName(member, isFa);
  // The select is a draft: nothing is sent until "Apply" (QA M-01). A server
  // role that changes underneath (refetch, another administrator) resets it.
  const [draftRole, setDraftRole] = useState<WorkspaceRole>(member.role);
  const [serverRole, setServerRole] = useState<WorkspaceRole>(member.role);
  if (serverRole !== member.role) {
    setServerRole(member.role);
    setDraftRole(member.role);
  }
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [pending, setPending] = useState<"role" | "remove" | null>(null);
  const selectRef = useRef<HTMLDivElement>(null);
  // Apply unmounts once the draft matches the server role, so focus has to be
  // put back on the select by hand (QA M-04). Closing the remove dialog needs
  // nothing: the table puts focus back on the cell the trash button sits in.
  const focusAfterRender = useRef<"select" | null>(null);

  useEffect(() => {
    if (!focusAfterRender.current) return;
    focusAfterRender.current = null;
    selectRef.current?.querySelector<HTMLElement>("button")?.focus();
  });

  if (!manageable) {
    return (
      <span className="block text-end text-xs text-muted">
        {isSelf ? (isFa ? "حساب شما" : "Your account") : (isFa ? "فقط خواندنی" : "Read only")}
      </span>
    );
  }

  const roleDirty = draftRole !== member.role;
  const noteId = `member-role-note-${member.id}`;

  async function applyRole() {
    if (pending || !roleDirty || draftRole === "owner") return;
    setPending("role");
    const applied = await onApplyRole(member, draftRole);
    setPending(null);
    // A refused change never shows the attempted role as current.
    if (!applied) setDraftRole(member.role);
    focusAfterRender.current = "select";
  }

  async function confirmRemove() {
    if (pending) return;
    setPending("remove");
    const removed = await onRemove(member);
    setPending(null);
    if (removed) setConfirmingRemove(false);
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <div ref={selectRef} className="w-36">
        <OptionSelect
          label={isFa ? `نقش ${name}` : `Role for ${name}`}
          value={draftRole}
          isDisabled={pending === "role"}
          describedBy={roleDirty ? noteId : undefined}
          options={assignableRoles(actorRole).map((role) => ({ id: role, text: roleLabel(role, lang) }))}
          onChange={(next) => setDraftRole(next as AssignableWorkspaceRole)}
        />
      </div>

      {roleDirty ? (
        <>
          <Button size="sm" variant="primary" isDisabled={pending === "role"} onPress={() => void applyRole()}>
            {pending === "role" ? (isFa ? "در حال اعمال" : "Applying") : (isFa ? "اعمال" : "Apply")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            isDisabled={pending === "role"}
            onPress={() => {
              setDraftRole(member.role);
              focusAfterRender.current = "select";
            }}
          >
            {isFa ? "انصراف" : "Cancel"}
          </Button>
          <span id={noteId} className="basis-full text-end text-xs text-muted">
            {isFa
              ? `نقش ${name} پس از «اعمال» به «${roleLabel(draftRole, lang)}» تغییر می‌کند.`
              : `${name}'s role changes to ${roleLabel(draftRole, lang)} only after Apply.`}
            {draftRole === "admin" && ` ${ADMIN_CONSEQUENCE[lang]}`}
          </span>
        </>
      ) : (
        // The dialog closes back into the table, which owns focus inside the
        // grid: focus lands on the cell that holds the trash button, and the
        // button itself is one Tab away. That still answers QA M-08 (focus does
        // not fall to the page), but by the cell, not by the button.
        // AlertDialog.Trigger is skipped on purpose: it would wrap the button in
        // a second role="button" element.
        <AlertDialog
          isOpen={confirmingRemove}
          onOpenChange={(open) => {
            if (!pending) setConfirmingRemove(open);
          }}
        >
          <Button
            isIconOnly
            size="sm"
            variant="ghost"
            className="text-danger"
            // Its own removal must not disable it: the dialog closes back onto
            // this button's cell, and a disabled button cannot take focus.
            isDisabled={busy && pending !== "remove"}
            aria-label={isFa ? `حذف ${name}` : `Remove ${name}`}
            onPress={onActionStarted}
          >
            <Trash2 aria-hidden="true" size={15} />
          </Button>
          <AlertDialog.Backdrop>
            <AlertDialog.Container>
              <AlertDialog.Dialog>
                <AlertDialog.Header>
                  <AlertDialog.Icon status="danger" />
                  <AlertDialog.Heading>{isFa ? `حذف ${name}` : `Remove ${name}`}</AlertDialog.Heading>
                </AlertDialog.Header>
                <AlertDialog.Body className="space-y-3">
                  <p className="m-0 text-sm text-muted" dir="auto">
                    {isFa
                      ? `دسترسی ${name} به این فضای کاری قطع شود؟`
                      : `End ${name}'s access to this workspace?`}
                  </p>
                  {/* A refused removal keeps the dialog open, so the reason has
                      to be inside it: the page banner sits behind the backdrop. */}
                  {removeError && (
                    <ErrorState
                      error={removeError}
                      codes={MEMBER_ERROR_CODES}
                      fallback={isFa ? "حذف عضو ناموفق بود." : "Removing the member failed."}
                    />
                  )}
                </AlertDialog.Body>
                <AlertDialog.Footer>
                  <Button variant="ghost" isDisabled={pending === "remove"} onPress={() => setConfirmingRemove(false)}>
                    {isFa ? "انصراف" : "Cancel"}
                  </Button>
                  <Button variant="danger" isDisabled={pending === "remove"} onPress={() => void confirmRemove()}>
                    <Trash2 aria-hidden="true" size={14} />
                    {pending === "remove" ? (isFa ? "در حال حذف" : "Removing") : (isFa ? "حذف عضو" : "Remove member")}
                  </Button>
                </AlertDialog.Footer>
              </AlertDialog.Dialog>
            </AlertDialog.Container>
          </AlertDialog.Backdrop>
        </AlertDialog>
      )}
    </div>
  );
}

type SortColumn = "name" | "joined_at";

interface SortState {
  column: SortColumn;
  direction: "ascending" | "descending";
}

function sortMembers(members: WorkspaceMember[], sort: SortState, lang: "fa" | "en") {
  const sign = sort.direction === "descending" ? -1 : 1;
  const collator = new Intl.Collator(lang === "fa" ? "fa-IR" : "en");
  return [...members].sort((a, b) => {
    if (sort.column === "joined_at") return sign * a.joined_at.localeCompare(b.joined_at);
    return sign * collator.compare(memberName(a, lang === "fa"), memberName(b, lang === "fa"));
  });
}

function matchesQuery(member: WorkspaceMember, needle: string) {
  if (!needle) return true;
  return [member.display_name, member.email, member.phone_e164].some((value) =>
    value?.toLowerCase().includes(needle),
  );
}

function MemberDirectory({ actorRole, canManage }: { actorRole: WorkspaceRole; canManage: boolean }) {
  const { user } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const membersQuery = useMembers();
  const roleMutation = useChangeMemberRole();
  const removeMutation = useRemoveMember();
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const [sort, setSort] = useState<SortState>({ column: "name", direction: "ascending" });
  const headingRef = useRef<HTMLHeadingElement>(null);

  const members = useMemo(() => membersQuery.data?.items ?? [], [membersQuery.data]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sortMembers(
      members.filter((member) => (roleFilter === "all" || member.role === roleFilter) && matchesQuery(member, needle)),
      sort,
      lang,
    );
  }, [members, query, roleFilter, sort, lang]);

  if (membersQuery.isLoading) {
    return <LoadingState label={isFa ? "در حال دریافت اعضا" : "Loading members"} />;
  }
  if (membersQuery.isError) {
    return (
      <ErrorState
        error={membersQuery.error}
        codes={MEMBER_ERROR_CODES}
        fallback={isFa ? "دریافت اعضا ناموفق بود." : "Could not load members."}
      />
    );
  }

  const total = membersQuery.data?.total ?? members.length;
  const mutationError = roleMutation.error ?? removeMutation.error;
  const busy = roleMutation.isPending || removeMutation.isPending;

  async function applyRole(member: WorkspaceMember, role: AssignableWorkspaceRole) {
    removeMutation.reset();
    roleMutation.reset();
    try {
      await roleMutation.mutateAsync({ membershipId: member.id, role });
      toast.success(
        isFa
          ? `نقش ${memberName(member, isFa)} به «${roleLabel(role, lang)}» تغییر کرد.`
          : `${memberName(member, isFa)} is now ${roleLabel(role, lang)}.`,
      );
      return true;
    } catch {
      return false;
    }
  }

  async function remove(member: WorkspaceMember) {
    roleMutation.reset();
    removeMutation.reset();
    try {
      await removeMutation.mutateAsync(member.id);
      toast.success(
        isFa
          ? `${memberName(member, isFa)} از فضای کاری حذف شد.`
          : `${memberName(member, isFa)} was removed from the workspace.`,
      );
      headingRef.current?.focus();
      return true;
    } catch {
      return false;
    }
  }

  const tableLabel = isFa ? "اعضای فضای کاری" : "Workspace members";

  return (
    <Card<"section"> render={(props) => <section {...props} />} aria-labelledby="members-heading" className="min-w-0">
      <Card.Header className="flex-wrap gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <Users aria-hidden="true" className="shrink-0 text-accent" size={20} />
          <div className="min-w-0">
            <Card.Title<"h2"> render={(props) => <h2 {...props} />} id="members-heading" ref={headingRef} tabIndex={-1}>
              {tableLabel}
            </Card.Title>
            <Card.Description>
              {isFa ? `${formatNumber(total, lang)} عضو` : `${formatNumber(total, lang)} member${total === 1 ? "" : "s"}`}
            </Card.Description>
          </div>
        </div>
      </Card.Header>

      <Card.Content className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1 basis-56">
            <SearchField
              aria-label={isFa ? "جست‌وجو در اعضا" : "Search members"}
              value={query}
              onChange={setQuery}
              fullWidth
            >
              <SearchField.Group>
                <SearchField.SearchIcon />
                <SearchField.Input placeholder={isFa ? "نام یا ایمیل" : "Name or email"} />
                <SearchField.ClearButton aria-label={isFa ? "پاک کردن جست‌وجو" : "Clear search"} />
              </SearchField.Group>
            </SearchField>
          </div>
          <div className="w-40">
            <OptionSelect
              label={isFa ? "پالایش بر پایهٔ نقش" : "Filter by role"}
              value={roleFilter}
              onChange={setRoleFilter}
              options={[
                { id: "all", text: isFa ? "همهٔ نقش‌ها" : "All roles" },
                ...FILTER_ROLES.map((role) => ({ id: role, text: roleLabel(role, lang) })),
              ]}
            />
          </div>
        </div>

        {mutationError && (
          <ErrorState
            error={mutationError}
            codes={MEMBER_ERROR_CODES}
            fallback={isFa ? "به‌روزرسانی عضو ناموفق بود." : "Member update failed."}
          />
        )}

        <Table className="min-w-0">
          {/* The table scrolls sideways on a narrow screen. A keyboard user has
              to be able to scroll it too, so the region takes focus (QA M-02, M-05). */}
          <Table.ScrollContainer
            role="region"
            tabIndex={0}
            aria-label={isFa ? "فهرست اعضا، با پیمایش افقی" : "Member list, scrolls horizontally"}
            className="platform-members-table-scroll"
          >
            <Table.Content
              aria-label={tableLabel}
              sortDescriptor={sort}
              onSortChange={(descriptor) =>
                setSort({
                  column: descriptor.column === "joined_at" ? "joined_at" : "name",
                  direction: descriptor.direction,
                })
              }
            >
              <Table.Header>
                <Table.Column id="name" isRowHeader allowsSorting>
                  {({ sortDirection }) => (
                    <Table.SortableColumnHeader sortDirection={sortDirection}>
                      {isFa ? "عضو" : "Member"}
                    </Table.SortableColumnHeader>
                  )}
                </Table.Column>
                <Table.Column id="role">{isFa ? "نقش" : "Role"}</Table.Column>
                <Table.Column id="joined_at" allowsSorting>
                  {({ sortDirection }) => (
                    <Table.SortableColumnHeader sortDirection={sortDirection}>
                      {isFa ? "تاریخ عضویت" : "Joined"}
                    </Table.SortableColumnHeader>
                  )}
                </Table.Column>
                <Table.Column id="actions">{isFa ? "مدیریت" : "Management"}</Table.Column>
              </Table.Header>
              <Table.Body
                items={visible}
                // React Aria caches a row's cells per item, so anything a cell
                // reads from outside the item has to be listed here. Without
                // this a refused removal never reaches the row's dialog.
                dependencies={[removeMutation.error, busy, actorRole, canManage, user?.id, lang]}
                renderEmptyState={() => (
                  <EmptyState
                    label={
                      members.length === 0
                        ? isFa
                          ? "عضوی در این فضای کاری نیست"
                          : "No workspace members"
                        : isFa
                          ? "عضوی با این جست‌وجو پیدا نشد"
                          : "No member matches this search"
                    }
                  />
                )}
              >
                {(member: WorkspaceMember) => {
                  const isSelf = member.user_id === user?.id;
                  return (
                    <Table.Row id={member.id}>
                      <Table.Cell>
                        <div className="flex min-w-0 items-center gap-3">
                          <Avatar size="sm" color="accent" variant="soft">
                            <Avatar.Fallback>{memberName(member, isFa).slice(0, 1).toUpperCase()}</Avatar.Fallback>
                          </Avatar>
                          <span className="min-w-0">
                            <span className="block truncate font-semibold text-foreground" dir="auto">
                              {member.display_name || memberContact(member, isFa)}
                              {isSelf && (
                                <span className="ms-1 font-normal text-muted">{isFa ? "(شما)" : "(you)"}</span>
                              )}
                            </span>
                            <span className="mt-0.5 block truncate text-xs text-muted" dir="ltr">
                              {memberContact(member, isFa)}
                            </span>
                          </span>
                        </div>
                      </Table.Cell>
                      <Table.Cell>
                        <StatusBadge status={member.role} label={roleLabel(member.role, lang)} />
                      </Table.Cell>
                      <Table.Cell className="text-xs text-muted">{formatDate(member.joined_at, lang)}</Table.Cell>
                      <Table.Cell>
                        <MemberRowActions
                          member={member}
                          actorRole={actorRole}
                          isSelf={isSelf}
                          manageable={canManage && canManageTarget(actorRole, user?.id, member)}
                          busy={busy}
                          removeError={removeMutation.error}
                          isFa={isFa}
                          lang={lang}
                          onApplyRole={applyRole}
                          onRemove={remove}
                          onActionStarted={() => {
                            roleMutation.reset();
                            removeMutation.reset();
                          }}
                        />
                      </Table.Cell>
                    </Table.Row>
                  );
                }}
              </Table.Body>
            </Table.Content>
          </Table.ScrollContainer>
        </Table>
      </Card.Content>
    </Card>
  );
}

/** The "Invite member" button and the modal it opens. */
function InviteMember({ actorRole }: { actorRole: WorkspaceRole }) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const inviteMutation = useInviteMember();
  const [isOpen, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<AssignableWorkspaceRole>("member");
  const [days, setDays] = useState<number>(DEFAULT_EXPIRY_DAYS);
  const pending = inviteMutation.isPending;
  const daysValid = Number.isInteger(days) && days >= MIN_EXPIRY_DAYS && days <= MAX_EXPIRY_DAYS;

  // Every close goes through here, so a failed invitation never greets the user
  // again the next time the modal opens.
  function changeOpen(open: boolean) {
    if (pending) return;
    if (!open) inviteMutation.reset();
    setOpen(open);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !email.trim() || !daysValid) return;
    inviteMutation.reset();
    // A failed delivery keeps the typed address for another attempt; only a
    // delivered invitation clears the form and closes the modal.
    inviteMutation.mutate(
      { email: email.trim(), role, expires_in_days: days },
      {
        onSuccess: () => {
          setEmail("");
          setOpen(false);
          toast.success(isFa ? "دعوت‌نامه ارسال شد." : "Invitation sent.");
        },
      },
    );
  }

  return (
    <Modal isOpen={isOpen} onOpenChange={changeOpen}>
      <Button size="sm" variant="primary" className="ms-auto">
        <Mail aria-hidden="true" size={15} />
        {isFa ? "دعوت عضو" : "Invite member"}
      </Button>
      <Modal.Backdrop>
        <Modal.Container>
          <Modal.Dialog>
            <form onSubmit={submit} noValidate>
              <Modal.Header>
                <Modal.Heading>{isFa ? "دعوت عضو تازه" : "Invite a new member"}</Modal.Heading>
              </Modal.Header>
              <Modal.Body className="space-y-4">
                <p className="m-0 text-xs text-muted">
                  {isFa ? "دعوت‌نامه از طریق ایمیل ارسال می‌شود" : "Invitations are delivered by email"}
                </p>

                <TextField
                  value={email}
                  onChange={setEmail}
                  type="email"
                  autoComplete="email"
                  isRequired
                  isReadOnly={pending}
                  fullWidth
                >
                  <Label>{isFa ? "ایمیل" : "Email address"}</Label>
                  <Input dir="ltr" placeholder="name@company.com" />
                </TextField>

                <div className="flex flex-col gap-1.5">
                  <OptionSelect
                    label={isFa ? "نقش" : "Role"}
                    showLabel
                    value={role}
                    isDisabled={pending}
                    describedBy={role === "admin" ? "invitation-role-note" : undefined}
                    options={assignableRoles(actorRole).map((candidate) => ({
                      id: candidate,
                      text: roleLabel(candidate, lang),
                    }))}
                    onChange={(next) => setRole(next as AssignableWorkspaceRole)}
                  />
                  {role === "admin" && (
                    <p id="invitation-role-note" className="m-0 text-xs text-muted">
                      {ADMIN_CONSEQUENCE[lang]}
                    </p>
                  )}
                </div>

                <NumberField
                  value={days}
                  onChange={setDays}
                  minValue={MIN_EXPIRY_DAYS}
                  maxValue={MAX_EXPIRY_DAYS}
                  step={1}
                  isReadOnly={pending}
                  isInvalid={!daysValid}
                  fullWidth
                >
                  <Label>{isFa ? "اعتبار (روز)" : "Expiry (days)"}</Label>
                  <NumberField.Group>
                    <NumberField.DecrementButton />
                    <NumberField.Input />
                    <NumberField.IncrementButton />
                  </NumberField.Group>
                  <Description>
                    {isFa ? "یک عدد صحیح بین ۱ تا ۳۰ وارد کنید." : "Enter a whole number from 1 to 30."}
                  </Description>
                </NumberField>

                {inviteMutation.error && (
                  <ErrorState
                    error={inviteMutation.error}
                    codes={MEMBER_ERROR_CODES}
                    fallback={isFa ? "عملیات دعوت ناموفق بود." : "The invitation action failed."}
                  />
                )}
              </Modal.Body>
              <Modal.Footer>
                <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                  {isFa ? "انصراف" : "Cancel"}
                </Button>
                <Button type="submit" variant="primary" isDisabled={pending || !email.trim() || !daysValid}>
                  <UserPlus aria-hidden="true" size={15} />
                  {pending ? (isFa ? "در حال ارسال" : "Sending") : (isFa ? "ارسال دعوت" : "Send invite")}
                </Button>
              </Modal.Footer>
            </form>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}

function InvitationRow({
  invitation,
  isFa,
  lang,
  isRevoking,
  onRevoke,
}: {
  invitation: WorkspaceInvitation;
  isFa: boolean;
  lang: "fa" | "en";
  isRevoking: boolean;
  onRevoke: () => void;
}) {
  const dateLine =
    invitation.status === "accepted" && invitation.accepted_at
      ? `${isFa ? "پذیرفته‌شده در:" : "Accepted:"} ${formatDate(invitation.accepted_at, lang)}`
      : invitation.status === "revoked" && invitation.revoked_at
        ? `${isFa ? "لغوشده در:" : "Revoked:"} ${formatDate(invitation.revoked_at, lang)}`
        : `${isFa ? "انقضا:" : "Expires:"} ${formatDate(invitation.expires_at, lang)}`;
  return (
    <li className="flex flex-col gap-3 border-b border-separator px-1 py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-semibold text-foreground" dir="ltr">
            {invitation.email}
          </span>
          <StatusBadge status={invitation.status} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
          <span className="inline-flex items-center gap-1.5">
            <ShieldCheck aria-hidden="true" size={13} />
            {roleLabel(invitation.role, lang)}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <CalendarClock aria-hidden="true" size={13} />
            {dateLine}
          </span>
        </div>
      </div>
      {invitation.status === "pending" && (
        <Button
          size="sm"
          variant="ghost"
          className="shrink-0 self-start text-danger sm:self-auto"
          isDisabled={isRevoking}
          aria-label={isFa ? `لغو دعوت ${invitation.email}` : `Revoke the invitation for ${invitation.email}`}
          onPress={onRevoke}
        >
          <Trash2 aria-hidden="true" size={14} />
          {isRevoking ? (isFa ? "در حال لغو" : "Revoking") : (isFa ? "لغو دعوت" : "Revoke")}
        </Button>
      )}
    </li>
  );
}

function InvitationManagement({ actorRole }: { actorRole: WorkspaceRole }) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const invitationsQuery = useInvitations();
  const revokeMutation = useRevokeInvitation();
  const [tab, setTab] = useState<InvitationStatus>("pending");
  const headingRef = useRef<HTMLHeadingElement>(null);

  const invitations = invitationsQuery.data?.items ?? [];
  const byStatus = (status: InvitationStatus) => invitations.filter((item) => item.status === status);

  let body: ReactNode;
  if (invitationsQuery.isLoading) {
    body = <LoadingState label={isFa ? "در حال دریافت دعوت‌ها" : "Loading invitations"} />;
  } else if (invitationsQuery.isError) {
    body = (
      <ErrorState
        error={invitationsQuery.error}
        codes={MEMBER_ERROR_CODES}
        fallback={isFa ? "دریافت دعوت‌ها ناموفق بود." : "Could not load invitations."}
      />
    );
  } else {
    body = (
      <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as InvitationStatus)}>
        <Tabs.List aria-label={isFa ? "وضعیت دعوت‌ها" : "Invitation states"}>
          {INVITATION_TABS.map((status) => (
            <Tabs.Tab key={status} id={status}>
              {`${statusLabel(status, lang)} (${formatNumber(byStatus(status).length, lang)})`}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {INVITATION_TABS.map((status) => {
          const items = byStatus(status);
          return (
            <Tabs.Panel key={status} id={status}>
              {items.length === 0 ? (
                <EmptyState label={isFa ? "دعوتی در این وضعیت نیست" : "No invitation in this state"} />
              ) : (
                <ul className="m-0 list-none p-0">
                  {items.map((invitation) => (
                    <InvitationRow
                      key={invitation.id}
                      invitation={invitation}
                      isFa={isFa}
                      lang={lang}
                      isRevoking={revokeMutation.isPending && revokeMutation.variables === invitation.id}
                      onRevoke={() => {
                        revokeMutation.reset();
                        revokeMutation.mutate(invitation.id, {
                          onSuccess: () => {
                            headingRef.current?.focus();
                            toast.success(isFa ? "دعوت لغو شد." : "The invitation was revoked.");
                          },
                        });
                      }}
                    />
                  ))}
                </ul>
              )}
            </Tabs.Panel>
          );
        })}
      </Tabs>
    );
  }

  return (
    <Card<"section">
      render={(props) => <section {...props} />}
      aria-labelledby="invitations-heading"
      className="min-w-0"
    >
      <Card.Header className="flex-wrap gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <UserPlus aria-hidden="true" className="shrink-0 text-accent" size={20} />
          <div className="min-w-0">
            <Card.Title<"h2">
              render={(props) => <h2 {...props} />}
              id="invitations-heading"
              ref={headingRef}
              tabIndex={-1}
            >
              {isFa ? "دعوت‌ها" : "Invitations"}
            </Card.Title>
            <Card.Description>
              {isFa ? "دعوت‌نامه از طریق ایمیل ارسال می‌شود" : "Invitations are delivered by email"}
            </Card.Description>
          </div>
        </div>
        <InviteMember actorRole={actorRole} />
      </Card.Header>

      <Card.Content className="min-w-0">
        {revokeMutation.error && (
          <div className="mb-4">
            <ErrorState
              error={revokeMutation.error}
              codes={MEMBER_ERROR_CODES}
              fallback={isFa ? "عملیات دعوت ناموفق بود." : "The invitation action failed."}
            />
          </div>
        )}
        {body}
      </Card.Content>
    </Card>
  );
}

export default function MembersPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const can = useCan();
  const role = can.role;
  // Invite, change role, remove and revoke need member:manage; the per-row
  // owner/admin rules in canManageTarget still apply on top.
  const canManage = can.has("member:manage");

  return (
    <main className="platform-page space-y-5">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={isFa ? "اعضا و دسترسی‌ها" : "Members and access"}
        actions={
          role ? (
            <span className={`platform-status platform-status-${role}`}>
              <ShieldCheck aria-hidden="true" size={13} />
              {roleLabel(role, lang)}
            </span>
          ) : undefined
        }
      />

      {!role ? (
        <ErrorState
          error={null}
          fallback={isFa ? "فضای کاری فعالی انتخاب نشده است." : "No active workspace is selected."}
        />
      ) : (
        <div className="grid min-w-0 gap-5">
          <MemberDirectory actorRole={role} canManage={canManage} />
          {canManage && <InvitationManagement actorRole={role} />}
        </div>
      )}
    </main>
  );
}
