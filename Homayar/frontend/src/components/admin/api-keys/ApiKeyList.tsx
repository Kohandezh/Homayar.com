import { Button, Chip, Table } from "@heroui/react";

import {
  apiKeyStatus,
  expiryWarningDays,
  expiryWarningLabel,
  orderedScopes,
  statusLabel,
} from "@/components/admin/api-keys/api-key-model";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { ApiKey } from "@/types/platform";

const STATUS_CHIP_COLOR = {
  active: "success",
  expired: "warning",
  revoked: "danger",
} as const;

/** One row of the key list (K2); the server order is kept as delivered. */
export default function ApiKeyList({
  keys,
  creatorName,
  onRevoke,
}: {
  keys: ApiKey[];
  /** Resolved per key: the creator's name, "Former member", or "—" while /members is unusable. */
  creatorName: (key: ApiKey) => string;
  onRevoke: (key: ApiKey) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const never = isFa ? "هرگز" : "Never";

  return (
    <Table>
      {/* The table scrolls sideways on a narrow screen; the region takes focus
          so a keyboard user can scroll it too (as on the members page). */}
      <Table.ScrollContainer
        role="region"
        tabIndex={0}
        aria-label={isFa ? "فهرست کلیدهای API، با پیمایش افقی" : "API key list, scrolls horizontally"}
      >
        {/* Arrow-key navigation: the controls inside the cells are the focus
            stops (not the cells), so a dialog opened from a row button gives
            focus back to that button when it closes (A4). */}
        <Table.Content
          aria-label={isFa ? "کلیدهای API فضای کاری" : "Workspace API keys"}
          keyboardNavigationBehavior="arrow"
        >
          <Table.Header>
            <Table.Column isRowHeader>{isFa ? "نام" : "Name"}</Table.Column>
            <Table.Column>{isFa ? "پیشوند" : "Prefix"}</Table.Column>
            <Table.Column>{isFa ? "دسترسی‌ها" : "Scopes"}</Table.Column>
            <Table.Column>{isFa ? "سازنده" : "Created by"}</Table.Column>
            <Table.Column>{isFa ? "آخرین استفاده" : "Last used"}</Table.Column>
            <Table.Column>{isFa ? "انقضا" : "Expires"}</Table.Column>
            <Table.Column>{isFa ? "وضعیت" : "Status"}</Table.Column>
            {/* Holds each row's Revoke button; the text is inside the column
                because React Aria drops aria-label on a column, which would
                leave the header unnamed. */}
            <Table.Column>{isFa ? "کارها" : "Actions"}</Table.Column>
          </Table.Header>
          <Table.Body>
            {keys.map((key) => {
              const status = apiKeyStatus(key);
              const warnDays = expiryWarningDays(key);
              return (
                <Table.Row key={key.id} id={key.id} data-status={status}>
                  <Table.Cell><bdi dir="auto">{key.name}</bdi></Table.Cell>
                  <Table.Cell>
                    {/* The prefix is the only part of the key the server keeps;
                        the raw key itself never appears in the list. */}
                    <span dir="ltr" className="font-mono text-xs">hk_{key.key_prefix}…</span>
                  </Table.Cell>
                  <Table.Cell>
                    <span className="flex flex-wrap gap-1">
                      {orderedScopes(key.scopes).map((scope) => (
                        <Chip key={scope} variant="soft" size="sm">
                          <Chip.Label dir="ltr" className="font-mono">{scope}</Chip.Label>
                        </Chip>
                      ))}
                    </span>
                  </Table.Cell>
                  <Table.Cell><bdi dir="auto">{creatorName(key)}</bdi></Table.Cell>
                  <Table.Cell>
                    {key.last_used_at
                      ? <time dateTime={key.last_used_at}>{formatDate(key.last_used_at, lang)}</time>
                      : never}
                  </Table.Cell>
                  <Table.Cell>
                    {key.expires_at ? (
                      <span className="grid gap-1">
                        <time dateTime={key.expires_at}>{formatDate(key.expires_at, lang)}</time>
                        {warnDays !== null && (
                          <Chip color="warning" variant="soft" size="sm" className="justify-self-start">
                            <Chip.Label>{expiryWarningLabel(warnDays, lang)}</Chip.Label>
                          </Chip>
                        )}
                      </span>
                    ) : (
                      never
                    )}
                  </Table.Cell>
                  <Table.Cell>
                    <Chip color={STATUS_CHIP_COLOR[status]} variant="soft" size="sm">
                      <Chip.Label>{statusLabel(status, lang)}</Chip.Label>
                    </Chip>
                  </Table.Cell>
                  <Table.Cell>
                    {/* Only a working key can be revoked; the button is named
                        after the key so screen readers say which one. */}
                    {status === "active" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-danger"
                        aria-label={isFa ? `ابطال ${key.name}` : `Revoke ${key.name}`}
                        onPress={() => onRevoke(key)}
                      >
                        {isFa ? "ابطال" : "Revoke"}
                      </Button>
                    )}
                  </Table.Cell>
                </Table.Row>
              );
            })}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  );
}
