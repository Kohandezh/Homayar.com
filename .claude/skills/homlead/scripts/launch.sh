#!/bin/bash
# Start one foreus mission in a new Herdr tab.
# Usage: launch.sh <TICKET> "<prompt that starts with /foreus TICKET>"
# If the first prompt never starts a turn, close the tab and launch once more.
LAUNCH_CHECK_SECS=${LAUNCH_CHECK_SECS:-60}
LAUNCH_POLL_SECS=${LAUNCH_POLL_SECS:-2}
LAUNCH_CALL_SECS=${LAUNCH_CALL_SECS:-15}
T="$1"; P="$2"
[ -n "$T" ] && [ -n "$P" ] || { echo "usage: launch.sh <TICKET> <prompt>"; exit 2; }
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

# hr <seconds> <herdr args...>: run herdr, killed after <seconds> so no call can hang.
hr() { local s=$1; shift; perl -e 'alarm shift; exec @ARGV' "$s" herdr "$@"; }

# One launch attempt. Prints the pane line, then the status on success.
# Sets pane and tab for the caller; return 1 when the prompt was never taken.
attempt() {
  local out s screen start check_start deadline resent entered
  pane=none; tab=none
  out=$(hr "$LAUNCH_CALL_SECS" tab create --workspace "$HERDR_WORKSPACE_ID" --cwd "$PWD" --label "$T" --no-focus 2>/dev/null) || return 1
  # An id that could not be read is none, never an empty string.
  tab=$(printf '%s\n' "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["tab"]["tab_id"])' 2>/dev/null) || tab=none
  pane=$(printf '%s\n' "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])' 2>/dev/null) || pane=none
  [ -n "$tab" ] || tab=none
  [ -n "$pane" ] || pane=none
  [ "$tab" != none ] && [ "$pane" != none ] || return 1
  echo "$T pane $pane"
  hr "$LAUNCH_CALL_SECS" pane run "$pane" "claudepersonal" >/dev/null 2>&1
  # Wait for the agent to be detected, at most 80 s.
  start=$SECONDS
  while [ $((SECONDS - start)) -lt 80 ]; do
    hr "$LAUNCH_CALL_SECS" agent get "$pane" >/dev/null 2>&1 && break
    sleep 2
  done
  hr 70 agent wait "$pane" --timeout 60000 >/dev/null 2>&1
  sleep 3
  hr "$LAUNCH_CALL_SECS" agent prompt "$pane" "$P" >/dev/null 2>&1
  # Check that the prompt started a turn.
  check_start=$SECONDS
  deadline=$((SECONDS + LAUNCH_CHECK_SECS))
  resent=0; entered=0
  s=""
  while [ $SECONDS -lt $deadline ]; do
    s=$(hr "$LAUNCH_CALL_SECS" agent get "$pane" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("result",{}).get("agent",{}).get("agent_status") or "")' 2>/dev/null)
    if [ "$s" = working ]; then echo "$s"; return 0; fi
    if ! screen=$(hr "$LAUNCH_CALL_SECS" pane read "$pane" --source visible 2>/dev/null); then
      sleep "$LAUNCH_POLL_SECS"
      continue
    fi
    if printf '%s\n' "$screen" | grep -F '❯ Try "' >/dev/null 2>&1; then
      # The prompt never landed. Send it once more, past half of the window.
      if [ "$resent" -eq 0 ] && [ $((SECONDS - check_start)) -ge $((LAUNCH_CHECK_SECS / 2)) ]; then
        hr "$LAUNCH_CALL_SECS" agent prompt "$pane" "$P" >/dev/null 2>&1
        resent=1
      fi
    elif [ -n "$s" ] && [ "$s" != idle ]; then
      echo "$s"; return 0
    elif [ "$s" = idle ]; then
      # The text sits in the input box but Enter was lost. Press Enter once.
      if [ "$entered" -eq 0 ]; then
        hr "$LAUNCH_CALL_SECS" agent send-keys "$pane" enter >/dev/null 2>&1
        entered=1
      fi
    fi
    sleep "$LAUNCH_POLL_SECS"
  done
  return 1
}

if attempt; then exit 0; fi
echo "launch.sh: $T: first prompt not taken in pane $pane within ${LAUNCH_CHECK_SECS}s; closing tab $tab and launching again" >&2
[ "$tab" != none ] && hr "$LAUNCH_CALL_SECS" tab close "$tab" >/dev/null 2>&1
if attempt; then exit 0; fi
echo "launch.sh: $T: first prompt not taken in pane $pane after 2 tries; tab $tab left open for inspection" >&2
exit 1
