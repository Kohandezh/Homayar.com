import { useId, type ReactNode } from "react";
import { Card } from "@heroui/react";

/** One part of the voice operations page: a HeroUI Card as a <section> landmark named by its <h2>. */
export default function VoiceSection({ title, children }: { title: string; children: ReactNode }) {
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
