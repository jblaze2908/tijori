# Deploy

Tijori runs anywhere Docker runs: a laptop, a home server, or any VPS or cloud VM. One `docker compose up` starts four containers: Postgres, a one-shot migration, the api (which also serves the UI), and the mail collector worker.

## Requirements

- Docker Engine with the Compose plugin (`docker compose version`). Linux, macOS or Windows (WSL2).
- **Memory:** idle, the three long-running containers use about 210 MiB together (measured on one instance: api 83 MiB, worker 69 MiB, Postgres 57 MiB). A 1 GB machine is enough to run it; building the image (Node and Python stages) is the peak and is not measured, so give a small VM swap or build elsewhere.
- **Disk:** the api image is 367 MB, plus the official Postgres 17 image (about 0.8 GB together, estimate). Data grows with your mail: one person's instance holds 78 MB of database and 311 MB of stored statements and mail.
- A domain name and HTTPS in front of it, if you use it beyond `localhost`. Google sign-in needs an `https://` URL in production.

## 1. Get the code and configure

```bash
git clone https://github.com/<you>/tijori.git && cd tijori
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
```

Fill in `deploy/.env` (every key is commented in `deploy/.env.example`; it is gitignored):

| Key | How to get it |
|---|---|
| `TIJORI_OWNER_DB_PASSWORD`, `TIJORI_APP_DB_PASSWORD` | `openssl rand -hex 32`, twice. URL-safe, because they go into connection URLs |
| `TIJORI_MASTER_KEY` | `openssl rand -base64 32`. Seals every stored password; **keep a copy in a password manager**, without it a backup can't be read |
| `TIJORI_PUBLIC_URL` | The URL you'll open Tijori at, e.g. `https://tijori.example.com` |
| `TIJORI_OIDC_CLIENT_ID`, `TIJORI_OIDC_CLIENT_SECRET` | Google sign-in, see below |
| `TIJORI_OWNER_EMAIL` | The one Google account allowed to sign in |

The env file can live anywhere; on a server, `/etc/tijori/tijori.env` (mode 600, owned by root) keeps it out of the checkout. Pass its path with `--env-file`.

### Google sign-in

