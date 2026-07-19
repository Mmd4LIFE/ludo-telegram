#!/bin/sh
# Container entrypoint: wait for Postgres, run migrations, seed, then serve.
set -e

echo "Waiting for database at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
python - <<'PY'
import os, time
import psycopg2
host = os.environ.get("POSTGRES_HOST", "db")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
user = os.environ.get("POSTGRES_USER", "ludo")
pw = os.environ.get("POSTGRES_PASSWORD", "ludo_secret")
db = os.environ.get("POSTGRES_DB", "ludo")
for i in range(30):
    try:
        psycopg2.connect(host=host, port=port, user=user, password=pw, dbname=db).close()
        print("database is up")
        break
    except Exception as e:  # noqa: BLE001
        print(f"  ({i+1}/30) db not ready: {e}")
        time.sleep(2)
else:
    raise SystemExit("database never became available")
PY

echo "Running migrations..."
alembic upgrade head

echo "Seeding..."
python -m app.seed || echo "seed skipped/failed (non-fatal)"

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
