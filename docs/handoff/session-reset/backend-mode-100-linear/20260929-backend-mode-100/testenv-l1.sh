# Lane 1: isolated PostgreSQL cluster (port 5433), Redis databases and temp dirs (parallel teams must never share them).
source /root/foreman/20260929-backend-mode-100/testenv.sh
export LANE=1
export DATABASE_URL=postgresql+psycopg://homayar_test:ci-only-postgres@127.0.0.1:5433/homayar_test
export TEST_DATABASE_URL=$DATABASE_URL
export REDIS_URL=redis://127.0.0.1:6379/2 TEST_REDIS_URL=redis://127.0.0.1:6379/3
export STORAGE_LOCAL_ROOT=/tmp/homayar-l1-objects UPLOAD_DIR=/tmp/homayar-l1-uploads UPLOAD_QUARANTINE_DIR=/tmp/homayar-l1-quarantine
export LANE_LOCK=/root/foreman/20260929-backend-mode-100/lane-1.lock
