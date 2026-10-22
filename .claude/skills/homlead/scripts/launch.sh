#!/bin/bash
# Start one foreus mission in a new Herdr tab.
# Usage: launch.sh <TICKET> "<prompt that starts with /foreus TICKET>"
set -e
T="$1"; P="$2"
[ -n "$T" ] && [ -n "$P" ] || { echo "usage: launch.sh <TICKET> <prompt>"; exit 2; }
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
out=$(herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd "$PWD" --label "$T" --no-focus)
pane=$(echo "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
echo "$T pane $pane"
herdr pane run "$pane" "claudepersonal" >/dev/null
for i in $(seq 1 40); do herdr agent get "$pane" >/dev/null 2>&1 && break; sleep 2; done
herdr agent wait "$pane" --timeout 60000 >/dev/null 2>&1 || true
sleep 3
status() { herdr agent get "$pane" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["agent"]["agent_status"])'; }
herdr agent prompt "$pane" "$P" >/dev/null
herdr agent wait "$pane" --until working --timeout 20000 >/dev/null 2>&1 || true
# Sometimes the text lands in the input box but Enter is lost, or nothing lands.
# Press Enter once, then send the prompt once more.
if [ "$(status)" = idle ]; then
  herdr agent send-keys "$pane" enter >/dev/null
  herdr agent wait "$pane" --until working --timeout 15000 >/dev/null 2>&1 || true
fi
if [ "$(status)" = idle ]; then
  herdr agent prompt "$pane" "$P" >/dev/null
  herdr agent wait "$pane" --until working --timeout 20000 >/dev/null 2>&1 || true
fi
status
