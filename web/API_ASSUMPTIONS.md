# What the UI calls beyond docs/api.md

`docs/api.md` (v2) is the contract, and the UI follows it for every route it documents. This file lists only what the UI also relies on. Delete it once `docs/api.md` covers these.

## Not in M0

The UI treats a 404 on these as "section unavailable".

| Route | Shape the UI expects |
|---|---|
| `GET /api/recurring` | `{items: [{id, merchant, cadence, amount_expected, next_due}]}` |
| `GET /api/alerts?month=` | `{items: [{id, kind, severity: "good"\|"warn"\|"bad", title, detail}]}` |

## Auth

- **`GET /auth/login?next=/path`:** a server redirect to Google sign-in. `next` is a same-origin app path; the UI sanitises it before building the link.
- **`401` on `/api/me`:** the UI goes to `/welcome?next=<current path>`.
- **`POST /auth/logout`:** ends the session. The UI then goes to `/welcome`.
- **`GET /auth/login?invite=<token>&next=/onboarding/profile`:** how the invite page signs someone in.

## Onboarding and settings (proposed to the backend; not in docs/api.md yet)

Every read is optional: a 404 renders "not available yet". Secrets are write-only. No response carries an app password or a statement password, and the UI never prefills or echoes one and clears the field after each submit. All writes are JSON.

| Route | Body | Response |
|---|---|---|
| `GET /api/onboarding` | | `{step: "profile"\|"accounts"\|"mail"\|"gmail"\|"passwords"\|"backfill"\|"done", completed: [step], gmail_filter, label, gmail_check: {label_found, messages, checked_at} \| null, backfill: {status: "idle"\|"running"\|"done"\|"error", processed, total \| null, txns, message \| null}}` |
| `PATCH /api/onboarding` | `{step}` | The same shape. The UI seeds its cache with it. |
| `POST /api/onboarding/check-gmail` | `{}` | `{label_found, messages, checked_at}` |
| `POST /api/onboarding/backfill` | `{}` | The `backfill` object. The UI polls `GET /api/onboarding` every 2 s while it's running. |
| `PATCH /api/me` | `{name}` | `Me` |
| `PATCH /api/settings` | `{month_start_day}` | Already in docs/api.md |
| `GET` and `PATCH /api/classify-profile` | `{own_names[], own_vpas[], own_account_masks[], investment_account_masks[], employer_patterns[]}` | The same shape. Masks are last-4 only. |
| `POST /api/accounts` | `{institution, name \| null, kind, mask}` | `Account` |
| `DELETE /api/accounts/{id}` | | |
| `GET /api/mail-sources` | | `{items: [{id, provider, email, host, port, label, status: "active"\|"paused"\|"error", last_sync_at, last_error, messages_seen}]}` |
| `POST /api/mail-sources/test` | `{provider, host, port, email, app_password}` | `{ok: true, messages}` or `{ok: false, error}`, with a friendly error. Stores nothing. |
| `POST /api/mail-sources` | The same body as the test | The source |
| `PATCH /api/mail-sources/{id}` | `{app_password}` | The source (password rotation) |
| `DELETE /api/mail-sources/{id}` | | |
| `GET /api/statement-passwords` | | `{items: [{account_id, account_label, set, updated_at}]}` |
| `PUT /api/statement-passwords/{account_id}` | `{password}` | `{account_id, set: true, updated_at}` |
| `DELETE /api/statement-passwords/{account_id}` | | |
| `GET /api/household` | | `{id, name, members: [{id, name, email, role, joined_at}], invites: [{id, email, status, created_at, expires_at}]}` |
| `POST /api/household/invites` | `{email}` | `{id, email, url, expires_at}`. The URL is shown once, for the admin to copy. |
| `DELETE /api/household/invites/{id}` | | |
| `GET /api/invites/{token}` | | Needs no auth. `{valid, household_name, inviter_name, email, expires_at}` |
