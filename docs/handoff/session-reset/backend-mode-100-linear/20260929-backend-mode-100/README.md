# Mission 20260929-backend-mode-100 — durable checkpoint

Sanitized copies of the Foreman coordination records (the live copies are in `~/foreman/20260929-backend-mode-100/`
inside the session container). Contents contain no credentials; test-only CI values are not copied.

- `ledger.md` — mission ledger: modes, Linear IDs and states, preflight evidence, work units, substitutions.
- `linear-map.tsv` — backlog key (WPxx / WPxx-Tyy) -> Linear identifier (HOM-n) for project Homayar Backend.
- `BASELINE.md` — verified local test baseline on 682ffee.
- `<team-id>/SPEC.md` — per-issue Foreman contracts.
- `QUEUE.md`, `coverage-ledger.json` — added when the planning unit is accepted.

Resume: read `ledger.md`, then the active team's SPEC and the Linear issue it names.
