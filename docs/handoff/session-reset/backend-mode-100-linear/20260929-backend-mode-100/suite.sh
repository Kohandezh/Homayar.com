#!/usr/bin/env bash
# Usage: [LANE=k] suite.sh <worktree> <outdir> [chunk-index]   (LANE selects testenv-l<k>.sh and lane-<k>.lock)
# Splits backend/tests/test_*.py into 4 chunks (sorted, contiguous) and runs one chunk (or all, sequentially)
# under the mission lock. Each chunk stays under ~10 min so a container restart loses at most one chunk.
set -u
WT=$1; OUT=$2; ONLY=${3:-}
mkdir -p "$OUT"; cd "$WT/Homayar" || exit 2
if [ -n "${LANE:-}" ]; then source /root/foreman/20260929-backend-mode-100/testenv-l${LANE}.sh; LOCK=$LANE_LOCK; else source /root/foreman/20260929-backend-mode-100/testenv.sh; LOCK=/root/foreman/20260929-backend-mode-100/heavy.lock; fi
ls backend/tests/test_*.py | sort > "$OUT/all-files.txt"
split -n l/4 -d -a 2 "$OUT/all-files.txt" "$OUT/chunk-"
for i in 00 01 02 03; do
  [ -n "$ONLY" ] && [ "$ONLY" != "$i" ] && continue
  flock "$LOCK" uv run pytest $(cat "$OUT/chunk-$i") -q -p no:cacheprovider -rs > "$OUT/full-$i.txt" 2>&1
  echo "chunk $i: $(tail -1 "$OUT/full-$i.txt")"
done
