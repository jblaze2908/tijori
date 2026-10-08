# Deploy

Tijori deploys itself on a single Linux host with Docker:
- A systemd timer runs `deploy/pull-update.sh` every 2 minutes.
- The script pulls `main` with a **read-only deploy key**. There are no GitHub Actions, and nothing outside the server holds access to it.
- Alternative: [scale0](https://github.com/jblaze2908/scale0)'s deployer reads `deploy/app.conf` instead, and lets the api sleep when idle.

Paths below (`/opt/tijori`, `/etc/tijori`) are the defaults the unit files and scripts use; each can be overridden by env.

## What a release does
1. `flock` makes sure only one deploy runs at a time.
2. Fetch `origin/main`. Stop if it's already deployed and healthy, or if this commit already failed.
3. **Build first.** `docker compose build`. A failed build rejects the commit, and production is left untouched. There's no test gate in the deploy.
4. `pg_dump` the live database to `/var/backups/tijori/pre-<sha>.sql.gz`, keeping the last 14. The `migrate` service runs during `up`, and a code rollback doesn't undo it, so this dump is the way back.
5. `docker compose up --build`, then poll `http://172.17.0.1:8310/health` for up to 2 minutes.
6. On failure, go back to the last `deployed` commit and mark the target as failed. On success, move the `deployed` branch.
7. Optionally, send a notification to the ntfy topic `tijori-ops` (`NTFY_URL` and `NTFY_OPS_TOKEN` in the env file).

Migrations (the one-shot `migrate` service, `alembic upgrade head`) must be **backward-compatible (expand/contract)**, because a rollback runs old code against the new schema.

## One-time setup on the server
Each step changes live infrastructure or creates a credential, so each one is confirmed before it's run.

```bash
# 1. Read-only deploy key: only the public half is added to GitHub, without write access
ssh-keygen -t ed25519 -N "" -C "tijori read-only" -f /root/.ssh/tijori_deploy
gh repo deploy-key add /root/.ssh/tijori_deploy.pub --repo <you>/tijori --title "server read-only"   # run from a machine with gh auth

# 2. Code and config
GIT_SSH_COMMAND="ssh -i /root/.ssh/tijori_deploy -o IdentitiesOnly=yes" git clone git@github.com:<you>/tijori.git /opt/tijori
install -d -m 700 /etc/tijori && install -m 600 /dev/null /etc/tijori/tijori.env   # fill in the values; names are in deploy/.env.example

# 3. Timer
cp /opt/tijori/deploy/tijori.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tijori.timer

# 4. Routing: a DNS record for your host, plus a reverse proxy (Traefik, Caddy, nginx) terminating TLS and forwarding to http://172.17.0.1:8310
```

## Useful commands
```bash
systemctl start tijori.service            # deploy now instead of waiting for the timer
journalctl -u tijori.service -n 100       # last deploy log
git -C /opt/tijori log -1 deployed        # what's live
rm /var/lib/tijori/failed-commit          # retry a rejected commit
```

## Backups
`deploy/backup.sh`, run nightly at 03:30 IST by `tijori-backup.timer` (edit `OnCalendar` for another zone):

- **What:** a `pg_dump -Fc` of the database, plus the stored-files volume. The files are already sealed by Tijori.
- **Where:** restic, which encrypts again with `/etc/tijori/restic.pass`, to Google Drive through the rclone remote `tijori-drive`. The remote uses scope `drive.file`, so it sees only the folder it created.
- **Kept:** 7 daily, 4 weekly and 12 monthly snapshots.
- **Checked:** on the 1st (or with `VERIFY=1`), the latest dump is restored and must list its tables, and 5% of the repository is read back.
- **Reported:** every run writes its result to `ops_event`. Settings → Notifications & retention shows the last good backup, and an alert (`backup_stale`) fires when there's none in 2 days.

Two secrets live outside the backup: `TIJORI_MASTER_KEY` (in `/etc/tijori/tijori.env`) and the restic password. Keep both in a password manager. Without the master key, the restored vault and files can't be read. Without the restic password, the backup can't be opened.

One-time setup (as root on the server):
```bash
apt-get install -y restic rclone
umask 077; openssl rand -base64 36 > /etc/tijori/restic.pass
rclone config    # new remote "tijori-drive", type drive, scope drive.file; authorise on a machine with a browser
cp /opt/tijori/deploy/tijori-backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tijori-backup.timer
VERIFY=1 systemctl start tijori-backup.service   # first run, with the restore check
```

Restore, on a fresh host with the two secrets and the rclone remote:
```bash
export RESTIC_REPOSITORY=rclone:tijori-drive:tijori-backup RESTIC_PASSWORD_FILE=/etc/tijori/restic.pass
restic restore latest --target /restore
# database: pg_restore -d tijori /restore/var/backups/tijori/nightly.dump
# files: copy /restore/var/lib/docker/volumes/tijori_blobs/_data into the new tijori_blobs volume
```

## Later
Woodpecker CI, and tests if they're ever reintroduced, would run on pushes and PRs.
