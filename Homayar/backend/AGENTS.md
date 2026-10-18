# Backend API

FastAPI app with Celery workers, PostgreSQL, and Redis. The Python project root is `Homayar/` (`pyproject.toml`, `uv.lock`, `alembic.ini`).

## Commands (run in `Homayar/`, use uv)

- `uv run pytest backend/tests --quiet -rs`
- `make backend` starts the API on port 8001. `make help` lists the rest.

## Boundaries

- Work only in `Homayar/backend/`. Do not read `website/` or the root `src/`.
- Read `Homayar/frontend/` only when an API change needs its console caller updated in the same PR.
