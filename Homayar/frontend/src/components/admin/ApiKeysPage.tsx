import { useMemo, useRef, useState } from "react";
import { Link } from "@heroui/react";

import ApiKeyList from "@/components/admin/api-keys/ApiKeyList";
import CreateApiKeyModal from "@/components/admin/api-keys/CreateApiKeyModal";
import RevokeApiKeyDialog from "@/components/admin/api-keys/RevokeApiKeyDialog";
import { apiDocsUrl, creatorLabel } from "@/components/admin/api-keys/api-key-model";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { useApiKeys, useMembers } from "@/lib/platform-api";
import type { ApiKey } from "@/types/platform";

/**
 * The workspace's API keys (K1): the list, the create flow with its one-time
 * key, and revocation. The route guard only mounts this page for a workspace
 * with `api_key:manage`, so no permission branch is needed here.
 */
export default function ApiKeysPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const keysQuery = useApiKeys();
  // A failed member list must never blank the key list (K2): it only costs the
  // creator column its names.
  const membersQuery = useMembers();
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const membersById = useMemo(
    () => new Map((membersQuery.data?.items ?? []).map((member) => [member.user_id, member])),
    [membersQuery.data],
  );

  function creatorName(key: ApiKey): string {
    if (membersQuery.isPending || membersQuery.isError) return "—";
    const member = membersById.get(key.created_by_user_id);
    // A matching member always has a name to show; only a creator with no
    // member row reads as a former member (A1).
    return member
      ? creatorLabel(member)
      : (isFa ? "عضو پیشین" : "Former member");
  }

  function closeRevoke(removed: boolean) {
    setRevokeTarget(null);
    // After Cancel or Esc, React Aria returns focus to the Revoke button that
    // opened the dialog. Only when that button is gone (the key was revoked,
    // or a 404 said it already was) may focus fall back to the list region.
    if (!removed) return;
    requestAnimationFrame(() => {
      if (document.activeElement === document.body) listRef.current?.focus();
    });
  }

  const items = keysQuery.data?.items ?? [];

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={isFa ? "کلیدهای API" : "API keys"}
        actions={
          <div className="flex flex-wrap items-center gap-3">
            {/* The production backend serves no docs page, so the link is a
                development companion only. */}
            {import.meta.env.MODE !== "production" && (
              <Link href={apiDocsUrl()} target="_blank" rel="noopener noreferrer">
                {isFa ? "مستندات API" : "API documentation"}
              </Link>
            )}
            <CreateApiKeyModal />
          </div>
        }
      />
      <p className="mt-0 mb-4 text-sm text-muted">
        {isFa
          ? "کلید API به سامانهٔ تلفن (PBX)، CRM یا ابزارهای دیگر شما اجازه می‌دهد به همایار وصل شوند. هر کلید به نام سازندهٔ خود و فقط با دسترسی‌هایی که انتخاب می‌کنید کار می‌کند."
          : "API keys let your phone system (PBX), CRM or other tools connect to Homayar. A key acts for the person who created it, with only the scopes you choose."}
      </p>

      <div className="mt-4" ref={listRef} tabIndex={-1}>
        {keysQuery.isPending ? (
          <LoadingState label={isFa ? "در حال دریافت کلیدها" : "Loading API keys"} />
        ) : keysQuery.isError ? (
          <ErrorState
            error={keysQuery.error}
            fallback={isFa ? "دریافت فهرست کلیدها ناموفق بود." : "Could not load the API key list."}
          />
        ) : items.length === 0 ? (
          <EmptyState label={isFa ? "هنوز کلید API ساخته نشده است" : "No API keys yet"} />
        ) : (
          <ApiKeyList keys={items} creatorName={creatorName} onRevoke={setRevokeTarget} />
        )}
      </div>

      <RevokeApiKeyDialog target={revokeTarget} onClose={closeRevoke} />
    </div>
  );
}
