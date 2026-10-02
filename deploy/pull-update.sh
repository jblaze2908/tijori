#!/usr/bin/env bash
set -euo pipefail

# Pull-based deploy, same model as nullframe: the server pulls with a read-only deploy key,
# so nothing outside host ever holds server access. Run by deploy/tijori.timer every 2 min.
# Under scale0 a stopped api is asleep, not down: the rollout goes through `scale0 restart tijori`
# and the health check through the address scale0 holds, which wakes it. db and worker stay always on.
DEPLOY_DIR="${DEPLOY_DIR:-/opt/tijori}"
ENV_FILE="${TIJORI_ENV_FILE:-/etc/tijori/tijori.env}"
BRANCH="${DEPLOY_BRANCH:-main}"
STATE_DIR="${TIJORI_STATE_DIR:-/var/lib/tijori}"
BACKUP_DIR="${TIJORI_BACKUP_DIR:-/var/backups/tijori}"
HEALTH_URL="${TIJORI_HEALTH_URL:-http://172.17.0.1:8310/health}"
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -i /root/.ssh/tijori_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes}"

# A build can outlast the 2-min timer interval; overlapping runs would race on checkout.
# Root-only: pre-deploy DB dumps hold personal financial data.
install -d -m 700 "$STATE_DIR" "$BACKUP_DIR"
exec 9>"$STATE_DIR/deploy.lock"
flock -n 9 || exit 0

compose() { docker compose -p tijori -f deploy/compose.yml --env-file "$ENV_FILE" "$@"; }
scaled() { command -v scale0 >/dev/null && scale0 managed tijori; }
# Start the stack on the checked-out code: all of it directly, or db + worker directly and the api
# through scale0 so it can sleep again (waking it runs migrate first, as `up` always has).
start_app() {
  if scaled; then
    compose build && compose up --detach --remove-orphans db worker && scale0 restart tijori
  else
    compose up --detach --build --remove-orphans
  fi
}

notify() {
  # Optional ops alerts; NTFY_* live in the root-only env file and are never echoed.
  local url token
  url="$(sed -n 's/^NTFY_URL=//p' "$ENV_FILE" | tail -1)"
  token="$(sed -n 's/^NTFY_OPS_TOKEN=//p' "$ENV_FILE" | tail -1)"
  [[ -n "$url" && -n "$token" ]] || return 0
  curl -fsS -m 10 -H "Authorization: Bearer $token" -H "Title: tijori deploy" -d "$1" "$url/tijori-ops" >/dev/null || true
}

cd "$DEPLOY_DIR"
checkout_commit="$(git rev-parse HEAD)"
# `deployed` moves only after build + health pass; HEAD may point at a rejected target.
deployed_commit="$(git rev-parse -q --verify refs/heads/deployed || true)"
rollback_commit="${deployed_commit:-$checkout_commit}"
git fetch --prune origin "$BRANCH"
target_commit="$(git rev-parse "origin/$BRANCH")"
short="${target_commit:0:8}"

if [[ "$deployed_commit" == "$target_commit" ]] && { scaled || compose ps --services --status running | grep -qx api; }; then
  exit 0
fi
# Don't rebuild and re-test a commit that already failed; a new push clears it.
if [[ "$(cat "$STATE_DIR/failed-commit" 2>/dev/null || true)" == "$target_commit" ]]; then
  exit 0
fi

restore_previous_release() {
  echo "Restoring $rollback_commit" >&2
  git checkout --detach "$rollback_commit"
  start_app
}

reject() {
  echo "$target_commit" >"$STATE_DIR/failed-commit"
  notify "❌ $short rejected: $1"
}

git checkout --detach "$target_commit"

# Build before touching the running stack, so a broken build never takes prod down.
if ! compose build; then
  echo "Build failed for $short" >&2
  git checkout --detach "$rollback_commit"
  reject "build failed"
  exit 1
fi

# The migrate service runs on `up` and code rollback does not undo it, so snapshot first.
if compose ps --services --status running | grep -qx db; then
  compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip >"$BACKUP_DIR/pre-$short.sql.gz"
  ls -1t "$BACKUP_DIR"/pre-*.sql.gz | tail -n +15 | xargs -r rm -f
fi

if ! start_app; then
  echo "Compose rollout failed" >&2
  restore_previous_release
  reject "compose up failed"
  exit 1
fi

ready=false
for _ in {1..24}; do
  if curl --fail --silent --show-error -m 5 "$HEALTH_URL" >/dev/null; then
    ready=true
    break
  fi
  sleep 5
done

if [[ "$ready" != true ]]; then
  echo "Health check failed" >&2
  restore_previous_release
  reject "health check failed"
  exit 1
fi

git branch --force deployed "$target_commit"
rm -f "$STATE_DIR/failed-commit"
notify "✅ deployed $short"
