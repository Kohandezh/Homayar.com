import { useId, type ReactNode } from "react";
import { Card, Skeleton } from "@heroui/react";
import { EmptyState, ErrorState } from "@/components/platform/Ui";
import type { CardState } from "@/components/admin/overview/card-state";

/**
 * One overview card: a HeroUI Card rendered as a <section> landmark named by
 * its <h2>. The heading stays in every state, so a failed request never
 * blanks the card. The footer shows only once the card has an answer.
 */
export default function OverviewCard({
  title,
  state,
  error,
  emptyText,
  footer,
  children,
}: {
  title: string;
  state: CardState;
  error?: unknown;
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
      <Card.Header>
        <Card.Title<"h2"> render={(props) => <h2 {...props} />} id={headingId}>
          {title}
        </Card.Title>
      </Card.Header>
      <Card.Content className="min-w-0">
        {state === "loading" && (
          <div className="flex flex-col gap-3" aria-hidden="true">
            <Skeleton className="h-4 w-3/4 rounded-lg" />
            <Skeleton className="h-4 w-1/2 rounded-lg" />
            <Skeleton className="h-4 w-2/3 rounded-lg" />
          </div>
        )}
        {state === "error" && <ErrorState error={error} />}
        {state === "empty" && emptyText && <EmptyState label={emptyText} />}
        {state === "data" && children}
      </Card.Content>
      {footer && (state === "data" || state === "empty") && <Card.Footer>{footer}</Card.Footer>}
    </Card>
  );
}
