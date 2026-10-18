import { useEffect, useState } from "react";
import { Button, Chip, Table } from "@heroui/react";

import { VOICE_COPY } from "@/components/admin/voice/voice-copy";
import { heartbeatAge } from "@/components/admin/voice/voice-model";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { VoiceMediaNode } from "@/types/voice";

/** The current time, updated every second, so heartbeat ages keep counting between refreshes. */
function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/**
 * One row per media node: health, heartbeat age, calls held against the
 * node's maximum, draining, and the Drain or Resume button. The button only
 * asks the page to open the confirm dialog; it sends nothing itself.
 */
export default function MediaNodeTable({
  nodes,
  onAction,
}: {
  nodes: VoiceMediaNode[];
  onAction: (node: VoiceMediaNode) => void;
}) {
  const { lang } = useI18n();
  const copy = VOICE_COPY[lang];
  const now = useNow(1000);

  function ageText(node: VoiceMediaNode) {
    const age = heartbeatAge(node.heartbeat_at, now);
    if (!age) return "—";
    const value = formatNumber(age.value, lang);
    if (age.unit === "s") return copy.secondsAgo(value);
    return age.unit === "min" ? copy.minutesAgo(value) : copy.hoursAgo(value);
  }

  return (
    <Table className="min-w-0">
      {/* The table scrolls sideways on a narrow screen, so the region takes
          focus for a keyboard user (as the teams table does). */}
      <Table.ScrollContainer role="region" tabIndex={0} aria-label={copy.tableScrollLabel}>
        <Table.Content aria-label={copy.mediaNodes} className="min-w-[640px]">
          <Table.Header>
            <Table.Column id="node" isRowHeader>
              {copy.columnNode}
            </Table.Column>
            <Table.Column id="health">{copy.columnHealth}</Table.Column>
            <Table.Column id="heartbeat">{copy.columnHeartbeat}</Table.Column>
            <Table.Column id="calls">{copy.columnCalls}</Table.Column>
            <Table.Column id="status">{copy.columnStatus}</Table.Column>
            <Table.Column id="actions">{copy.columnActions}</Table.Column>
          </Table.Header>
          {/* React Aria caches rows; the clock and the language change cells without changing a node. */}
          <Table.Body items={nodes} dependencies={[lang, now, onAction]}>
            {(node: VoiceMediaNode) => (
              <Table.Row id={node.node_id}>
                <Table.Cell>
                  <span dir="ltr" className="font-medium text-foreground">
                    {node.node_id}
                  </span>
                </Table.Cell>
                <Table.Cell>
                  <Chip color={node.healthy ? "success" : "danger"} variant="soft" size="sm">
                    <Chip.Label>{node.healthy ? copy.healthy : copy.unhealthy}</Chip.Label>
                  </Chip>
                </Table.Cell>
                <Table.Cell>
                  <time dateTime={node.heartbeat_at} className="tabular-nums">
                    {ageText(node)}
                  </time>
                </Table.Cell>
                <Table.Cell>
                  <span className="tabular-nums">
                    {`${formatNumber(node.held, lang)} / ${formatNumber(node.max_concurrent, lang)}`}
                  </span>
                </Table.Cell>
                <Table.Cell>
                  <Chip color={node.draining ? "warning" : "default"} variant="soft" size="sm">
                    <Chip.Label>{node.draining ? copy.draining : copy.active}</Chip.Label>
                  </Chip>
                </Table.Cell>
                <Table.Cell>
                  <Button
                    size="sm"
                    variant={node.draining ? "secondary" : "ghost"}
                    aria-label={node.draining ? copy.resumeNode(node.node_id) : copy.drainNode(node.node_id)}
                    onPress={() => onAction(node)}
                  >
                    {node.draining ? copy.resume : copy.drain}
                  </Button>
                </Table.Cell>
              </Table.Row>
            )}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  );
}
