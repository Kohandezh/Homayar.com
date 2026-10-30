#!/bin/bash
# Test launch.sh against a fake herdr. Runs 9 cases, about two minutes.
# Usage: bash test-launch.sh (from any folder)
set -u
TMP=$(mktemp -d)
trap '[ -f "$TMP/hang-pids" ] && kill $(cat "$TMP/hang-pids") 2>/dev/null; rm -rf "$TMP"' EXIT
LAUNCH="$(dirname "$0")/launch.sh"

cat > "$TMP/herdr" <<'STUB'
#!/bin/bash
# Fake herdr. State lives in $STUB_DIR.
d=$STUB_DIR
echo "$*" >> "$d/log"
if [ "$1" = tab ] && [ "$2" = create ]; then
  if [ "$STUB_BADJSON_ONCE" = 1 ] && [ ! -f "$d/badjson" ]; then
    echo done > "$d/badjson"
    echo '{"result":{}}'
    exit 0
  fi
  n=$(cat "$d/count")
  echo $((n + 1)) > "$d/count"
  printf '{"result":{"tab":{"tab_id":"wT:t%s"},"root_pane":{"pane_id":"wT:p%s"}}}\n' "$n" "$n"
  exit 0
fi
if [ "$1" = agent ] && [ "$2" = get ]; then
  if [ "$(cat "$d/state-$3" 2>/dev/null)" = taken ]; then
    echo '{"result":{"agent":{"agent_status":"working"}}}'
  else
    echo '{"result":{"agent":{"agent_status":"idle"}}}'
  fi
  exit 0
fi
if [ "$1" = pane ] && [ "$2" = read ]; then
  state=$(cat "$d/state-$3" 2>/dev/null)
  if [ "$state" = taken ]; then
    printf 'Claude Code\nworking on the task\n'
  elif [ "$state" = typed ]; then
    printf '────────────────────\n❯ /foreus HOM-1 do the task\n────────────────────\n'
  elif [ "$state" = dialog ]; then
    printf 'Do you trust the files in this folder?\n❯ 1. Yes, proceed\n  2. No, exit\n'
  else
    printf 'Claude Code\n────────────────────\n❯ Try "fix lint errors"\n────────────────────\n'
  fi
  exit 0
fi
if [ "$1" = agent ] && [ "$2" = prompt ]; then
  case " $STUB_STUCK " in *" $3 "*) echo $$ >> "$d/hang-pids"; exec sleep 600 ;; esac
  echo "prompt $(date +%s)" >> "$d/times"
  case " $STUB_DIALOG " in *" $3 "*) echo dialog > "$d/state-$3"; exit 0 ;; esac
  case " $STUB_NOENTER " in *" $3 "*) echo typed > "$d/state-$3"; exit 0 ;; esac
  echo taken > "$d/state-$3"
  exit 0
