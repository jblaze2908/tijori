# Tijori API

The backend for Tijori. It covers:

- the SBI and HDFC statement parsers, with reconciliation,
- the deterministic classifier,
- the Postgres schema with row-level security,
- the HTTP API: reads, writes, uploads, Google sign-in and onboarding,
- the same-origin UI,
- the one-off legacy import.

The HTTP contract lives in [`../docs/api.md`](../docs/api.md), and deployment in [`../docs/deploy.md`](../docs/deploy.md).

The stack is FastAPI, SQLAlchemy 2, Alembic, pydantic/pydantic-settings, psycopg 3, uvicorn, and `cryptography` for SecretBox. PDFs are read by OS tools: `pdftotext` (poppler-utils) and `qpdf`. Tests cover the MCP tools and the orders and freshness services (`tests/`, stdlib `unittest`, no extra dependency):

```sh
uv run python -m unittest discover -s tests      # offline; the database tests skip
TIJORI_TEST_DATABASE_URL=postgresql+psycopg://tijori_app:…@127.0.0.1:55432/tijori \
TIJORI_TEST_ADMIN_DATABASE_URL=postgresql+psycopg://tijori_owner:…@127.0.0.1:55432/tijori \
  uv run python -m unittest discover -s tests    # also against a migrated database (see Run locally)
```

## Setup

```sh
cd api
uv sync --frozen          # Python 3.12+, from uv.lock
```

`pyproject.toml` enforces a release-age cooldown (`exclude-newer`). The Docker image installs the hash-pinned `requirements.lock.txt`. Regenerate it after any lock change:

```sh
uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.lock.txt
```

## Configuration

All configuration comes from `TIJORI_*` variables, validated at startup. Errors never echo values.

| Variable | Notes |
|---|---|
| `TIJORI_ENV` | `dev`, `test` or `prod` (default `prod`). Only dev/test accept the `X-Tijori-Dev-Member` header |
| `TIJORI_DATABASE_URL` | Runtime role `tijori_app`: not a superuser, no `BYPASSRLS` |
| `TIJORI_ADMIN_DATABASE_URL` | Owner role, used by migrations and the CLI |
| `TIJORI_PUBLIC_URL` | E.g. `https://tijori.example.com`. The OAuth redirect is `{this}/auth/callback` |
| `TIJORI_OIDC_CLIENT_ID`, `TIJORI_OIDC_CLIENT_SECRET` | The Google OAuth client. Required in prod |
| `TIJORI_OWNER_EMAIL` | The one Google account that may sign in. Required in prod. Every other member is refused on every request; invites are off |
| `TIJORI_MASTER_KEY` | SecretBox key, base64 of 32 bytes. Required in prod. `TIJORI_MASTER_KEY_OLD` is only needed while rotating |
| `TIJORI_BLOB_DIR`, `TIJORI_WEB_DIST`, `TIJORI_MAX_UPLOAD_BYTES` | Raw upload store, built UI, upload cap. The image sets the first two |

Each request binds the member's RLS context. It sets `tijori.member_id` and `tijori.household_id` with `set_config(..., true)` inside its own transaction, and resolves the login in the same round-trip. Queries also filter `member_id` explicitly, so rows shared with you never leak into your own totals.

## Run locally

```sh
docker run -d --rm --name tijori-pg -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_DB=tijori -e POSTGRES_USER=tijori_owner -e POSTGRES_PASSWORD=dev-only postgres:17.11
export TIJORI_ADMIN_DATABASE_URL=postgresql+psycopg://tijori_owner:dev-only@127.0.0.1:55432/tijori
uv run alembic upgrade head
psql "${TIJORI_ADMIN_DATABASE_URL/+psycopg/}" -c "ALTER ROLE tijori_app LOGIN PASSWORD 'dev-only-app'"

export TIJORI_ENV=dev TIJORI_BLOB_DIR=$PWD/.blobs TIJORI_MASTER_KEY=$(openssl rand -base64 32)
export TIJORI_DATABASE_URL=postgresql+psycopg://tijori_app:dev-only-app@127.0.0.1:55432/tijori
uv run uvicorn tijori.app.main:app --port 8310     # schema browser at /api/docs in dev
```

Members come from Google sign-in (an allowlisted email or an invite), or from the legacy import below. In dev, the header `X-Tijori-Dev-Member: <email>` authenticates as an existing member:

```sh
curl -s -XPOST localhost:8310/api/uploads -H 'X-Tijori-Dev-Member: me@example.com' -H 'X-Requested-With: tijori' \
  -F file=@statement.txt
```

Google sign-in needs a real OAuth client with the redirect URI `http://localhost:8310/auth/callback` registered.

## CLI

```sh
uv run python -m tijori.cli import-legacy --data data.json --sheet sheet.csv --member you@example.com \
  [--statement sbi.txt --statement hdfc.txt] [--profile profile.json]
uv run python -m tijori.cli backtest --labels data.json --statement sbi.txt --statement hdfc.txt \
  --profile profile.json [--train-until 2026-07]
TIJORI_MASTER_KEY=<new> TIJORI_MASTER_KEY_OLD=<current> uv run python -m tijori.cli rotate-master-key
```

- **import-legacy** is idempotent. With `--statement`, txns get the same line identity an upload gives them, so uploading those statements later matches rather than duplicates.
- **lock-to-owner** ends every session, pasted token, connected app and invite held by anyone but `TIJORI_OWNER_EMAIL`. Other members' data is kept. Runs on the owner connection.
- **rotate-master-key** re-wraps every data key under the new master key; the secrets themselves aren't re-encrypted. It runs on the owner (superuser) connection. Afterwards, drop `TIJORI_MASTER_KEY_OLD`.
- **Real data** (statements, labels, profiles) never enters git. Keep those files outside the repo.

## Layout

| Path | What it holds |
|---|---|
| `tijori/parsers/` | Parser registry, SBI and HDFC statement parsers, reconciliation |
| `tijori/classify/` | Taxonomy, narration parsing, brand dictionary, kind rules, payee memory, engine, backtest |
| `tijori/services/` | SQLAlchemy service layer: ingest, txns, reports and trends, net worth, members, onboarding, mail, secrets |
| `tijori/app/` | FastAPI app: auth (dev header, session cookie), Google sign-in, routes, uploads, onboarding, SPA and security headers |
| `tijori/secretbox.py`, `tijori/imap_check.py`, `tijori/pdf.py`, `tijori/blobs.py` | AES-GCM envelope, read-only IMAP test, PDF → text, raw blob store |
| `tijori/models.py`, `migrations/versions/` | Schema and Alembic migrations 0001–0004, including RLS policies and grants |
