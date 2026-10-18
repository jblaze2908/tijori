#!/usr/bin/env bash
# The whole pre-commit check: api tests, web typecheck and build.
#   scripts/check.sh          offline: the database tests skip
#   scripts/check.sh --db     also against a throwaway, migrated Postgres 17 (RLS, the owner lock, real tokens)
#   scripts/check.sh api|web  one side only (combine with --db for api)
# Uses uv and pnpm when they are installed, otherwise the same pinned images as deploy/Dockerfile, so it
# runs on any machine with Docker. Nothing is written into the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_IMAGE="python:3.12-slim"
NODE_IMAGE="node:22.16.0-alpine"
PG_IMAGE="postgres:17.11"
DB=0 SIDES=()
for arg in "$@"; do
  case "$arg" in
    --db) DB=1 ;;
    api | web) SIDES+=("$arg") ;;
    *) echo "usage: scripts/check.sh [--db] [api] [web]" >&2; exit 2 ;;
  esac
done
[[ ${#SIDES[@]} -gt 0 ]] || SIDES=(api web)
has() { command -v "$1" >/dev/null 2>&1; }
want() { [[ " ${SIDES[*]} " == *" $1 "* ]]; }
step() { printf '\n== %s\n' "$*"; }

NET="" PG=""
cleanup() {
  [[ -n "$PG" ]] && docker rm -f "$PG" >/dev/null 2>&1 || true
  [[ -n "$NET" ]] && docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Throwaway Postgres on tmpfs: native runs reach it on a loopback port, containers by name on a private network.
start_db() {
  local tag; tag="tijori-check-$$"
  NET="$tag" PG="$tag-pg"
  docker network create "$NET" >/dev/null
  docker run -d --rm --name "$PG" --network "$NET" -p 127.0.0.1::5432 --tmpfs /var/lib/postgresql/data \
    -v "$ROOT/deploy/postgres/init-app-role.sh:/docker-entrypoint-initdb.d/init-app-role.sh:ro" \
    -e POSTGRES_DB=tijori -e POSTGRES_USER=tijori_owner -e POSTGRES_PASSWORD=check-only \
    -e TIJORI_APP_DB_PASSWORD=check-only-app "$PG_IMAGE" >/dev/null
  # Over TCP: during first-boot init Postgres listens on its socket only, so this waits for init to finish.
  for _ in $(seq 60); do
    docker exec "$PG" pg_isready -q -h 127.0.0.1 -U tijori_owner -d tijori 2>/dev/null && return 0
    sleep 0.5
  done
  echo "postgres did not come up" >&2; exit 1
}

# Migrate as the owner, then run the suite as the RLS-bound app role (created as in deploy) and the owner.
API_SCRIPT='set -e
python -m unittest discover -s tests
if [ -n "${CHECK_DB_HOST:-}" ]; then
  owner="postgresql+psycopg://tijori_owner:check-only@$CHECK_DB_HOST/tijori"
  TIJORI_ADMIN_DATABASE_URL="$owner" python -m alembic upgrade head
  echo "-- against the migrated database"
  TIJORI_TEST_ADMIN_DATABASE_URL="$owner" \
  TIJORI_TEST_DATABASE_URL="postgresql+psycopg://tijori_app:check-only-app@$CHECK_DB_HOST/tijori" \
    python -m unittest discover -s tests
fi'

check_api() {
  local host=""
  if [[ $DB == 1 ]]; then start_db; fi
  if has uv; then
    step "api: tests (uv)"
    [[ $DB == 1 ]] && host="127.0.0.1:$(docker port "$PG" 5432/tcp | head -1 | sed 's/.*://')"
    (cd "$ROOT/api" && uv sync --frozen -q && CHECK_DB_HOST="$host" uv run --frozen sh -c "$API_SCRIPT")
  else
    step "api: tests ($PY_IMAGE, no uv here)"
    [[ $DB == 1 ]] && host="$PG:5432"
    docker run --rm ${NET:+--network "$NET"} -v "$ROOT/api:/src:ro" -v tijori-check-pip:/root/.cache/pip \
      -e PYTHONDONTWRITEBYTECODE=1 -e CHECK_DB_HOST="$host" -w /src "$PY_IMAGE" sh -c \
      "pip install -q --root-user-action=ignore --disable-pip-version-check --require-hashes --no-deps \
         -r requirements.lock.txt && $API_SCRIPT"
  fi
}

check_web() {
  if has pnpm; then
    step "web: typecheck and build (pnpm)"
    (cd "$ROOT/web" && pnpm install --frozen-lockfile --ignore-scripts --silent && pnpm run typecheck && pnpm run build)
  else
    step "web: typecheck and build ($NODE_IMAGE, no pnpm here)"
    # Copied in the container, so node_modules and dist never land in the checkout.
    docker run --rm -v "$ROOT/web:/src:ro" -v tijori-check-pnpm:/pnpm-store "$NODE_IMAGE" sh -c \
      'set -e; mkdir /w && cd /src && tar --exclude=node_modules --exclude=dist -cf - . | tar -xf - -C /w && cd /w
       corepack enable >/dev/null && pnpm config set store-dir /pnpm-store >/dev/null
       pnpm install --frozen-lockfile --ignore-scripts --silent && pnpm run typecheck && pnpm run build'
  fi
}

want api && check_api
want web && check_web
step "ok"
