#!/bin/bash
# Start the shared demo backend for homlead missions.
# Postgres in Docker on 127.0.0.1:4002, API on 127.0.0.1:4001, demo seed loaded.
# Local-only values. Never use them outside this laptop demo.
set -e
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/Homayar"
docker rm -f homayar-demo-pg >/dev/null 2>&1 || true
docker run -d --name homayar-demo-pg -e POSTGRES_DB=homayar_dev -e POSTGRES_USER=homayar_dev \
  -e POSTGRES_PASSWORD=local-only -p 127.0.0.1:4002:5432 postgres:16-alpine >/dev/null
until docker exec homayar-demo-pg pg_isready -U homayar_dev >/dev/null 2>&1; do sleep 1; done
sleep 2
export ENVIRONMENT=development DEPLOYMENT_MODE=onprem \
  DATABASE_URL=postgresql+psycopg://homayar_dev:local-only@127.0.0.1:4002/homayar_dev \
  JWT_SECRET=local-only-jwt-secret-000000000000000000000 \
  TOKEN_PEPPER=local-only-token-pepper-0000000000000000000 \
  STORAGE_BACKEND=local TASKS_EAGER=true ALLOW_MOCK_PROVIDERS=true \
  AUTO_CREATE_SCHEMA=false ENABLE_NEO4J_PROJECTION=false
uv sync -q
.venv/bin/python -m alembic upgrade head
# Pass --no-seed to keep the data when you only restart the API after a merge.
if [ "${1:-}" != "--no-seed" ]; then .venv/bin/python -m backend.database.demo_seed; fi
exec .venv/bin/uvicorn backend.main:app --port 4001 --host 127.0.0.1
