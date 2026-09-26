# Deploy

Tijori deploys itself, the same way nullframe does:
- A systemd timer on host runs `deploy/pull-update.sh` every 2 minutes.
- The script pulls `main` with a **read-only deploy key**. There are no GitHub Actions, and nothing outside host holds access to the server.

## What a release does
1. `flock` makes sure only one deploy runs at a time.
2. Fetch `origin/main`. Stop if it's already deployed and healthy, or if this commit already failed.
3. **Build first.** `docker compose build`. A failed build rejects the commit, and production is left untouched. There's no test gate for now (decided 2026-09-26).
4. `pg_dump` the live database to `/var/backups/tijori/pre-<sha>.sql.gz`, keeping the last 14. The `migrate` service runs during `up`, and a code rollback doesn't undo it, so this dump is the way back.
5. `docker compose up --build`, then poll `http://172.17.0.1:8310/health` for up to 2 minutes.
6. On failure, go back to the last `deployed` commit and mark the target as failed. On success, move the `deployed` branch.
7. Optionally, send a notification to the ntfy topic `tijori-ops` (`NTFY_URL` and `NTFY_OPS_TOKEN` in the env file).

Migrations (the one-shot `migrate` service, `alembic upgrade head`) must be **backward-compatible (expand/contract)**, because a rollback runs old code against the new schema.

## One-time setup on host
Each step changes live infrastructure or creates a credential, so each one is confirmed before it's run.

```bash
# 1. Read-only deploy key: only the public half is added to GitHub, without write access
ssh-keygen -t ed25519 -N "" -C "host tijori read-only" -f /root/.ssh/tijori_deploy
gh repo deploy-key add /root/.ssh/tijori_deploy.pub --repo jblaze2908/tijori --title "host read-only"   # run from a machine with gh auth

# 2. Code and config
GIT_SSH_COMMAND="ssh -i /root/.ssh/tijori_deploy -o IdentitiesOnly=yes" git clone git@github.com:jblaze2908/tijori.git /opt/tijori
install -d -m 700 /etc/tijori && install -m 600 /dev/null /etc/tijori/tijori.env   # Jai fills in the values; names are in .env.example

# 3. Timer
cp /opt/tijori/deploy/tijori.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tijori.timer

# 4. Routing: a Cloudflare DNS record `tijori` → 203.0.113.10, plus a Traefik router in /opt/sso-proxy/config/traefik/dynamic_config.yml (like brain) to http://172.17.0.1:8310
```

## Useful commands
```bash
systemctl start tijori.service            # deploy now instead of waiting for the timer
journalctl -u tijori.service -n 100       # last deploy log
git -C /opt/tijori log -1 deployed        # what's live
rm /var/lib/tijori/failed-commit          # retry a rejected commit
```

## Later
Woodpecker CI, and tests if they're ever reintroduced, would run on pushes and PRs.