fi
if [ "$1" = agent ] && [ "$2" = send-keys ]; then
  echo "enter $(date +%s)" >> "$d/times"
  if [ "$(cat "$d/state-$3" 2>/dev/null)" = typed ]; then
    lost=0
    for item in $STUB_LOST_ENTERS; do
      case "$item" in "$3="*) lost=${item#*=} ;; esac
    done
    gone=$(cat "$d/lost-$3" 2>/dev/null || echo 0)
    if [ "$gone" -lt "$lost" ]; then
      echo $((gone + 1)) > "$d/lost-$3"
    else
      echo taken > "$d/state-$3"
    fi
  fi
  exit 0
fi
if [ "$1" = agent ] && [ "$2" = rename ]; then
  if [ "$STUB_RENAME_FAIL" = 1 ]; then
    echo "stub: rename failed" >&2
    exit 1
  fi
  echo '{"result":{"type":"ok"}}'
  exit 0
fi
exit 0
STUB
chmod +x "$TMP/herdr"
# Warm up: the first run of a new script file takes about 1 s on macOS, which could pass the 2 s call limit.
STUB_DIR="$TMP" "$TMP/herdr" warmup >/dev/null 2>&1

export PATH="$TMP:$PATH"
# Safety guard: never run a case unless the fake herdr is the one on PATH.
if [ "$(command -v herdr)" != "$TMP/herdr" ]; then
  echo "FAIL setup: herdr is not the fake"
  exit 2
fi
export STUB_DIR="$TMP"
export HERDR_WORKSPACE_ID=wT
export LAUNCH_CHECK_SECS=6
export LAUNCH_POLL_SECS=1
export LAUNCH_CALL_SECS=2
export LAUNCH_ENTER_GAP_SECS=2
export LAUNCH_ENTER_MAX=3

reset_stub() {
  echo 1 > "$TMP/count"
  rm -f "$TMP"/state-* "$TMP"/badjson "$TMP"/times "$TMP"/lost-*
  : > "$TMP/log"
}

# 45 s limit, the same alarm pattern launch.sh uses, so a hung launch.sh fails the case instead of stalling the test.
run_launch() { perl -e 'alarm shift; exec @ARGV' 45 "$LAUNCH" HOM-1 "/foreus HOM-1 do the task" > "$TMP/out" 2> "$TMP/err"; echo $?; }

fail_line() { # <name> <rc> <stdout> <stderr>
  if [ "$2" = 142 ]; then
    echo "FAIL $1: hung rc=$2 stdout=[$3] stderr=[$4]"
  else
    echo "FAIL $1: rc=$2 stdout=[$3] stderr=[$4]"
  fi
}

# renamed_before_prompt <pane>: the rename line is in the log and comes before the first prompt line.
renamed_before_prompt() {
  local rp pp
  rp=$(grep -n "^agent rename $1 foreman-hom1$" "$TMP/log"); rp=${rp%%:*}
  pp=$(grep -n "^agent prompt $1 " "$TMP/log"); pp=${pp%%:*}
  [ -n "$rp" ] && [ -n "$pp" ] && [ "$rp" -lt "$pp" ]
}

# times_ok_gap: every enter in times comes at least 1 s after the event before it.
times_ok_gap() {
  local prev="" kind t line
  [ -s "$TMP/times" ] || return 1
  while read -r line; do
    kind=${line%% *}; t=${line##* }
    if [ "$kind" = enter ]; then
      [ -n "$prev" ] && [ $((t - prev)) -ge 1 ] || return 1
    fi
    prev=$t
  done < "$TMP/times"
}

fails=0

# Case 1: normal
reset_stub
export STUB_STUCK="" STUB_NOENTER="" STUB_BADJSON_ONCE="" STUB_DIALOG="" STUB_LOST_ENTERS="" STUB_RENAME_FAIL=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] && [ -z "$err" ] \
   && ! grep -q '^tab close' "$TMP/log" \
   && grep -q '^agent rename wT:p1 foreman-hom1$' "$TMP/log" \
   && renamed_before_prompt wT:p1; then
  echo "PASS normal"
else
  fail_line normal "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 2: retry
reset_stub
export STUB_STUCK="wT:p1" STUB_NOENTER="" STUB_BADJSON_ONCE="" STUB_DIALOG="" STUB_LOST_ENTERS="" STUB_RENAME_FAIL=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nHOM-1 pane wT:p2\nworking' ] \
   && grep -q '^tab close wT:t1$' "$TMP/log" \
   && grep -q '^agent rename wT:p2 foreman-hom1$' "$TMP/log" \
   && [[ $err == *HOM-1* ]] && [[ $err == *wT:p1* ]]; then
  echo "PASS retry"
else
  fail_line retry "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 3: both-fail
reset_stub
export STUB_STUCK="wT:p1 wT:p2" STUB_NOENTER="" STUB_BADJSON_ONCE="" STUB_DIALOG="" STUB_LOST_ENTERS="" STUB_RENAME_FAIL=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 1 ] && [ "$out" = $'HOM-1 pane wT:p1\nHOM-1 pane wT:p2' ] \
   && [[ $err == *HOM-1* ]] && [[ $err == *wT:p2* ]] \
   && grep -q '^tab close wT:t1$' "$TMP/log" && ! grep -q '^tab close wT:t2$' "$TMP/log"; then
  echo "PASS both-fail"
else
  fail_line both-fail "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 4: enter-lost
