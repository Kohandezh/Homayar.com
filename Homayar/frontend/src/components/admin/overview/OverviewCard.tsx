import { useId, type ReactNode } from "react";
import { Card, Skeleton } from "@heroui/react";
import { EmptyState, ErrorState } from "@/components/platform/Ui";
import type { CardState } from "@/components/admin/overview/card-state";
import type { ErrorCodeMessages } from "@/lib/error-messages";

/**
 * One overview card: a HeroUI Card rendered as a <section> landmark named by
 * its <h2>. The heading and the header control stay in every state, so a
 * failed or empty request never blanks the card or takes away a filter. The
 * footer shows only once the card has an answer.
 */
export default function OverviewCard({
  title,
  action,
  state,
  error,
  errorCodes,
  persistentFooter,
  emptyText,
  footer,
  children,
}: {
  title: string;
  /** A control next to the heading, such as a filter; shown in every state. */
  action?: ReactNode;
  state: CardState;
  error?: unknown;
  /** Texts for the API error codes this card can get, by code. */
  errorCodes?: ErrorCodeMessages;
  /**
   * Rendered at the footer position in every state. A card whose footer must
   * never unmount (paging controls that hold keyboard focus while the next
   * page loads) uses this instead of `footer`.
   */
  persistentFooter?: ReactNode;
  emptyText?: string;
  footer?: ReactNode;
  children?: ReactNode;
}) {
  const headingId = useId();
  return (
    <Card<"section">
      render={(props) => <section {...props} />}
      aria-labelledby={headingId}
      aria-busy={state === "loading" ? true : undefined}
      className="min-w-0"
    >
      {/* Only a card with a header control needs the two-column header; the others render as on the base. */}
      <Card.Header
        className={action ? "flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" : undefined}
      >
        <Card.Title<"h2"> render={(props) => <h2 {...props} />} id={headingId}>
          {title}
        </Card.Title>
        {action}
      </Card.Header>
      <Card.Content className="min-w-0">
        {state === "loading" && (
          <div className="flex flex-col gap-3" aria-hidden="true">
            <Skeleton className="h-4 w-3/4 rounded-lg" />
            <Skeleton className="h-4 w-1/2 rounded-lg" />
            <Skeleton className="h-4 w-2/3 rounded-lg" />
          </div>
        )}
        {state === "error" && <ErrorState error={error} codes={errorCodes} />}
        {state === "empty" && emptyText && <EmptyState label={emptyText} />}
        {state === "data" && children}
      </Card.Content>
      {persistentFooter && <Card.Footer>{persistentFooter}</Card.Footer>}
      {footer && !persistentFooter && (state === "data" || state === "empty") && <Card.Footer>{footer}</Card.Footer>}
    </Card>
  );
}
