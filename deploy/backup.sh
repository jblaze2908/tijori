#!/usr/bin/env bash
set -euo pipefail

# Nightly off-site backup: a database dump and the stored files (already sealed by Tijori), encrypted again by
# restic and sent to Google Drive through rclone. The rclone remote uses the drive.file scope, so it can only see
# what it created. Run by deploy/tijori-backup.timer; restore steps are in docs/deploy.md.
DEPLOY_DIR="${DEPLOY_DIR:-/opt/tijori}"
ENV_FILE="${TIJORI_ENV_FILE:-/etc/tijori/tijori.env}"
BACKUP_DIR="${TIJORI_BACKUP_DIR:-/var/backups/tijori}"
export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-rclone:tijori-drive:tijori-backup}"
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/etc/tijori/restic.pass}"

cd "$DEPLOY_DIR"
install -d -m 700 "$BACKUP_DIR"
compose() { docker compose -p tijori -f deploy/compose.yml --env-file "$ENV_FILE" "$@"; }
blobs="$(docker volume inspect tijori_blobs --format '{{.Mountpoint}}')"
dump="$BACKUP_DIR/nightly.dump"

compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$dump.part"
mv "$dump.part" "$dump"
chmod 600 "$dump"

restic cat config >/dev/null 2>&1 || restic init
restic backup --host host --tag nightly "$dump" "$blobs"
restic forget --host host --tag nightly --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune

# On the 1st (or VERIFY=1): prove it restores. The dump must list its table data, and a sample of the
# repository is read back and checked.
if [[ "$(date +%d)" == "01" || "${VERIFY:-0}" == "1" ]]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  restic restore latest --host host --target "$tmp" --include "$dump"
  tables="$(compose exec -T db pg_restore --list <"$tmp$dump" | grep -c 'TABLE DATA')"
  [[ "$tables" -gt 20 ]] || { echo "restored dump lists only $tables tables" >&2; exit 1; }
  restic check --read-data-subset=5%
  echo "restore check ok: $tables tables"
fi