reset_stub
export STUB_STUCK="" STUB_NOENTER="wT:p1" STUB_BADJSON_ONCE="" STUB_DIALOG="" STUB_LOST_ENTERS="" STUB_RENAME_FAIL=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] && [ -z "$err" ] \
   && [ "$(grep -c '^agent send-keys wT:p1 enter$' "$TMP/log")" = 1 ] \
   && ! grep -q '^tab close' "$TMP/log" \
   && grep -q '^agent rename wT:p1 foreman-hom1$' "$TMP/log" \
   && times_ok_gap; then
  echo "PASS enter-lost"
else
  fail_line enter-lost "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 5: bad-json
reset_stub
export STUB_STUCK="" STUB_NOENTER="" STUB_BADJSON_ONCE=1 STUB_DIALOG="" STUB_LOST_ENTERS="" STUB_RENAME_FAIL=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] \
   && [[ $err == *"pane none"* ]] && [[ $err == *"tab none"* ]] \
   && ! grep -q '^tab close' "$TMP/log"; then
  echo "PASS bad-json"
else
  fail_line bad-json "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 6: enter-lost-twice (the ticket's case; two Enters need the longer window)
reset_stub
export STUB_STUCK="" STUB_NOENTER="wT:p1" STUB_BADJSON_ONCE="" STUB_DIALOG="" STUB_LOST_ENTERS="wT:p1=1" STUB_RENAME_FAIL=""
LAUNCH_CHECK_SECS=10
rc=$(run_launch)
LAUNCH_CHECK_SECS=6
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] && [ -z "$err" ] \
   && [ "$(grep -c '^agent send-keys wT:p1 enter$' "$TMP/log")" = 2 ] \
   && [ "$(grep -c '^tab create' "$TMP/log")" = 1 ] \
   && ! grep -q '^tab close' "$TMP/log" \
   && grep -q '^agent rename wT:p1 foreman-hom1$' "$TMP/log" \
   && times_ok_gap; then
  echo "PASS enter-lost-twice"
else
  fail_line enter-lost-twice "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 7: dialog-no-enter (a dialog option also starts with ❯, so no Enter may be sent)
reset_stub
export STUB_STUCK="" STUB_NOENTER="" STUB_BADJSON_ONCE="" STUB_DIALOG="wT:p1" STUB_LOST_ENTERS="" STUB_RENAME_FAIL=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nHOM-1 pane wT:p2\nworking' ] \
   && ! grep -q '^agent send-keys wT:p1' "$TMP/log" \
   && grep -q '^tab close wT:t1$' "$TMP/log"; then
  echo "PASS dialog-no-enter"
else
  fail_line dialog-no-enter "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 8: enter-cap (needs the longer window to fit 3 Enters)
reset_stub
export STUB_STUCK="" STUB_NOENTER="wT:p1" STUB_BADJSON_ONCE="" STUB_DIALOG="" STUB_LOST_ENTERS="wT:p1=99" STUB_RENAME_FAIL=""
LAUNCH_CHECK_SECS=10
rc=$(run_launch)
LAUNCH_CHECK_SECS=6
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] \
   && [ "$(grep -c '^agent send-keys wT:p1 enter$' "$TMP/log")" = 3 ] \
   && grep -q '^tab close wT:t1$' "$TMP/log" \
   && [ "$out" = $'HOM-1 pane wT:p1\nHOM-1 pane wT:p2\nworking' ]; then
  echo "PASS enter-cap"
else
  fail_line enter-cap "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 9: rename-fail
reset_stub
export STUB_STUCK="" STUB_NOENTER="" STUB_BADJSON_ONCE="" STUB_DIALOG="" STUB_LOST_ENTERS="" STUB_RENAME_FAIL=1
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] \
   && [[ $err == *foreman-hom1* ]] && [[ $err == *wT:p1* ]] \
   && ! grep -q '^tab close' "$TMP/log"; then
  echo "PASS rename-fail"
else
  fail_line rename-fail "$rc" "$out" "$err"; fails=$((fails+1))
fi

[ "$fails" -eq 0 ] || exit 1
exit 0
