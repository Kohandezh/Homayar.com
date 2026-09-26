import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  CalendarClock,
  Mail,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { formatDate, formatNumber, parseWholeNumber } from "@/lib/format";
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
import type {
  AssignableWorkspaceRole,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRole,
} from "@/types/platform";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";

const ALL_ASSIGNABLE_ROLES: AssignableWorkspaceRole[] = ["admin", "member", "viewer"];
const ADMIN_ASSIGNABLE_ROLES: AssignableWorkspaceRole[] = ["member", "viewer"];

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

interface RowProps {
  member: WorkspaceMember;
  actorRole: WorkspaceRole;
  isSelf: boolean;
  manageable: boolean;
  busy: boolean;
  isFa: boolean;
  lang: "fa" | "en";
  onApplyRole: (member: WorkspaceMember, role: AssignableWorkspaceRole) => Promise<boolean>;
  onRemove: (member: WorkspaceMember) => Promise<boolean>;
  onRemoveStarted: () => void;
}

function MemberRow({
  member,
  actorRole,
  isSelf,
  manageable,
  busy,
  isFa,
  lang,
  onApplyRole,
  onRemove,
  onRemoveStarted,
}: RowProps) {
  const name = memberName(member, isFa);
  const contact = member.email ?? member.phone_e164 ?? (isFa ? "بدون اطلاعات تماس" : "No contact information");
  const assignableRoles = actorRole === "owner" ? ALL_ASSIGNABLE_ROLES : ADMIN_ASSIGNABLE_ROLES;
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
  const selectRef = useRef<HTMLSelectElement>(null);
  const trashRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Where focus goes after the next render: controls that unmount (the
  // trash button, Apply) must not drop focus to the page (QA M-04).
  const focusAfterRender = useRef<"select" | "trash" | "cancel" | null>(null);
  const setFocusTarget = (target: "select" | "trash" | "cancel") => {
    focusAfterRender.current = target;
  };

  useEffect(() => {
    const target = focusAfterRender.current;
    if (!target) return;
    focusAfterRender.current = null;
    ({ select: selectRef, trash: trashRef, cancel: cancelRef })[target].current?.focus();
  });

  const roleDirty = draftRole !== member.role;

  async function applyRole() {
    if (pending || !roleDirty || draftRole === "owner") return;
    setPending("role");
    const applied = await onApplyRole(member, draftRole);
    setPending(null);
    // A refused change never shows the attempted role as current.
    if (!applied) setDraftRole(member.role);
    setFocusTarget("select");
  }

  async function confirmRemove() {
    if (pending) return;
    setPending("remove");
    const removed = await onRemove(member);
    setPending(null);
    if (!removed) setFocusTarget("cancel");
  }

  return (
    <tr className="border-b border-border-subtle last:border-b-0">
      <th scope="row" className="px-4 py-3 text-start font-normal sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-bg-elevated text-xs font-bold text-accent" aria-hidden="true">
            {name.slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="block truncate font-semibold text-text-primary" dir="auto">
              {member.display_name || contact}
              {isSelf && (
                <span className="ms-1 font-normal text-text-muted">
                  {isFa ? "(شما)" : "(you)"}
                </span>
              )}
            </span>
            <span className="mt-0.5 block truncate text-xs text-text-secondary" dir="ltr">
              {contact}
            </span>
          </span>
        </div>
      </th>
      <td className="px-4 py-3">
        <span className={`platform-status platform-status-${member.role}`}>
          {roleLabel(member.role, lang)}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-text-secondary">
        {formatDate(member.joined_at, lang)}
      </td>
      <td className="px-4 py-3 sm:px-5">
        {manageable ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {confirmingRemove ? (
              <div className="flex flex-wrap items-center gap-2" role="group" aria-label={isFa ? `حذف ${name}` : `Remove ${name}`}>
                <span className="text-xs text-error" dir="auto">
                  {isFa ? `دسترسی ${name} به این فضای کاری قطع شود؟` : `End ${name}'s access to this workspace?`}
                </span>
                <button
                  ref={cancelRef}
                  type="button"
                  className="platform-button"
                  aria-disabled={pending === "remove"}
                  onClick={() => {
                    if (pending) return;
                    setConfirmingRemove(false);
                    setFocusTarget("trash");
                  }}
                >
                  {isFa ? "انصراف" : "Cancel"}
                </button>
                <button
                  type="button"
                  className="platform-button platform-button-danger"
                  aria-disabled={pending === "remove"}
                  onClick={() => void confirmRemove()}
                >
                  <Trash2 aria-hidden="true" size={14} />
                  {pending === "remove" ? (isFa ? "در حال حذف" : "Removing") : (isFa ? "حذف عضو" : "Remove member")}
                </button>
              </div>
            ) : (
              <>
                <label className="sr-only" htmlFor={`member-role-${member.id}`}>
                  {isFa ? `نقش ${name}` : `Role for ${name}`}
                </label>
                <select
                  ref={selectRef}
                  id={`member-role-${member.id}`}
                  className="platform-select w-32"
                  value={draftRole}
                  aria-disabled={pending === "role"}
                  aria-describedby={roleDirty ? `member-role-note-${member.id}` : undefined}
                  onChange={(event) => {
                    if (pending) return;
                    setDraftRole(event.currentTarget.value as AssignableWorkspaceRole);
                  }}
                >
                  {assignableRoles.map((role) => (
                    <option key={role} value={role}>
                      {roleLabel(role, lang)}
                    </option>
                  ))}
                </select>
                {roleDirty ? (
                  <>
                    <button
                      type="button"
                      className="platform-button platform-button-primary"
                      aria-disabled={pending === "role"}
                      onClick={() => void applyRole()}
                    >
                      {pending === "role" ? (isFa ? "در حال اعمال" : "Applying") : (isFa ? "اعمال" : "Apply")}
                    </button>
                    <button
                      type="button"
                      className="platform-button"
                      aria-disabled={pending === "role"}
                      onClick={() => {
                        if (pending) return;
                        setDraftRole(member.role);
                        setFocusTarget("select");
                      }}
                    >
                      {isFa ? "انصراف" : "Cancel"}
                    </button>
                    <span id={`member-role-note-${member.id}`} className="basis-full text-end text-xs text-text-secondary">
                      {isFa
                        ? `نقش ${name} پس از «اعمال» به «${roleLabel(draftRole, lang)}» تغییر می‌کند.`
                        : `${name}'s role changes to ${roleLabel(draftRole, lang)} only after Apply.`}
                      {draftRole === "admin" && ` ${ADMIN_CONSEQUENCE[lang]}`}
                    </span>
                  </>
                ) : (
                  <button
                    ref={trashRef}
                    type="button"
                    className="platform-icon-button platform-button-danger"
                    aria-disabled={busy}
                    onClick={() => {
                      if (busy) return;
                      onRemoveStarted();
                      setConfirmingRemove(true);
                      setFocusTarget("cancel");
                    }}
                    aria-label={isFa ? `حذف ${name}` : `Remove ${name}`}
                    title={isFa ? "حذف عضو" : "Remove member"}
                  >
                    <Trash2 aria-hidden="true" size={15} />
                  </button>
                )}
              </>
            )}
          </div>
        ) : (
          <div className="flex justify-end text-xs text-text-muted">
            {isSelf ? (isFa ? "حساب شما" : "Your account") : (isFa ? "فقط خواندنی" : "Read only")}
          </div>
        )}
      </td>
    </tr>
  );
}

function MemberDirectory({ actorRole }: { actorRole: WorkspaceRole }) {
  const { user } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const membersQuery = useMembers();
  const roleMutation = useChangeMemberRole();
  const removeMutation = useRemoveMember();
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);

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

  const members = membersQuery.data?.items ?? [];
  const total = membersQuery.data?.total ?? members.length;
  const mutationError = roleMutation.error ?? removeMutation.error;
  const busy = roleMutation.isPending || removeMutation.isPending;

  async function applyRole(member: WorkspaceMember, role: AssignableWorkspaceRole) {
    removeMutation.reset();
    roleMutation.reset();
    setAnnouncement("");
    try {
      await roleMutation.mutateAsync({ membershipId: member.id, role });
      setAnnouncement(
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
    setAnnouncement("");
    try {
      await removeMutation.mutateAsync(member.id);
      setAnnouncement(
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

  return (
    <section className="platform-members-directory platform-surface overflow-hidden" aria-labelledby="members-heading">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle px-4 py-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <Users aria-hidden="true" className="shrink-0 text-accent" size={20} />
          <div className="min-w-0">
            <h2 id="members-heading" ref={headingRef} tabIndex={-1} className="m-0 text-sm font-semibold text-text-primary">
              {isFa ? "اعضای فضای کاری" : "Workspace members"}
            </h2>
            <p className="mt-1 mb-0 text-xs text-text-secondary">
              {isFa ? `${formatNumber(total, lang)} عضو` : `${formatNumber(total, lang)} member${total === 1 ? "" : "s"}`}
            </p>
          </div>
        </div>
      </div>

      <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
      {announcement && (
        <p className="m-0 border-b border-border-subtle px-4 py-3 text-xs text-success sm:px-5" aria-hidden="true">
          {announcement}
        </p>
      )}

      {mutationError && (
        <div className="p-4 pb-0">
          <ErrorState
            error={mutationError}
            codes={MEMBER_ERROR_CODES}
            fallback={isFa ? "به‌روزرسانی عضو ناموفق بود." : "Member update failed."}
          />
        </div>
      )}

      {members.length === 0 ? (
        <div className="p-4">
          <EmptyState label={isFa ? "عضوی در این فضای کاری نیست" : "No workspace members"} />
        </div>
      ) : (
        // A positioned, keyboard-reachable scroll container: the visually
        // hidden labels inside it cannot widen the page (QA M-02) and the
        // off-screen columns are reachable by keyboard (QA M-05).
        <div
          className="platform-members-table-scroll relative overflow-x-auto"
          tabIndex={0}
          role="region"
          aria-label={isFa ? "فهرست اعضا، قابل پیمایش افقی" : "Member list, scrolls horizontally"}
        >
          <table className="platform-members-table w-full min-w-[720px] border-collapse text-sm">
            <caption className="sr-only">
              {isFa ? "فهرست اعضا و نقش‌های فضای کاری" : "Workspace members and roles"}
            </caption>
            <thead>
              <tr className="border-b border-border-subtle bg-bg-primary text-xs text-text-secondary">
                <th scope="col" className="px-4 py-3 text-start font-semibold sm:px-5">
                  {isFa ? "عضو" : "Member"}
                </th>
                <th scope="col" className="px-4 py-3 text-start font-semibold">
                  {isFa ? "نقش" : "Role"}
                </th>
                <th scope="col" className="px-4 py-3 text-start font-semibold">
                  {isFa ? "تاریخ عضویت" : "Joined"}
                </th>
                <th scope="col" className="px-4 py-3 text-end font-semibold sm:px-5">
                  {isFa ? "مدیریت" : "Management"}
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <MemberRow
                  key={member.id}
                  member={member}
                  actorRole={actorRole}
                  isSelf={member.user_id === user?.id}
                  manageable={canManageTarget(actorRole, user?.id, member)}
                  busy={busy}
                  isFa={isFa}
                  lang={lang}
                  onApplyRole={applyRole}
                  onRemove={remove}
                  onRemoveStarted={() => {
                    roleMutation.reset();
                    removeMutation.reset();
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
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
    <li className="platform-invitation-row flex flex-col gap-3 border-b border-border-subtle px-4 py-4 last:border-b-0 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-semibold text-text-primary" dir="ltr">
            {invitation.email}
          </span>
          <StatusBadge status={invitation.status} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-secondary">
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
        <button
          type="button"
          className="platform-button platform-button-danger shrink-0 self-start sm:self-auto"
          onClick={() => {
            if (!isRevoking) onRevoke();
          }}
          aria-disabled={isRevoking}
        >
          <Trash2 aria-hidden="true" size={14} />
          {isRevoking ? (isFa ? "در حال لغو" : "Revoking") : (isFa ? "لغو دعوت" : "Revoke")}
        </button>
      )}
    </li>
  );
}

function InvitationManagement({ actorRole }: { actorRole: "owner" | "admin" }) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const invitationsQuery = useInvitations();
  const inviteMutation = useInviteMember();
  const revokeMutation = useRevokeInvitation();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<AssignableWorkspaceRole>("member");
  const [expiresInDays, setExpiresInDays] = useState("7");
  const [daysTouched, setDaysTouched] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const roles = actorRole === "owner" ? ALL_ASSIGNABLE_ROLES : ADMIN_ASSIGNABLE_ROLES;
  const days = parseWholeNumber(expiresInDays);
  const daysValid = days !== null && days >= 1 && days <= 30;
  const pending = inviteMutation.isPending;

  function submitInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setDaysTouched(true);
    if (pending || !email.trim() || !daysValid || days === null) return;
    inviteMutation.reset();
    revokeMutation.reset();
    // A failed delivery keeps the typed address for another attempt; only a
    // delivered invitation clears the form.
    inviteMutation.mutate(
      { email: email.trim(), role, expires_in_days: days },
      { onSuccess: () => setEmail("") },
    );
  }

  return (
    <section className="platform-invitations platform-surface overflow-hidden" aria-labelledby="invitations-heading">
      <div className="border-b border-border-subtle px-4 py-4 sm:px-5">
        <div className="flex items-center gap-3">
          <UserPlus aria-hidden="true" className="text-accent" size={20} />
          <div>
            <h2 id="invitations-heading" ref={headingRef} tabIndex={-1} className="m-0 text-sm font-semibold text-text-primary">
              {isFa ? "دعوت‌ها" : "Invitations"}
            </h2>
            <p className="mt-1 mb-0 text-xs text-text-secondary">
              {isFa ? "دعوت‌نامه از طریق ایمیل ارسال می‌شود" : "Invitations are delivered by email"}
            </p>
          </div>
        </div>
      </div>

      <form className="platform-invite-form grid gap-4 border-b border-border-subtle bg-bg-primary p-4 sm:grid-cols-2 sm:p-5" onSubmit={submitInvitation} noValidate>
        <div className="platform-field sm:col-span-2">
          <label htmlFor="invitation-email">{isFa ? "ایمیل" : "Email address"}</label>
          <div className="relative">
            <Mail aria-hidden="true" className="pointer-events-none absolute start-3 top-1/2 -translate-y-1/2 text-text-muted" size={15} />
            <input
              id="invitation-email"
              className="platform-input ps-9"
              type="email"
              dir="ltr"
              autoComplete="email"
              value={email}
              onChange={(event) => {
                setEmail(event.currentTarget.value);
                if (inviteMutation.isSuccess) inviteMutation.reset();
              }}
              placeholder="name@company.com"
              required
              aria-disabled={pending}
              readOnly={pending}
            />
          </div>
        </div>
        <div className="platform-field">
          <label htmlFor="invitation-role">{isFa ? "نقش" : "Role"}</label>
          <select
            id="invitation-role"
            className="platform-select"
            value={role}
            onChange={(event) => {
              if (!pending) setRole(event.currentTarget.value as AssignableWorkspaceRole);
            }}
            aria-disabled={pending}
            aria-describedby={role === "admin" ? "invitation-role-note" : undefined}
          >
            {roles.map((candidate) => (
              <option key={candidate} value={candidate}>
                {roleLabel(candidate, lang)}
              </option>
            ))}
          </select>
          {role === "admin" && (
            <p id="invitation-role-note" className="m-0 text-xs text-text-muted">{ADMIN_CONSEQUENCE[lang]}</p>
          )}
        </div>
        <div className="platform-field">
          <label htmlFor="invitation-expiry">{isFa ? "اعتبار (روز)" : "Expiry (days)"}</label>
          <input
            id="invitation-expiry"
            className="platform-input"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            value={expiresInDays}
            onChange={(event) => setExpiresInDays(event.currentTarget.value)}
            onBlur={() => setDaysTouched(true)}
            aria-invalid={daysTouched && !daysValid}
            aria-describedby="invitation-expiry-help"
            required
            readOnly={pending}
          />
          <p id="invitation-expiry-help" className={daysTouched && !daysValid ? "platform-form-error" : "m-0 text-xs text-text-muted"}>
            {isFa ? "یک عدد صحیح بین ۱ تا ۳۰ وارد کنید." : "Enter a whole number from 1 to 30."}
          </p>
        </div>
        <button
          type="submit"
          className="platform-button platform-button-primary self-end sm:col-span-2"
          aria-disabled={pending || !email.trim() || !daysValid}
        >
          <UserPlus aria-hidden="true" size={15} />
          {pending ? (isFa ? "در حال ارسال" : "Sending") : (isFa ? "ارسال دعوت" : "Send invite")}
        </button>
      </form>

      {(inviteMutation.error || revokeMutation.error) && (
        <div className="p-4 pb-0">
          <ErrorState
            error={inviteMutation.error ?? revokeMutation.error}
            codes={MEMBER_ERROR_CODES}
            fallback={isFa ? "عملیات دعوت ناموفق بود." : "The invitation action failed."}
          />
        </div>
      )}
      {inviteMutation.isSuccess && (
        <p className="m-0 border-b border-border-subtle px-4 py-3 text-xs text-success" role="status">
          {isFa ? "دعوت‌نامه ارسال شد." : "Invitation sent."}
        </p>
      )}

      {invitationsQuery.isLoading ? (
        <LoadingState label={isFa ? "در حال دریافت دعوت‌ها" : "Loading invitations"} />
      ) : invitationsQuery.isError ? (
        <div className="p-4">
          <ErrorState
            error={invitationsQuery.error}
            codes={MEMBER_ERROR_CODES}
            fallback={isFa ? "دریافت دعوت‌ها ناموفق بود." : "Could not load invitations."}
          />
        </div>
      ) : invitationsQuery.data?.items.length ? (
        <ul className="m-0 list-none p-0">
          {invitationsQuery.data.items.map((invitation) => (
            <InvitationRow
              key={invitation.id}
              invitation={invitation}
              isFa={isFa}
              lang={lang}
              isRevoking={revokeMutation.isPending && revokeMutation.variables === invitation.id}
              onRevoke={() => {
                inviteMutation.reset();
                revokeMutation.reset();
                revokeMutation.mutate(invitation.id, { onSuccess: () => headingRef.current?.focus() });
              }}
            />
          ))}
        </ul>
      ) : (
        <div className="p-4">
          <EmptyState label={isFa ? "دعوتی ثبت نشده است" : "No invitations yet"} />
        </div>
      )}
    </section>
  );
}

export default function MembersPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const role = workspace?.role;
  const canManage = role === "owner" || role === "admin";

  return (
    <main className="platform-page platform-members-page space-y-5">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={isFa ? "اعضا و دسترسی‌ها" : "Members and access"}
        actions={role ? (
          <span className={`platform-status platform-status-${role}`}>
            <ShieldCheck aria-hidden="true" size={13} />
            {roleLabel(role, lang)}
          </span>
        ) : undefined}
      />

      {!role ? (
        <ErrorState
          error={null}
          fallback={isFa ? "فضای کاری فعالی انتخاب نشده است." : "No active workspace is selected."}
        />
      ) : (
        <div className={`platform-members-grid grid gap-5 ${canManage ? "2xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]" : "grid-cols-1"}`}>
          <MemberDirectory actorRole={role} />
          {canManage && <InvitationManagement actorRole={role} />}
        </div>
      )}
    </main>
  );
}
