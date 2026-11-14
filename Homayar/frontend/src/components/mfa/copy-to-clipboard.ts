import { useCallback, useState } from "react";

/**
 * Copy text through the async clipboard API. The clipboard can be missing or
 * refuse (insecure origin, permission, jsdom); the value stays selectable on
 * screen either way, so the caller only needs a copied/failed flag (same
 * contract as the one-time API key in CreateApiKeyModal).
 */
export function useCopyToClipboard() {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  const copy = useCallback((value: string) => {
    setFailed(false);
    const write = navigator.clipboard?.writeText?.bind(navigator.clipboard);
    if (!write) {
      setCopied(false);
      setFailed(true);
      return;
    }
    Promise.resolve(write(value))
      .then(() => setCopied(true))
      .catch(() => {
        setCopied(false);
        setFailed(true);
      });
  }, []);

  return { copied, failed, copy };
}
