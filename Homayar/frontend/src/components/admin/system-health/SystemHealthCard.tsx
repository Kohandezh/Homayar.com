import { useId, type ReactNode } from "react";
import { Card } from "@heroui/react";

/** One card of the system health page: a HeroUI Card as a <section> landmark named by its <h2> (P3). */
export default function SystemHealthCard({ title, children }: { title: string; children: ReactNode }) {
  const headingId = useId();
  return (
    <Card<"section"> render={(props) => <section {...props} />} aria-labelledby={headingId} className="min-w-0">
      <Card.Header>
        <Card.Title<"h2"> render={(props) => <h2 {...props} />} id={headingId}>
          {title}
        </Card.Title>
      </Card.Header>
      <Card.Content className="min-w-0">{children}</Card.Content>
    </Card>
  );
}
