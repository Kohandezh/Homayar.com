import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Globe, Menu } from "lucide-react";
import { Avatar, Button, ListBox, SearchField, Select } from "@heroui/react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";

/** Shown only for more than one workspace; refused ones stay listed but cannot be chosen. */
function WorkspaceSelect() {
  const { workspace, workspaces, unavailableWorkspaceIds, setWorkspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";

  return (
    <Select
      aria-label={isFa ? "فضای کاری" : "Workspace"}
      placeholder={isFa ? "انتخاب فضای کاری" : "Choose a workspace"}
      value={workspace?.id ?? null}
      onChange={(id) => {
        if (typeof id === "string") setWorkspace(id);
      }}
      disabledKeys={unavailableWorkspaceIds}
      variant="secondary"
      // React Aria's hidden native <select> is `position: fixed` at the viewport's left edge with
      // a -1px margin; in RTL that widens the page by 1px. Layout containment keeps it in this box.
      className="w-40 [contain:layout] sm:w-48"
    >
      <Select.Trigger>
        <Select.Value />
        <Select.Indicator />
      </Select.Trigger>
      <Select.Popover>
        <ListBox>
          {workspaces.map((item) => {
            const unavailable = unavailableWorkspaceIds.includes(item.id);
            const suffix = unavailable ? (isFa ? " (در دسترس نیست)" : " (unavailable)") : "";
            return (
              <ListBox.Item key={item.id} id={item.id} textValue={`${item.name}${suffix}`}>
                {item.name}
                {unavailable && <span className="text-xs text-muted">{suffix}</span>}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            );
          })}
        </ListBox>
      </Select.Popover>
    </Select>
  );
}

export default function Header({ onOpenNavigation }: { onOpenNavigation: () => void }) {
  const navigate = useNavigate();
  const { user, workspace, workspaces } = useAuth();
  const { lang, setLang } = useI18n();
  const isFa = lang === "fa";
  const [query, setQuery] = useState("");

  function submitSearch(value: string) {
    const trimmed = value.trim();
    if (trimmed.length >= 2 && workspace) navigate(`/search?q=${encodeURIComponent(trimmed)}`);
  }

  const accountName = user?.display_name || user?.email || user?.phone_e164;
  const initial = (user?.display_name || user?.email)?.[0]?.toUpperCase() || "H";

  return (
    <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-separator bg-surface/95 px-4 py-3 backdrop-blur sm:px-6">
      <Button
        isIconOnly
        variant="ghost"
        className="lg:hidden"
        aria-label={isFa ? "باز کردن منو" : "Open navigation"}
        onPress={onOpenNavigation}
      >
        <Menu size={19} aria-hidden="true" />
      </Button>

      <div role="search" className="min-w-0 flex-1 basis-40 md:max-w-md">
        <SearchField
          aria-label={isFa ? "جست‌وجوی جلسه‌ها" : "Search meetings"}
          value={query}
          onChange={setQuery}
          onSubmit={submitSearch}
          isDisabled={!workspace}
          variant="secondary"
          fullWidth
        >
          <SearchField.Group>
            <SearchField.SearchIcon />
            <SearchField.Input
              placeholder={isFa ? "جست‌وجوی جلسه‌ها و متن‌ها" : "Search meetings and transcripts"}
            />
            <SearchField.ClearButton aria-label={isFa ? "پاک کردن جست‌وجو" : "Clear search"} />
          </SearchField.Group>
        </SearchField>
      </div>

      <div className="ms-auto flex min-w-0 items-center gap-2">
        {workspaces.length > 1 ? (
          <WorkspaceSelect />
        ) : (
          <span className="hidden max-w-44 truncate text-sm text-muted md:inline">{workspace?.name}</span>
        )}
        <Button
          variant="ghost"
          size="sm"
          onPress={() => setLang(isFa ? "en" : "fa")}
          aria-label={isFa ? "Switch to English" : "تغییر به فارسی"}
        >
          <Globe size={14} aria-hidden="true" />
          <span>{isFa ? "EN" : "FA"}</span>
        </Button>
        <div className="flex min-w-0 items-center gap-2">
          <Avatar size="sm" color="accent" variant="soft">
            <Avatar.Fallback>{initial}</Avatar.Fallback>
          </Avatar>
          <span className="hidden max-w-36 truncate text-sm text-foreground md:inline">{accountName}</span>
        </div>
      </div>
    </header>
  );
}
