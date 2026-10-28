#!/bin/bash
# Test launch.sh against a fake herdr. Runs 5 cases, about one minute.
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
  if [ -z "$(cat "$d/state-$3" 2>/dev/null)" ]; then
    printf 'Claude Code\n❯ Try "fix lint errors"\n'
  else
    printf 'Claude Code\nworking on the task\n'
  fi
  exit 0
fi
if [ "$1" = agent ] && [ "$2" = prompt ]; then
  case " $STUB_STUCK " in *" $3 "*) echo $$ >> "$d/hang-pids"; exec sleep 600 ;; esac
  case " $STUB_NOENTER " in *" $3 "*) echo typed > "$d/state-$3" ;; *) echo taken > "$d/state-$3" ;; esac
  exit 0
fi
if [ "$1" = agent ] && [ "$2" = send-keys ]; then
  if [ "$(cat "$d/state-$3" 2>/dev/null)" = typed ]; then echo taken > "$d/state-$3"; fi
  exit 0
fi
exit 0
STUB
chmod +x "$TMP/herdr"
# Warm up: the first run of a new script file takes about 1 s on macOS, which could pass the 2 s call limit.
STUB_DIR="$TMP" "$TMP/herdr" warmup >/dev/null 2>&1

export PATH="$TMP:$PATH"
export STUB_DIR="$TMP"
export HERDR_WORKSPACE_ID=wT
export LAUNCH_CHECK_SECS=6
export LAUNCH_POLL_SECS=1
export LAUNCH_CALL_SECS=2

reset_stub() {
  echo 1 > "$TMP/count"
  rm -f "$TMP"/state-* "$TMP/badjson"
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

fails=0

# Case 1: normal
reset_stub
export STUB_STUCK="" STUB_NOENTER="" STUB_BADJSON_ONCE=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] && [ -z "$err" ] \
   && ! grep -q '^tab close' "$TMP/log"; then
  echo "PASS normal"
else
  fail_line normal "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 2: retry
reset_stub
export STUB_STUCK="wT:p1" STUB_NOENTER="" STUB_BADJSON_ONCE=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nHOM-1 pane wT:p2\nworking' ] \
   && grep -q '^tab close wT:t1$' "$TMP/log" \
   && [[ $err == *HOM-1* ]] && [[ $err == *wT:p1* ]]; then
  echo "PASS retry"
else
  fail_line retry "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 3: both-fail
reset_stub
export STUB_STUCK="wT:p1 wT:p2" STUB_NOENTER="" STUB_BADJSON_ONCE=""
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
export STUB_STUCK="" STUB_NOENTER="wT:p1" STUB_BADJSON_ONCE=""
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] \
   && grep -q '^agent send-keys wT:p1 enter$' "$TMP/log" && ! grep -q '^tab close' "$TMP/log"; then
  echo "PASS enter-lost"
else
  fail_line enter-lost "$rc" "$out" "$err"; fails=$((fails+1))
fi

# Case 5: bad-json
reset_stub
export STUB_STUCK="" STUB_NOENTER="" STUB_BADJSON_ONCE=1
rc=$(run_launch)
out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
if [ "$rc" = 0 ] && [ "$out" = $'HOM-1 pane wT:p1\nworking' ] \
   && [[ $err == *"pane none"* ]] && [[ $err == *"tab none"* ]] \
   && ! grep -q '^tab close' "$TMP/log"; then
  echo "PASS bad-json"
else
  fail_line bad-json "$rc" "$out" "$err"; fails=$((fails+1))
fi

[ "$fails" -eq 0 ] || exit 1
exit 0
