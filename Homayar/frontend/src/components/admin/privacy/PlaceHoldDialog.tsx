import { useState, type ComponentProps, type FormEvent } from "react";
import {
  Alert,
  Button,
  Calendar,
  DateField,
  DatePicker,
  Description,
  FieldError,
  Input,
  Label,
  Modal,
  TextField,
  toast,
} from "@heroui/react";
import { today, type CalendarDate, type DateValue } from "@internationalized/date";

import { describeHoldError } from "@/components/admin/privacy/hold-errors";
import { HOLD_ERROR_CODES, PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import { isValidReason, REASON_MAX_LENGTH } from "@/components/admin/privacy/privacy-model";
import { useI18n } from "@/lib/i18n-context";
import { reviewAtFromDay, usePlaceLegalHold } from "@/lib/privacy-api";

type DateSegment = ComponentProps<typeof DateField.Segment>["segment"];

const DEFAULT_REVIEW_DAYS = 90;
const MAX_REVIEW_DAYS = 3650;

/**
 * The review day is sent as 00:00 UTC, so "today" is the UTC day too: then
 * tomorrow is always in the future, whatever the browser's time zone.
 */
function reviewWindow() {
  const day = today("UTC");
  return {
    initial: day.add({ days: DEFAULT_REVIEW_DAYS }),
    min: day.add({ days: 1 }),
    max: day.add({ days: MAX_REVIEW_DAYS }),
  };
}

/**
 * Places a hold on every live recording of one meeting. Mounted only while
 * open, so each open starts with the default review date (today + 90 days).
 * A refused hold keeps the dialog open with what was typed.
 */
export default function PlaceHoldDialog({ meetingId, onClose }: { meetingId: string; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  const mutation = usePlaceLegalHold();
  const [range] = useState(reviewWindow);
  const [reason, setReason] = useState("");
  const [reviewDay, setReviewDay] = useState<CalendarDate | null>(range.initial);

  const dayInRange =
    reviewDay !== null && reviewDay.compare(range.min) >= 0 && reviewDay.compare(range.max) <= 0;
  const canSubmit = isValidReason(reason) && dayInRange && !mutation.isPending;
  const failure = mutation.error ? describeHoldError(mutation.error, lang, copy.holdFailed) : null;
  const dateError = reviewDay !== null && !dayInRange ? HOLD_ERROR_CODES.hold_review_invalid[lang] : null;
  const reviewDateMessage = failure?.onReviewDate ? failure.text : dateError;

  function changeOpen(open: boolean) {
    if (mutation.isPending) return;
    if (!open) onClose();
  }

  function changeDay(value: DateValue | null) {
    // The picker keeps the calendar of its value: Gregorian, even when the
    // interface shows the Persian calendar (same rule as AuditFilterBar).
    setReviewDay(value ? (value as CalendarDate) : null);
    if (failure?.onReviewDate) mutation.reset();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || reviewDay === null) return;
    mutation.mutate(
      {
        meetingId,
        // `toString()` is always the Gregorian day, whatever calendar is shown.
        input: { reason: reason.trim(), review_at: reviewAtFromDay(reviewDay.toString().slice(0, 10)) },
      },
      {
        onSuccess: () => {
          toast.success(copy.holdPlaced);
          onClose();
        },
      },
    );
  }

  return (
    // The documented controlled form: open state on the backdrop, no Modal
    // root. A root is a DialogTrigger and warns when it has no pressable child.
    <Modal.Backdrop isOpen onOpenChange={changeOpen}>
      <Modal.Container>
        <Modal.Dialog>
          <form onSubmit={submit} noValidate>
            <Modal.Header>
              <Modal.Heading>{copy.placeHoldHeading}</Modal.Heading>
            </Modal.Header>
            <Modal.Body className="space-y-4">
              <p className="m-0 text-sm text-muted">{copy.placeHoldBody}</p>
              <TextField
                value={reason}
                onChange={setReason}
                maxLength={REASON_MAX_LENGTH}
                isReadOnly={mutation.isPending}
                fullWidth
              >
                <Label>{copy.reasonLabel}</Label>
                <Input dir="auto" />
                <Description>{copy.reasonHint}</Description>
              </TextField>

              <DatePicker
                value={reviewDay}
                onChange={changeDay}
                minValue={range.min}
                maxValue={range.max}
                isInvalid={reviewDateMessage !== null}
                isReadOnly={mutation.isPending}
                className="w-full"
              >
                <Label>{copy.reviewDateLabel}</Label>
                <DateField.Group fullWidth>
                  <DateField.Input>
                    {(segment: DateSegment) => <DateField.Segment segment={segment} />}
                  </DateField.Input>
                  <DateField.Suffix>
                    <DatePicker.Trigger aria-label={copy.chooseReviewDate}>
                      <DatePicker.TriggerIndicator />
                    </DatePicker.Trigger>
                  </DateField.Suffix>
                </DateField.Group>
                {reviewDateMessage ? (
                  <FieldError>{reviewDateMessage}</FieldError>
                ) : (
                  <Description>{copy.reviewDateHint}</Description>
                )}
                <DatePicker.Popover>
                  <Calendar aria-label={copy.reviewDateLabel}>
                    <Calendar.Header>
                      <Calendar.Heading />
                      <Calendar.NavButton slot="previous" />
                      <Calendar.NavButton slot="next" />
                    </Calendar.Header>
                    <Calendar.Grid>
                      <Calendar.GridHeader>
                        {(day) => <Calendar.HeaderCell>{day}</Calendar.HeaderCell>}
                      </Calendar.GridHeader>
                      <Calendar.GridBody>{(date) => <Calendar.Cell date={date} />}</Calendar.GridBody>
                    </Calendar.Grid>
                  </Calendar>
                </DatePicker.Popover>
              </DatePicker>

              {failure && !failure.onReviewDate && (
                <Alert status="danger" role="alert">
                  <Alert.Indicator aria-hidden="true" />
                  <Alert.Content>
                    <Alert.Description>{failure.text}</Alert.Description>
                  </Alert.Content>
                </Alert>
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button variant="ghost" isDisabled={mutation.isPending} onPress={() => changeOpen(false)}>
                {copy.cancel}
              </Button>
              <Button type="submit" variant="primary" isDisabled={!canSubmit}>
                {copy.placeHoldConfirm}
              </Button>
            </Modal.Footer>
          </form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
