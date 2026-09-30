#!/usr/bin/env bash
# Start disposable local test services (Postgres 16, Redis) for the mission. Idempotent.
set -u
PGBIN=/usr/lib/postgresql/16/bin
PGDATA=/var/lib/postgresql/homayar-mission
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  install -d -o postgres -g postgres "$PGDATA"
  su postgres -c "$PGBIN/initdb -D $PGDATA -U postgres --auth=trust" >/dev/null
fi
su postgres -c "$PGBIN/pg_ctl -D $PGDATA status" >/dev/null 2>&1 || su postgres -c "$PGBIN/pg_ctl -D $PGDATA -l $PGDATA/server.log -o '-p 5432 -k /tmp -c max_connections=200' -w start" >/dev/null
psql -h 127.0.0.1 -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='homayar_test'" | grep -q 1 || psql -h 127.0.0.1 -U postgres -qc "CREATE ROLE homayar_test LOGIN SUPERUSER PASSWORD 'ci-only-postgres';"
psql -h 127.0.0.1 -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='homayar_test'" | grep -q 1 || psql -h 127.0.0.1 -U postgres -qc "CREATE DATABASE homayar_test OWNER homayar_test;"
# Parallel lanes: one PostgreSQL cluster per lane (roles such as homayar_rls_probe are cluster-global).
for lane in 1 2 3 4; do
  LD=/var/lib/postgresql/homayar-lane$lane; LP=$((5432+lane))
  if [ ! -f "$LD/PG_VERSION" ]; then
    install -d -o postgres -g postgres "$LD"
    su postgres -c "$PGBIN/initdb -D $LD -U postgres --auth=trust" >/dev/null
  fi
  su postgres -c "$PGBIN/pg_ctl -D $LD status" >/dev/null 2>&1 || su postgres -c "$PGBIN/pg_ctl -D $LD -l $LD/server.log -o '-p $LP -k /tmp -c max_connections=120 -c shared_buffers=64MB' -w start" >/dev/null
  psql -h 127.0.0.1 -p $LP -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='homayar_test'" | grep -q 1 || psql -h 127.0.0.1 -p $LP -U postgres -qc "CREATE ROLE homayar_test LOGIN SUPERUSER PASSWORD 'ci-only-postgres';"
  psql -h 127.0.0.1 -p $LP -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='homayar_test'" | grep -q 1 || psql -h 127.0.0.1 -p $LP -U postgres -qc "CREATE DATABASE homayar_test OWNER homayar_test;"
done
redis-cli ping >/dev/null 2>&1 || (cd /tmp && redis-server --port 6379 --daemonize yes --dir /tmp) >/dev/null
echo "postgres: $(psql -h 127.0.0.1 -U homayar_test -d homayar_test -tAc 'select version()' | cut -c1-20)  redis: $(redis-cli ping)"
