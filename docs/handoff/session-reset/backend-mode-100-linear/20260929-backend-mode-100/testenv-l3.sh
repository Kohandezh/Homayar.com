# Lane 3: isolated PostgreSQL cluster (port 5435), Redis databases and temp dirs (parallel teams must never share them).
source /root/foreman/20260929-backend-mode-100/testenv.sh
export LANE=3
export DATABASE_URL=postgresql+psycopg://homayar_test:ci-only-postgres@127.0.0.1:5435/homayar_test
export TEST_DATABASE_URL=$DATABASE_URL
export REDIS_URL=redis://127.0.0.1:6379/6 TEST_REDIS_URL=redis://127.0.0.1:6379/7
export STORAGE_LOCAL_ROOT=/tmp/homayar-l3-objects UPLOAD_DIR=/tmp/homayar-l3-uploads UPLOAD_QUARANTINE_DIR=/tmp/homayar-l3-quarantine
export LANE_LOCK=/root/foreman/20260929-backend-mode-100/lane-3.lock
