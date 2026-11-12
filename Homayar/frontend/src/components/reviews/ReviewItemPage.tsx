import { useMemo, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router";
import type { Key } from "react";
import { Alert, AlertDialog, Button, Card, Chip, Label, ListBox, Select, TextField, Input, toast, linkVariants } from "@heroui/react";
import { ArrowLeft } from "lucide-react";

import { ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { useAuth } from "@/lib/auth-context";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMembers, useTeams } from "@/lib/platform-api";
import {
  nextReviewItem,
  useAssignReviewItem,
  useCancelReviewItem,
  useChangeReviewDueTime,
  useCompleteReviewItem,
  useReviewCriteria,
  useReviewItem,
  useStartReviewItem,
} from "@/lib/review-api";
import { useCan } from "@/lib/use-can";
import CriteriaPanel from "@/components/reviews/CriteriaPanel";
import { REVIEW_ERROR_CODES, REVIEWS_COPY } from "@/components/reviews/reviews-copy";
import { isDueAtInRange, isReviewItemNotFound, isRevisionConflict, toLocalDateTimeInput } from "@/components/reviews/review-model";
import type { ReviewCancelReason } from "@/types/reviews";

/** The workspace roles that may be assigned a review (ADR 0051 D4: no viewers). */
const ASSIGNABLE_ROLES = new Set(["owner", "admin", "member"]);

const CANCEL_REASONS: ReviewCancelReason[] = ["duplicate", "out_of_scope", "meeting_unusable", "other"];

/**
 * The route piece: react-router reuses one component instance between
 * `/reviews/{a}` and `/reviews/{b}`, but every piece of this page's state
 * belongs to one item, so the key remounts the page per item id.
 */
export default function ReviewItemRoute() {
  const { itemId } = useParams();
  if (!itemId) return null;
  return <ReviewItemPage key={itemId} itemId={itemId} />;
}

/**
 * The one review item page (`/reviews/:itemId`, HOM-265 AC3 and AC5). All
 * state here is per item; the wrapper above remounts it when the id changes.
 */
function ReviewItemPage({ itemId }: { itemId: string }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { lang } = useI18n();
  const copy = REVIEWS_COPY[lang];
  const can = useCan();
  const canManage = can.has("review:manage");

  const itemQuery = useReviewItem(itemId);
  // The criteria view is asked for only once the item itself is there: an
  // invisible item answers 404, and its criteria read would answer the same.
  const criteriaQuery = useReviewCriteria(itemId, itemQuery.isSuccess);
  const item = itemQuery.data ?? null;

  const membersQuery = useMembers();
  const teamsQuery = useTeams();
  const memberNames = useMemo(
    () => new Map((membersQuery.data?.items ?? []).map((member) => [member.user_id, member.display_name])),
    [membersQuery.data],
  );
  const teamNames = useMemo(
    () => new Map((teamsQuery.data?.items ?? []).map((team) => [team.id, team.name])),
    [teamsQuery.data],
  );

  const assignMutation = useAssignReviewItem();
  const dueMutation = useChangeReviewDueTime();
  const startMutation = useStartReviewItem();
  const completeMutation = useCompleteReviewItem();
  const cancelMutation = useCancelReviewItem();

  // One place for a refused write: a conflict reloads the item, anything
  // else shows its own mapped text. The console never retries by itself.
  const [conflict, setConflict] = useState(false);
  const [writeError, setWriteError] = useState<unknown>(null);

  /**
   * The form follows the item: every new revision (a successful write, or
   * the conflict reload of someone else's write) puts the fresh values back
   * into the fields. Derived during render, the React pattern for a reset.
   */
  interface ItemFormState {
    revision: number;
    assigneeChoice: string | null;
    dueValue: string;
    dueInvalid: boolean;
  }
  const [form, setForm] = useState<ItemFormState | null>(null);
  const revision = item?.revision ?? 0;
  if (item && (form === null || form.revision !== revision)) {
    setForm({ revision, assigneeChoice: null, dueValue: toLocalDateTimeInput(item.due_at), dueInvalid: false });
  }

  const [cancelling, setCancelling] = useState(false);
  // No reason is chosen until one is: every open of the dialog starts clean.
  const [cancelReason, setCancelReason] = useState<ReviewCancelReason | null>(null);
  const [nextState, setNextState] = useState<"idle" | "finding" | "empty" | "error">("idle");
  const [nextError, setNextError] = useState<unknown>(null);

  const isActive = item !== null && (item.status === "open" || item.status === "in_review");
  const isAssignee = item !== null && user !== null && item.assignee_user_id === user.id;
  const assignableMembers = (membersQuery.data?.items ?? []).filter((member) => ASSIGNABLE_ROLES.has(member.role));

  async function runWrite(promise: Promise<unknown>): Promise<boolean> {
    setConflict(false);
    setWriteError(null);
    try {
      await promise;
      return true;
    } catch (error) {
      if (isRevisionConflict(error)) {
        setConflict(true);
        // The latest version is loaded; the user decides whether to repeat the write.
        void itemQuery.refetch();
        void criteriaQuery.refetch();
      } else {
        setWriteError(error);
      }
      return false;
    }
  }

  async function submitAssign() {
    if (!item) return;
    const choice = form?.assigneeChoice ?? item.assignee_user_id;
    // "unassigned" is the select's sentinel option, never a user id: the API
    // takes null to unassign.
    const done = await runWrite(
      assignMutation.mutateAsync({
        id: item.id,
        input: { expected_revision: item.revision, assignee_user_id: choice === "unassigned" ? null : choice ?? null },
      }),
    );
    if (done) toast.success(copy.itemSaved);
  }

  async function submitDueTime() {
    if (!item) return;
    // A past or too-far time is refused here, with no request at all.
    if (!isDueAtInRange(form?.dueValue ?? "")) {
      setForm((running) => (running ? { ...running, dueInvalid: true } : running));
      return;
    }
    const done = await runWrite(
      dueMutation.mutateAsync({
        id: item.id,
        input: { expected_revision: item.revision, due_at: new Date(form!.dueValue).toISOString() },
      }),
    );
    if (done) toast.success(copy.itemSaved);
  }

  async function submitStart() {
    if (!item) return;
    await runWrite(startMutation.mutateAsync({ id: item.id, expectedRevision: item.revision }));
  }

  async function submitComplete() {
    if (!item) return;
    await runWrite(completeMutation.mutateAsync({ id: item.id, expectedRevision: item.revision }));
  }

  async function submitCancel() {
    if (!item || cancelReason === null) return;
    const done = await runWrite(
      cancelMutation.mutateAsync({ id: item.id, input: { expected_revision: item.revision, reason: cancelReason } }),
    );
    if (done) {
      toast.success(copy.itemSaved);
      setCancelling(false);
    }
  }

  async function goNext() {
    if (!item) return;
    setNextState("finding");
    setNextError(null);
    try {
      const next = await nextReviewItem(item.id);
      if (next) navigate(`/reviews/${next.id}`);
      else setNextState("empty");
    } catch (error) {
      // The lookup failed; the reason stays under the button for a retry.
      setNextError(error);
      setNextState("error");
    }
  }

  if (itemQuery.isPending) {
    return (
      <div className="platform-page">
        <PageHeader title={copy.title} />
        <LoadingState label={copy.loading} />
      </div>
    );
  }

  if (itemQuery.isError) {
    // An invisible item answers exactly like a missing one: one text, no hint.
    if (isReviewItemNotFound(itemQuery.error)) {
      return (
        <div className="platform-page max-w-2xl">
          <PageHeader title={copy.title} />
          <Card<"section"> render={(props) => <section {...props} />}>
            <Card.Content className="flex flex-col items-start gap-4 p-6">
              <p className="m-0 text-sm text-foreground">{copy.notFound}</p>
              <RouterLink to="/reviews" className={linkVariants().base()}>
                <ArrowLeft size={15} aria-hidden="true" className="rtl-mirror" />
                {copy.backToQueue}
              </RouterLink>
            </Card.Content>
          </Card>
        </div>
      );
    }
    return (
      <div className="platform-page">
        <PageHeader title={copy.title} />
        <ErrorState
          error={itemQuery.error}
          codes={REVIEW_ERROR_CODES}
          fallback={copy.loadError}
        />
      </div>
    );
  }

  if (!item) return null;

  const pendingWrite =
    assignMutation.isPending ||
    dueMutation.isPending ||
    startMutation.isPending ||
    completeMutation.isPending ||
    cancelMutation.isPending;

  return (
    <div className="platform-page space-y-5">
      <PageHeader title={copy.title} />

      <Card<"section"> render={(props) => <section {...props} aria-label={copy.itemRegionLabel} />} className="min-w-0">
        <Card.Content className="min-w-0 space-y-4">
          <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <dt className="text-xs font-medium text-muted">{copy.fieldStatus}</dt>
              <dd className="m-0 mt-1 flex flex-wrap items-center gap-1.5">
                <Chip color={item.status === "completed" ? "success" : item.status === "cancelled" ? "danger" : "accent"} variant="soft" size="sm">
                  <Chip.Label>
                    {item.status === "open"
                      ? copy.statuses.open
                      : item.status === "in_review"
                        ? copy.statuses.in_review
                        : item.status === "completed"
                          ? copy.statuses.completed
                          : item.status === "cancelled"
                            ? copy.statuses.cancelled
                            : item.status}
                  </Chip.Label>
                </Chip>
                {item.is_overdue && (
                  <Chip color="danger" variant="soft" size="sm">
                    <Chip.Label>{copy.overdue}</Chip.Label>
                  </Chip>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted">{copy.fieldDue}</dt>
              <dd className="m-0 mt-1 whitespace-nowrap">{formatDate(item.due_at, lang)}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted">{copy.fieldAssignee}</dt>
              <dd className="m-0 mt-1" dir="auto">
                {item.assignee_user_id === null
                  ? copy.unassigned
                  : (memberNames.get(item.assignee_user_id) ?? item.assignee_user_id)}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted">{copy.fieldTeam}</dt>
              <dd className="m-0 mt-1" dir="auto">
                {item.team_id === null ? copy.noTeam : (teamNames.get(item.team_id) ?? item.team_id)}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted">{copy.fieldSource}</dt>
              <dd className="m-0 mt-1">
                {item.source === "manual" ? copy.sources.manual : item.source === "sample" ? copy.sources.sample : item.source}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted">{copy.fieldRevision}</dt>
              <dd className="m-0 mt-1">{formatNumber(item.revision, lang)}</dd>
            </div>
            {item.status === "completed" && item.completed_at && (
              <div>
                <dt className="text-xs font-medium text-muted">{copy.completedAtLabel}</dt>
                <dd className="m-0 mt-1 whitespace-nowrap">{formatDate(item.completed_at, lang)}</dd>
              </div>
            )}
            {item.status === "cancelled" && item.cancel_reason && (
              <div>
                <dt className="text-xs font-medium text-muted">{copy.statuses.cancelled}</dt>
                <dd className="m-0 mt-1">
                  {copy.cancelledWith(
                    copy.cancelReasons[item.cancel_reason as ReviewCancelReason] ?? String(item.cancel_reason),
                  )}
                </dd>
              </div>
            )}
            <div>
              <dt className="text-xs font-medium text-muted">{copy.columnMeeting}</dt>
              <dd className="m-0 mt-1">
                <RouterLink to={`/meetings/${item.meeting_id}`} className={linkVariants().base()}>
                  {copy.openReport}
                </RouterLink>
              </dd>
            </div>
          </dl>

          {conflict && (
            <Alert status="warning" role="status">
              <Alert.Indicator aria-hidden="true" />
              <Alert.Content>
                <Alert.Description>{copy.conflict}</Alert.Description>
              </Alert.Content>
            </Alert>
          )}
          {writeError !== null && (
            <ErrorState error={writeError} codes={REVIEW_ERROR_CODES} fallback={copy.writeFailed} />
          )}

          {canManage && (
            <div className="space-y-4 border-t border-separator pt-4">
              {isActive && (
                <div className="flex flex-wrap items-end gap-3">
                  <Select
                    className="w-full sm:w-64"
                    value={form?.assigneeChoice ?? item.assignee_user_id ?? "unassigned"}
                    onChange={(key: Key | null) =>
                      setForm((running) =>
                        running ? { ...running, assigneeChoice: typeof key === "string" ? key : null } : running,
                      )
                    }
                    isDisabled={pendingWrite}
                  >
                    <Label>{copy.assignLabel}</Label>
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        <ListBox.Item key="unassigned" id="unassigned" textValue={copy.unassigned}>
                          {copy.unassigned}
                          <ListBox.ItemIndicator />
                        </ListBox.Item>
                        {assignableMembers.map((member) => (
                          <ListBox.Item key={member.user_id} id={member.user_id} textValue={member.display_name}>
                            <span dir="auto">{member.display_name}</span>
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        ))}
                      </ListBox>
                    </Select.Popover>
                  </Select>
                  <Button variant="secondary" isPending={assignMutation.isPending} onPress={() => void submitAssign()}>
                    {copy.assignSubmit}
                  </Button>
                </div>
              )}

              {isActive && (
                <div className="flex flex-wrap items-end gap-3">
                  <TextField
                    variant="secondary"
                    className="w-full sm:w-64"
                    value={form?.dueValue ?? ""}
                    onChange={(value) =>
                      setForm((running) => (running ? { ...running, dueValue: value } : running))
                    }
                    isInvalid={form?.dueInvalid ?? false}
                    isReadOnly={pendingWrite}
                  >
                    <Label>{copy.dueLabel}</Label>
                    {/* A plain datetime-local input: the value is local time,
                        the write sends Date.toISOString() with its UTC offset. */}
                    <Input type="datetime-local" />
                    {(form?.dueInvalid ?? false) && (
                      <p className="m-0 text-xs text-danger" role="alert">
                        {copy.dueInvalid}
                      </p>
                    )}
                  </TextField>
                  <Button variant="secondary" isPending={dueMutation.isPending} onPress={() => void submitDueTime()}>
                    {copy.dueSubmit}
                  </Button>
                </div>
              )}

              <div className="flex flex-wrap gap-3">
                {canManage && item.status === "open" && isAssignee && (
                  <Button variant="primary" isPending={startMutation.isPending} onPress={() => void submitStart()}>
                    {copy.startReview}
                  </Button>
                )}
                {canManage && item.status === "in_review" && isAssignee && (
                  <Button variant="primary" isPending={completeMutation.isPending} onPress={() => void submitComplete()}>
                    {copy.completeReview}
                  </Button>
                )}
                {canManage && isActive && (
                  <Button
                    variant="ghost"
                    className="text-danger"
                    onPress={() => {
                      setCancelReason(null);
                      setCancelling(true);
                    }}
                  >
                    {copy.cancelItem}
                  </Button>
                )}
              </div>
            </div>
          )}

          {item.status === "completed" && (
            <Alert status="success" role="status">
              <Alert.Indicator aria-hidden="true" />
              <Alert.Content>
                <Alert.Description>
                  {copy.reviewCompleted}{" "}
                  {nextState !== "empty" && (
                    <Button
                      size="sm"
                      variant="secondary"
                      className="ms-2"
                      isPending={nextState === "finding"}
                      onPress={() => void goNext()}
                    >
                      {copy.nextItem}
                    </Button>
                  )}
                </Alert.Description>
              </Alert.Content>
            </Alert>
          )}
          {nextState === "empty" && (
            <p className="m-0 text-sm text-muted">
              {copy.queueEmpty}{" "}
              <RouterLink to="/reviews" className={linkVariants().base()}>
                {copy.queueEmptyLink}
              </RouterLink>
            </p>
          )}
          {nextState === "error" && nextError !== null && (
            <ErrorState error={nextError} codes={REVIEW_ERROR_CODES} fallback={copy.writeFailed} />
          )}
        </Card.Content>
      </Card>

      <CriteriaPanel itemId={item.id} itemStatus={item.status} isAssignee={isAssignee} canManage={canManage} />

      {cancelling && (
        <AlertDialog
          isOpen
          onOpenChange={(open) => {
            if (!cancelMutation.isPending && !open) setCancelling(false);
          }}
        >
          <AlertDialog.Backdrop>
            <AlertDialog.Container>
              <AlertDialog.Dialog>
                <AlertDialog.Header>
                  <AlertDialog.Icon status="danger" />
                  <AlertDialog.Heading>{copy.cancelHeading}</AlertDialog.Heading>
                </AlertDialog.Header>
                <AlertDialog.Body className="space-y-3">
                  <p className="m-0 text-sm text-muted">{copy.cancelBody}</p>
                  <Select
                    variant="secondary"
                    fullWidth
                    value={cancelReason ?? ""}
                    onChange={(key: Key | null) => {
                      if (typeof key === "string" && (CANCEL_REASONS as readonly string[]).includes(key)) {
                        setCancelReason(key as ReviewCancelReason);
                      }
                    }}
                  >
                    <Label>{copy.cancelReasonLabel}</Label>
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        {CANCEL_REASONS.map((reason) => (
                          <ListBox.Item key={reason} id={reason} textValue={copy.cancelReasons[reason]}>
                            {copy.cancelReasons[reason]}
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        ))}
                      </ListBox>
                    </Select.Popover>
                  </Select>
                </AlertDialog.Body>
                <AlertDialog.Footer>
                  {/* Cancel in the dialog sends nothing; the item stays as it is.
                      Confirm needs a reason: none is chosen when it opens. */}
                  <Button variant="ghost" isDisabled={cancelMutation.isPending} onPress={() => setCancelling(false)}>
                    {copy.cancelDialogCancel}
                  </Button>
                  <Button
                    variant="danger"
                    isDisabled={cancelMutation.isPending || cancelReason === null}
                    onPress={() => void submitCancel()}
                  >
                    {copy.cancelConfirm}
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
