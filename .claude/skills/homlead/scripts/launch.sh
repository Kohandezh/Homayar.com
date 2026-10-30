#!/bin/bash
# Start one foreus mission in a new Herdr tab.
# Usage: launch.sh <TICKET> "<prompt that starts with /foreus TICKET>"
# If the first prompt never starts a turn, close the tab and launch once more.
LAUNCH_CHECK_SECS=${LAUNCH_CHECK_SECS:-60}
LAUNCH_POLL_SECS=${LAUNCH_POLL_SECS:-2}
LAUNCH_CALL_SECS=${LAUNCH_CALL_SECS:-15}
LAUNCH_ENTER_GAP_SECS=${LAUNCH_ENTER_GAP_SECS:-5}
LAUNCH_ENTER_MAX=${LAUNCH_ENTER_MAX:-3}
T="$1"; P="$2"
[ -n "$T" ] && [ -n "$P" ] || { echo "usage: launch.sh <TICKET> <prompt>"; exit 2; }
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

# hr <seconds> <herdr args...>: run herdr, killed after <seconds> so no call can hang.
hr() { local s=$1; shift; perl -e 'alarm shift; exec @ARGV' "$s" herdr "$@"; }

# holds_text <screen>: true when the input box holds text, that is a line that
# starts with "❯ " plus a non-space character (and not the empty ❯ Try "
# prompt), directly under a line made only of ─ characters.
holds_text() {
  printf '%s\n' "$1" | python3 -c '
import sys
lines = sys.stdin.buffer.read().decode("utf-8", "replace").split("\n")
for i, l in enumerate(lines):
    l = l.rstrip()
    if l.startswith("\u276f ") and len(l) > 2 and l[2] != " " and not l.startswith("\u276f Try \""):
        above = lines[i - 1].rstrip() if i > 0 else ""
        if above and set(above) == {"\u2500"}:
            print("yes")
            break
' | grep -q '^yes$'
}

# One launch attempt. Prints the pane line, then the status on success.
# Sets pane and tab for the caller; return 1 when the prompt was never taken.
attempt() {
  local out s screen start check_start deadline resent enters last_send name
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
  # Name the agent before the first prompt. A failed name never fails the launch.
  name="foreman-$(printf '%s' "$T" | tr '[:upper:]' '[:lower:]' | tr -d '-')"
  if [[ $name =~ ^[a-z][a-z0-9_-]{0,31}$ ]]; then
    hr "$LAUNCH_CALL_SECS" agent rename "$pane" "$name" >/dev/null 2>&1 \
      || echo "launch.sh: $T: could not name the agent $name in pane $pane; going on" >&2
  else
    echo "launch.sh: $T: could not name the agent $name in pane $pane; going on" >&2
  fi
  hr "$LAUNCH_CALL_SECS" agent prompt "$pane" "$P" >/dev/null 2>&1
  last_send=$SECONDS
  # Check that the prompt started a turn.
  check_start=$SECONDS
  deadline=$((SECONDS + LAUNCH_CHECK_SECS))
  resent=0; enters=0
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
        last_send=$SECONDS
      fi
    elif [ -n "$s" ] && [ "$s" != idle ]; then
      echo "$s"; return 0
    elif [ "$s" = idle ]; then
      # The text sits in the input box but the Enter was lost. Press Enter again,
      # only when the box holds text, at least LAUNCH_ENTER_GAP_SECS s after the
      # last send, and at most LAUNCH_ENTER_MAX times in this attempt.
      if [ "$enters" -lt "$LAUNCH_ENTER_MAX" ] \
         && [ $((SECONDS - last_send)) -ge "$LAUNCH_ENTER_GAP_SECS" ] \
         && holds_text "$screen"; then
        hr "$LAUNCH_CALL_SECS" agent send-keys "$pane" enter >/dev/null 2>&1
        enters=$((enters + 1))
        last_send=$SECONDS
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