1. In [Google Cloud Console](https://console.cloud.google.com/apis/credentials), create a project, then **OAuth consent screen**: type External, add your email as a test user (no verification needed for your own use).
2. **Credentials → Create credentials → OAuth client ID**, type *Web application*.
3. Authorised redirect URI: `{TIJORI_PUBLIC_URL}/auth/callback`, exactly. For local use also add `http://localhost:8310/auth/callback`.
4. Copy the client ID and secret into the env file.

### Mail

Mail is connected in the app, not in the env file (onboarding step 2, or Settings → Mail sources):

1. Turn on IMAP for the mailbox (Gmail: Settings → Forwarding and POP/IMAP).
2. Create an app password (Gmail: Google Account → Security → 2-Step Verification → App passwords; other providers call it an app-specific password).
3. In Tijori, enter the IMAP host (`imap.gmail.com` for Gmail), port 993, the address and the app password. Tijori tests the connection before saving.
4. Create a mail label/folder named `tijori` and a filter that files your bank statements and transaction alerts into it. Tijori only ever opens that label, read-only.

## 2. Start it

```bash
docker compose -f deploy/compose.yml --env-file deploy/.env up --detach --build
curl -fsS http://127.0.0.1:8310/health      # {"status":"ok","database":"ok"}
```

**Migrations** run by themselves: the `migrate` service runs `alembic upgrade head` as the database owner before the api starts, on every `up`. Migrations are written expand/contract, so the previous release still runs against a newer schema.

On a laptop you can now open `http://localhost:8310`. Production mode refuses a non-`https` public URL; for a quick local try without Google, run the api in dev mode instead (see `api/README.md`, "Run locally").

## 3. Put it behind HTTPS

The api publishes `127.0.0.1:8310` by default (`TIJORI_BIND`, `TIJORI_PORT`), reachable only from the host. Point a reverse proxy at it.

**Caddy** (gets the certificate itself):

```caddy
tijori.example.com {
    reverse_proxy 127.0.0.1:8310
}
```

**nginx** (certificate from certbot or your own):

```nginx
server {
    listen 443 ssl http2;
    server_name tijori.example.com;
    ssl_certificate     /etc/letsencrypt/live/tijori.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tijori.example.com/privkey.pem;
    client_max_body_size 16m;   # statement uploads; the api caps them at 15 MiB
    location / {
        proxy_pass http://127.0.0.1:8310;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

**Traefik** running in Docker can't reach the host's loopback. Either set `TIJORI_BIND=172.17.0.1` (the Linux docker0 gateway) and route to `http://172.17.0.1:8310`, or uncomment the `labels` block on the `api` service in `deploy/compose.yml`, put the api on Traefik's network, and set the host rule.

Don't publish port 8310 to the internet directly: the proxy is what terminates TLS.

## 4. Backups

Two secrets live outside any backup: `TIJORI_MASTER_KEY` and, if you use restic, its password. Without the master key, restored passwords and files can't be read.

**Plain `pg_dump`** (simplest; copy the files somewhere off the machine):

```bash
docker compose -p tijori -f deploy/compose.yml --env-file deploy/.env exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > tijori-$(date +%F).dump
docker run --rm -v tijori_blobs:/blobs -v "$PWD":/out alpine tar czf /out/tijori-blobs-$(date +%F).tgz -C /blobs .
```

**restic, nightly** (`deploy/backup.sh` + `tijori-backup.timer`): dumps the database, adds the stored-files volume, and sends both to any restic repository, encrypted. It keeps 7 daily, 4 weekly and 12 monthly snapshots, restores and checks the latest dump on the 1st of each month (or with `VERIFY=1`), and records each run so Settings → Notifications & retention shows the last good backup and alerts (`backup_stale`) after 2 days without one.

```bash
apt-get install -y restic                      # plus rclone if the repository is a cloud drive
umask 077; openssl rand -base64 36 > /etc/tijori/restic.pass
# RESTIC_REPOSITORY defaults to rclone:tijori-drive:tijori-backup; set it in the unit for S3, B2, SFTP, a local disk…
cp deploy/tijori-backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tijori-backup.timer
VERIFY=1 systemctl start tijori-backup.service # first run, with the restore check
```

The timer fires at 03:30 IST; edit `OnCalendar` for your zone. The units assume the checkout at `/opt/tijori` and the env file at `/etc/tijori/tijori.env` (`DEPLOY_DIR`, `TIJORI_ENV_FILE`).

**Restore** on a fresh machine with the master key in the env file:

```bash
docker compose -p tijori -f deploy/compose.yml --env-file deploy/.env up --detach db
docker compose -p tijori -f deploy/compose.yml --env-file deploy/.env exec -T db \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < tijori.dump
# files: copy the backed-up blobs into the tijori_blobs volume, then start everything with `up --detach`
```

With restic: `restic restore latest --target /restore`, then the dump is at `/restore/var/backups/tijori/nightly.dump`.

## 5. Update

```bash
git pull
docker compose -p tijori -f deploy/compose.yml --env-file deploy/.env exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > pre-update.sql.gz
docker compose -p tijori -f deploy/compose.yml --env-file deploy/.env up --detach --build
```

A code rollback (`git checkout <previous>` and `up` again) doesn't undo a migration; the dump is the way back.

## 6. Optional: auto-deploy from git

`deploy/pull-update.sh` with `tijori.{service,timer}` deploys every new commit on a branch, every 2 minutes, pulling with a read-only deploy key so nothing outside the server holds access to it. Each run:

1. Takes a lock, fetches the branch, and stops if that commit is already live or already failed.
2. Builds first; a failed build leaves production untouched.
3. Dumps the database to `/var/backups/tijori/pre-<sha>.sql.gz` (last 14 kept).
4. `up --build`, then polls `/health` for up to 2 minutes.
5. On failure, returns to the last good commit and marks the new one failed; on success, moves the `deployed` branch.
6. Optionally notifies an ntfy topic (`NTFY_URL`, `NTFY_OPS_TOKEN`).

```bash
ssh-keygen -t ed25519 -N "" -C "tijori read-only" -f /root/.ssh/tijori_deploy
gh repo deploy-key add /root/.ssh/tijori_deploy.pub --repo <you>/tijori --title "server read-only"
GIT_SSH_COMMAND="ssh -i /root/.ssh/tijori_deploy -o IdentitiesOnly=yes" git clone git@github.com:<you>/tijori.git /opt/tijori
install -d -m 700 /etc/tijori && install -m 600 deploy/.env.example /etc/tijori/tijori.env   # then fill it in
cp /opt/tijori/deploy/tijori.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tijori.timer
```

Useful: `systemctl start tijori.service` (deploy now), `journalctl -u tijori.service -n 100` (last log), `git -C /opt/tijori log -1 deployed` (what's live), `rm /var/lib/tijori/failed-commit` (retry a rejected commit). Set `TIJORI_HEALTH_URL` if the api isn't on `127.0.0.1:8310`.

`deploy/app.conf` describes the same app for [scale0](https://github.com/jblaze2908/scale0), an optional scale-to-zero tool that stops the api when idle and wakes it on the next request. Without scale0 installed, `pull-update.sh` ignores it.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| api exits with `prod needs …` | A required key is empty in the env file. The message names it, never the value |
| `prod needs an https:// TIJORI_PUBLIC_URL` | Production requires HTTPS. Put it behind a proxy (step 3), or try it locally in dev mode |
| Google shows `redirect_uri_mismatch` | The OAuth client's redirect URI must equal `{TIJORI_PUBLIC_URL}/auth/callback` exactly, scheme included |
| Sign-in works but you're refused | Only `TIJORI_OWNER_EMAIL` may sign in; check it matches the Google account |
| `migrate` fails, api never starts | `docker compose … logs migrate`. Usually the owner password changed after the database was created: the passwords only take effect on a fresh `pgdata` volume |
| `bind: cannot assign requested address` | `TIJORI_BIND` is an address the host doesn't have (172.17.0.1 doesn't exist on Docker Desktop). Use `127.0.0.1` |
| Mail test fails | Use an app password, not your account password; check IMAP is enabled and port 993 is reachable |
| No statements appear | Mail must be in the `tijori` label. Settings → Sources shows what was seen and why anything was skipped |
| `/health` reports the database down | `docker compose … ps` and `logs db`; check disk space |
