# Handoff — stab01-t1, phase B, turn 2 (fix turn, F1) — stab01-t1-impl

Mission 20260929-backend-mode-100 · Linear HOM-140 · maker worktree
`/root/foreman/20260929-backend-mode-100/wt/stab01-t1` (branch `mission/stab01-t1`, base 682ffee).
Nothing was committed, and I did not run the full suite (per the brief).

## Change (F1 only)
`Homayar/backend/tests/test_voice_admission.py` is the only file changed this turn:
- Added `import threading`.
- Added `test_concurrent_threads_get_distinct_identities_and_first_delays`:
  - It starts 12 threads in one process. They are released together by `threading.Barrier(12)`.
  - Each thread calls `admission_module._retrier_identity()` after the barrier and records the
    result under a lock.
  - Every thread is alive at the barrier, so no thread id can be reused.
  - It asserts 12 identities collected, 12 distinct identities, and 12 distinct
    `_contention_backoff(1, identity)` values.

`Homayar/backend/voice/admission.py` is unchanged since turn 1. Its `git diff` hunk is byte-identical
to the turn-1 diff (checked by script: `admission.py diff identical to turn 1: True`).

Turn-2 delta against the turn-1 diff (all additions, all in the test file):
```
+import threading
...
+def test_concurrent_threads_get_distinct_identities_and_first_delays():
+    threads = 12
+    barrier = threading.Barrier(threads)
+    identities: list[str] = []
+    lock = threading.Lock()
+
+    def contend() -> None:
+        barrier.wait()  # every thread is alive at once, so no thread id is reused
+        identity = admission_module._retrier_identity()
+        with lock:
+            identities.append(identity)
+
+    workers = [threading.Thread(target=contend) for _ in range(threads)]
+    for worker in workers:
+        worker.start()
+    for worker in workers:
+        worker.join()
+    assert len(identities) == threads
+    assert len(set(identities)) == threads
+    delays = {admission_module._contention_backoff(1, identity) for identity in identities}
+    assert len(delays) == threads
```

## Mutant evidence (temporary local edit, reverted)
The steps were:
1. Back up `admission.py` (sha256 `17b83ef0…a56d0`).
2. Replace the `_retrier_identity` body with `return "constant"  # MUTANT`.
3. Run the test file under the lock.
4. Restore the backup.

```
$ grep -n "MUTANT" backend/voice/admission.py
145:    return "constant"  # MUTANT
$ flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_voice_admission.py -q -p no:cacheprovider
        workers = [threading.Thread(target=contend) for _ in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert len(identities) == threads
>       assert len(set(identities)) == threads
E       AssertionError: assert 1 == 12
E        +  where 1 = len({'constant'})
E        +    where {'constant'} = set(['constant', 'constant', 'constant', 'constant', 'constant', 'constant', ...])

backend/tests/test_voice_admission.py:388: AssertionError
=========================== short test summary info ============================
FAILED backend/tests/test_voice_admission.py::test_concurrent_threads_get_distinct_identities_and_first_delays
1 failed, 14 passed in 6.01s
```
After the restore, sha256 is `17b83ef024028e6ac023b37fb3baefe7b917a982bd85a19c65d03ffc185a56d0`
(identical to the backup), `grep -c MUTANT` gives 0, and the final diff contains no mutant.

## Verification (from `.../wt/stab01-t1/Homayar`, after `source .../testenv.sh`)
Focused, under the lock (amended SPEC A2):
```
$ flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_voice_admission.py backend/tests/test_voice_api.py -q -p no:cacheprovider
.........................                                                [100%]
25 passed, 6 warnings in 9.34s
```
That is 24 from turn 1 plus the new test. The 6 warnings are the same count as on the base.

Lint on the changed file:
```
$ uv run ruff check backend/tests/test_voice_admission.py && uv run ruff format --check backend/tests/test_voice_admission.py
All checks passed!
1 file already formatted
```

## Disclosure
I made one run without the lock:
`uv run pytest backend/tests/test_voice_admission.py -q -p no:cacheprovider -k distinct_identities`
→ `1 passed, 14 deselected in 0.10s`. It ran right after I wrote the test and before I switched to
the locked form.
- Its only contact with PostgreSQL was `pytest_sessionstart` in `backend/tests/conftest.py`, which
  opens a connection and runs the read-only `SHOW server_version`.
- The test itself does not touch PostgreSQL, and nothing created, dropped or wrote any schema or rows.
- Every later pytest run (the mutant and the focused command) held `heavy.lock`.

## Final diff scope
`git diff --stat`:
```
 Homayar/backend/tests/test_voice_admission.py | 125 ++++++++++++++++++++++++++
 Homayar/backend/voice/admission.py            |  24 ++++-
 2 files changed, 147 insertions(+), 2 deletions(-)
```
