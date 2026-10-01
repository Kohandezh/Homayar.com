#!/usr/bin/env bash
# usage: stress.sh <worktree> <outdir> [runs=8]     (PYTEST overrides the runner, default "uv run pytest")
# Loads the CPU that the drill's pool children run on, so that the task's return path is slow while the
# drill's own polling process is not: the situation of the observed failure (HOM-163).
set -u
W=$1; OUT=$2; RUNS=${3:-8}
mkdir -p "$OUT"; cd "$W/Homayar" || exit 2
source /root/foreman/20260929-backend-mode-100/testenv-l4.sh
T=backend/tests/test_transport_drills.py::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert
CPU=$(( $(nproc) - 1 )); bg=()
for _ in 1 2 3; do taskset -c "$CPU" sh -c 'while :; do :; done' & bg+=($!); done
( while :; do
    for p in $(pgrep -f 'backend.tests._transport_worker'); do
      # Parallel lanes: only this worktree's workers (other lanes' drills must not be throttled).
      case "$(readlink -f /proc/$p/cwd 2>/dev/null)" in "$(readlink -f "$W")"*) ;; *) continue ;; esac
      for c in $(pgrep -P "$p"); do taskset -a -pc "$CPU" "$c" >/dev/null 2>&1; done
    done
    sleep 0.2
  done ) & bg+=($!)
trap 'kill "${bg[@]}" 2>/dev/null' EXIT
pass=0
for i in $(seq 1 "$RUNS"); do
  flock "$LANE_LOCK" ${PYTEST:-uv run pytest} "$T" -q -p no:cacheprovider -rs > "$OUT/run-$i.txt" 2>&1 || break
  pass=$((pass + 1))
done
echo "consecutive passes: $pass of $RUNS"
[ "$pass" -eq "$RUNS" ]
