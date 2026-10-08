# Tijori

Self-hosted personal finance for India. Tijori reads your bank and card statements from your own mailbox, classifies every transaction without AI, reconciles each statement to the paisa, and shows where the money went. Agents get the same data over MCP.

One instance serves one owner (optionally a household). Your statements, passwords and mail credentials stay on your server.

## What it does

- **Collects** statements and transaction alerts from a Gmail/IMAP label every 2 minutes, or by upload. Password-protected PDFs are opened with the passwords you store.
- **Parses** SBI and HDFC savings statements, HDFC and ICICI card statements, CAMS and CDSL CAS (mutual funds, demat). Each statement must reconcile against its own footer.
- **Classifies** deterministically: structural rules (fees, refunds, transfers between your own accounts, card bills, investments, salary, interest), a brand dictionary, and payee memory learned from your confirmations. Anything unsure lands in an Inbox for you to file once.
- **Shows** spending by category and cycle (your month can start on payday), trends, budgets, subscriptions and recurring payments, net worth with daily mutual fund NAVs, loans, and Blinkit/Zomato/Amazon order items matched to the charge.
- **Serves agents** over MCP (`/mcp`, OAuth 2.1). Person UPI handles are masked in anything that leaves Tijori.

## Architecture

| Part | What |
|---|---|
| `api/` | FastAPI + SQLAlchemy + Alembic on Postgres 17 with row-level security. Parsers, classifier, HTTP API, MCP server, IMAP collector worker. See [`api/README.md`](api/README.md) |
| `web/` | React 19 + Vite, no router/state/chart library; served by the api from the same origin under a strict CSP. See [`web/README.md`](web/README.md) |
| `deploy/` | `compose.yml` (db, one-shot migrate, api, worker), Dockerfile, pull-based deploy and nightly restic backup units |
| `docs/` | [`api.md`](docs/api.md) is the HTTP and MCP contract; [`deploy.md`](docs/deploy.md) covers self-hosting, backups and restore |

Secrets (app and statement passwords) are sealed with an AES-GCM envelope under `TIJORI_MASTER_KEY`. The api runs as a non-owner Postgres role, so row-level security always applies.

## Quickstart (local)

```sh
cd api && uv sync --frozen
# Postgres, migrations and a dev server: see "Run locally" in api/README.md
cd ../web && pnpm install --ignore-scripts && TIJORI_DEV_MEMBER=you@example.com pnpm run dev
```

In `TIJORI_ENV=dev` the header `X-Tijori-Dev-Member` stands in for Google sign-in, so you can try it without an OAuth client.

## Self-host

```sh
cp deploy/.env.example /etc/tijori/tijori.env && chmod 600 /etc/tijori/tijori.env   # fill it in
docker compose -f deploy/compose.yml --env-file /etc/tijori/tijori.env up --detach --build
```

You need a Google OAuth client (redirect `{TIJORI_PUBLIC_URL}/auth/callback`), a master key (`openssl rand -base64 32`), and a reverse proxy with TLS in front of the api's port. Only `TIJORI_OWNER_EMAIL` can sign in. [`docs/deploy.md`](docs/deploy.md) has the auto-deploy timer, backups and restore.

## Configuration

Every key is listed, with comments, in [`deploy/.env.example`](deploy/.env.example). The api reads only `TIJORI_*` variables and refuses to start in prod without the sign-in pair, the owner email and the master key.

## License

[MIT](LICENSE)
