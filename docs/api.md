# Tijori API

This is the contract the web UI is built against. Examples come from synthetic statements (fake names, numbers and amounts); none of it is real data.

- The API lives under `/api`, served from the same origin as the UI. The api also serves the built UI; see [Same-origin UI](#same-origin-ui).
- The health check is `/health`.
- FastAPI's generated schema is at `/api/docs` and `/api/openapi.json` in dev/test only (off in prod). This file is the reference.

## Conventions

| Topic | Rule |
|---|---|
| Money | A **string** holding a decimal with exactly two places, e.g. `"1234.50"`. Never a float. Transaction amounts are always ≥ 0; `direction` says which way the money moved. Signed aggregates such as `change` or trend points can be negative (`"-250.00"`). |
| Currency | INR everywhere (`"currency": "INR"`). |
| Dates and times | Dates are `YYYY-MM-DD`; months are `YYYY-MM`; timestamps are ISO 8601 with an offset. "Today" is taken in Asia/Kolkata. |
| Months are cycles | Every month parameter and bucket follows the member's `month_start_day` (default 1). This covers `/api/summary`, `/api/months`, `/api/transactions?month=`, `/api/budgets` and `/api/trends`. A month runs from that day to the day before it in the next month. It is **labelled by the calendar month it starts in**: with day 25, `2026-04` is 25 Apr – 24 May. With day 1 it is the calendar month |
| Spend | **One definition everywhere:** debits in the `everyday`, `oneoff` and `card` buckets, plus uncategorized (Inbox) debits. Refunds are reported separately and never netted. A txn's `bucket` comes from its category; the people categories (Family, Friends, Social circle) have a `credit_bucket` of `income`, so money sent counts as spend and money received as income. `/api/summary` `expense` and the `/api/trends` totals use exactly this rule, so a headline number always equals its trend bar |
| Paging | `page` starts at 1. `page_size` runs 1–200 (default 50). Paged responses carry `page`, `page_size` and `total`. |
| Ordering | Transaction lists are newest first (`occurred_at` desc, then `id` desc). |
| Nulls | Optional fields are present and set to `null`. They are never omitted. |
| Scope | Every `/api/*` response covers only the signed-in member's own data. |
| Untrusted text | `narration`, `merchant`, `counterparty`, `display`, `remark` and `commentary` can contain attacker-controlled text from bank narrations. Render them as text, never as HTML. |
| Writes | JSON bodies need `Content-Type: application/json` (415 otherwise). Uploads need multipart **and** the header `X-Requested-With: tijori` (403 otherwise). Both rules force a CORS preflight, so a cross-site form can't write. |

### Authentication

| Environment | How a request authenticates |
|---|---|
| Any environment | A session cookie from [Google sign-in](#google-sign-in-auth). |
| `TIJORI_ENV=dev` or `test` only | Also the header `X-Tijori-Dev-Member: <member email>` of an existing member. In prod the header is ignored, and the dev provider can't even be constructed. |

- Without a valid session, every `/api/*` call returns **401** `{"detail": "not signed in"}`. The UI should redirect to `/auth/login` when `GET /api/me` returns 401.
- Writes authenticated by the cookie must also send an `Origin` (or `Referer`) matching `TIJORI_PUBLIC_URL`, otherwise they get **403**. Browsers do this for `fetch`. With `SameSite=Lax` on the cookie, that is the CSRF defence, on top of the JSON-only and upload-header rules.

### Errors

Every error body is JSON. None carries a stack trace or SQL.

| Status | When | Body |
|---|---|---|
| 400 | Malformed multipart body | `{"detail": "..."}` |
| 401 | Not signed in, or the session expired | `{"detail": "not signed in"}` |
| 403 | A dev-header email that isn't a member, a cross-origin cookie write, or an upload without `X-Requested-With: tijori` | `{"detail": "..."}` |
| 404 | Unknown route, or a row that doesn't exist **or belongs to someone else** (the two are indistinguishable by design) | `{"detail": "..."}` |
| 405 | The route exists but not for this method | `{"detail": "Method Not Allowed"}` |
| 413 | The body is too large | `{"detail": "..."}` |
| 415 | Wrong `Content-Type`, or an upload that is neither PDF nor UTF-8 text | `{"detail": "..."}` |
| 422 | Validation failed | FastAPI's shape, listing every problem at once: `{"detail": [{"type", "loc": ["query"\|"body"\|"path", "<field>"], "msg", "input", "ctx"?}]}`. Semantic failures, such as an unknown category or an unparseable document, send `{"detail": "<message>", ...}` |
| 500 | A bug | `{"detail": "Internal Server Error"}`. The traceback goes to the server log only |
| 503 | `/health` when the database is down, or PDF tools missing on the server | `{"status": "degraded", ...}` / `{"detail": "..."}` |

### Enumerations

| Field | Values |
|---|---|
| `direction` | `debit` (money out), `credit` (money in) |
| `kind` | `spend`, `income`, `transfer`, `investment`, `refund`, `fee`, `cash` |
| `bucket` | `everyday`, `oneoff`, `card`, `invest`, `income`, `excluded`, or `null` while a txn waits in the Inbox |
| `classified_by` | `rule`, `payee_memory`, `dictionary`, `heuristic`, `user`, `system` (the legacy import), or `null` while in the Inbox |
| `status` | `pending`, `posted`, `reconciled` (matched to a statement that balanced to ₹0), `flagged` |
| `sources[]` | `statement`, `alert`, `sms`, `upload`, `expected`, `import` |
| `review_reason` / Inbox `reason` | `person` (a personal UPI handle or named transfer), `merchant_over_cap` (a merchant-QR payment above the local-shop cap), `conflict` (payee memory disagrees with itself), `new_payee`, or `null` |
| `rule_id` | What decided the txn, or why it is in the Inbox: `rule:<id>`, `kind:salary`, `dict:blinkit`, `memory:<payee_key>`, `upi:merchant_qr`, `upi:person`, `memory:conflict`, `link:reversal`, `user`, `legacy:2026-09-26`. Show it on hover; don't parse it |

### Transaction object (`Txn`)

Every endpoint that returns transactions uses this shape.

```json
{
  "id": 1125, "occurred_at": "2026-04-05", "posted_at": "2026-04-05",
  "amount": "450.00", "currency": "INR", "direction": "debit", "kind": "spend",
  "merchant": "Blinkit", "counterparty": "Blinkit", "vpa": "blinkit.rz", "payee_key": "brand:blinkit",
  "narration": "WDL TFR UPI/DR/333333333333/Blinkit/HDFC/blinkit.rz/Payvi",
  "account": {"id": 50, "institution": "SBI", "name": "SBI savings", "label": "SBI savings ••1234", "kind": "bank", "mask": "1234"},
  "category": {"id": 1151, "name": "Groceries"}, "bucket": "everyday",
  "classified_by": "dictionary", "rule_id": "dict:blinkit", "review_reason": null,
  "status": "reconciled", "sources": ["statement"], "notes": null, "tags": []
}
```

| Field | Notes |
|---|---|
| `merchant` | Normalised display name, or your own name for the payee (see Payee names) |
| `named` | `true` when `merchant` is your name for the payee, so it wins over the UPI handle in lists |
| `counterparty` | The payee as the bank printed it |
| `vpa` | The UPI handle, in full (Jai's call, 2026-09-27: a member only ever reads their own data). Anything that sends data out of Tijori, such as MCP, must mask a person's handle itself |
| `payee_key` | The stable payee identity used by the Inbox and payee memory. It can contain `:` and `\|`, so URL-encode it in paths |
| `account` | `null` when the txn has no account. `mask` is the last 4 digits only; `label` is ready to display: the account name (or institution) and `••` plus the mask |
| `category` | `null` while the txn is in the Inbox |
| `posted_at` | The bank's value date |

---

## `GET /health`

Needs no auth and returns no personal data.

- 200: `{"status": "ok", "database": "ok"}`
- 503: `{"status": "degraded", "database": "unavailable"}`

## `GET /api/me`

```json
{"name": "Asha", "email": "asha@example.test", "role": "admin",
 "household": {"id": 40, "name": "Asha's household"},
 "settings": {"month_start_day": 1, "local_shop_cap": "500.00"}}
```

`PATCH /api/me` `{"name"}` renames the member (1–120 chars) and returns 204.

## `GET /api/settings` and `PATCH /api/settings`

`GET` returns `{"month_start_day": 1, "local_shop_cap": "500.00", "raw_retention_days": 0, "notify_topic": null, "notify_enabled": false}`.

`PATCH` takes a JSON body with at least one of the fields below, and returns the new settings. It is audit-logged.

| Field | Type | Notes |
|---|---|---|
| `month_start_day` | integer 1–28 | The salary-cycle start. It moves every month boundary: summary, months, the transactions `month` filter, budgets, and trend months, quarters and FYs |
| `local_shop_cap` | money string (`"750.00"`) or integer, 0–1,00,000 | A merchant-QR payment up to this amount auto-files as Local shops. It applies to statements uploaded after the change |
| `raw_retention_days` | `0` (default), `90`, `180`, `365`, `730` | How long stored mail and files are kept. `0` keeps them forever. See [Retention](#retention) |
| `notify_topic` | 12–64 of `A-Z a-z 0-9 _ -`, or `null` | The ntfy topic pushes go to. `null` removes it and turns notifications off. See [Notifications](#notifications) |
| `notify_enabled` | bool | Send pushes. Has no effect without a topic |

## `GET /api/months`

Month cycles that contain at least one txn, oldest first. `start` and `end` are the cycle's first and last day. `through` is the last day with data. `complete` is true once the cycle has ended.

```json
{"as_of": "2026-09-26", "month_start_day": 1,
 "items": [{"month": "2026-04", "start": "2026-04-01", "end": "2026-04-30", "through": "2026-04-30",
            "complete": true, "txn_count": 16}]}
```

## `GET /api/accounts`

```json
{"items": [{"id": 51, "institution": "HDFC", "name": "HDFC savings", "kind": "bank", "mask": "9876",
            "label": "HDFC ••9876", "currency": "INR", "txn_count": 8,
            "first_txn_at": "2026-04-01", "last_txn_at": "2026-04-30",
            "last_statement": {"period_start": "2026-04-01", "period_end": "2026-04-30", "reconciled": true, "diff": "0.00"},
            "has_statement_password": false, "has_extra_statement_password": false, "balance": {"amount": "184500.00", "as_of": "2026-08-31"},
            "last_seen_at": null, "coverage_pct": null}]}
```

- `balance` is the balance printed after the account's newest statement line, or `null` when no statement had one.
- `last_seen_at` (last live alert) and `coverage_pct` (share of statement lines seen live) stay `null` until the collectors land in M1.

Accounts are created automatically from uploaded statements. They can also be declared up front, for example during onboarding:

| Endpoint | Behaviour |
|---|---|
| `POST /api/accounts` `{"institution", "kind", "name"?, "mask"?}` | `kind` is one of `bank`, `card`, `wallet`, `deposit`, `holding`, `cash`; `mask` is 4 digits. 201 with the account. A later statement for the same institution and mask lands on it |
| `PATCH /api/accounts/{id}` `{"name"?, "mask"?}` | Returns the account; send `null` to clear a field |
| `DELETE /api/accounts/{id}` | 204. 422 if the account has transactions or statements, since history is never orphaned |

---

## `GET /api/summary?month=YYYY-MM`

Dashboard totals for one month cycle and the cycle before it. `period` gives the cycle's first and last day.

| Param | Required | Notes |
|---|---|---|
| `month` | yes | `YYYY-MM`, a cycle per `month_start_day` |

These definitions match the 2026-09-26 report exactly. That was checked on the real data through the legacy import: every month's totals come out equal.

| Field | Meaning |
|---|---|
| `everyday`, `oneoff`, `card` | Sum of **debits** in that bucket. `card` is bill payments from a bank account (CRED, BillDesk) that still stand in for card spend: the card statement they pay isn't parsed. A payment matched to the card's "payment received" line moves to `excluded` (see [`/api/cards`](#get-apicards)), and the card's purchases count instead |
| `uncategorized` | Debits with no category yet (the Inbox) |
| `expense` | `everyday + oneoff + card + uncategorized`: the one spend definition |
| `invest` | Debits in the `invest` bucket |
| `income` | **Credits** in the `income` bucket: salary, interest, dividends, other income and refunds |
| `refunds` | The refund part of `income`. Refunds are **not** netted against spend here |
| `salary` | Credits in the `Salary` category |
| `salary_minus_expense` | `salary − expense` |
| `txn_count` | Every txn in the month, any bucket |

Self transfers, reversal pairs, pass-throughs and investment redemptions are `excluded` and count toward nothing but `txn_count`.

- `buckets` lists `everyday, card, oneoff, invest, income`, in that order.
- `categories` holds the expense-bucket debit categories plus `Uncategorized` (`category_id: null`), sorted by `amount` desc.
- `income_categories` holds the income-bucket credit categories.
- A category seen only in the previous month still appears, with `amount: "0.00"`.
- `change` is `amount − previous_amount`. `txn_count` on a category line covers the current month.

```json
{
  "month": "2026-04", "previous_month": "2026-03", "currency": "INR", "month_start_day": 1,
  "period": {"start": "2026-04-01", "end": "2026-04-30"},
  "totals": {"expense": "23752.00", "everyday": "2252.00", "card": "20000.00", "oneoff": "0.00",
             "uncategorized": "1500.00", "invest": "5000.00", "income": "150925.50", "refunds": "0.00",
             "salary": "150000.00", "salary_minus_expense": "126248.00", "txn_count": 16},
  "previous_totals": {"expense": "0.00", "...": "same keys"},
  "buckets": [{"bucket": "everyday", "amount": "2252.00", "previous_amount": "0.00", "change": "2252.00"}],
  "categories": [{"category_id": 1172, "name": "Card bill payment", "bucket": "card", "amount": "20000.00",
                  "previous_amount": "0.00", "change": "20000.00", "txn_count": 1}],
  "income_categories": [{"category_id": 1165, "name": "Salary", "bucket": "income", "amount": "150000.00",
                         "previous_amount": "0.00", "change": "150000.00", "txn_count": 1}]
}
```

## `GET /api/transactions`

| Param | Type | Notes |
|---|---|---|
| `month` | `YYYY-MM` | A cycle per `month_start_day` |
| `from`, `to` | `YYYY-MM-DD` | Inclusive bounds; can be combined with `month`. `from > to` is a 422 |
| `account` | int, repeatable | Account ids (up to 20); a txn on any of them matches |
| `category` | int id or `none`, repeatable | Up to 40; `none` matches Inbox txns. Any of them matches |
| `kind` | enum | |
| `direction` | `debit` \| `credit` | |
| `q` | string, 1–100 chars | Case-insensitive substring match on narration or merchant. `%` and `_` match literally |
| `min`, `max` | decimal ≥ 0, up to 2 places | Inclusive. `min > max` is a 422 |
| `sort` | `date_desc` (default), `date_asc`, `amount_desc`, `amount_asc` | Ties fall back to newest first |
| `paid_with` | `card` \| `bank` | `card`: txns on card accounts only; `bank`: everything else |
| `page`, `page_size` | int | See paging |

Returns the page plus `totals` for the **whole filtered set**, split on `/api/summary`'s rules:

```json
{"items": [Txn], "page": 1, "page_size": 50, "total": 29,
 "totals": {"spend": {"amount": "376605.00", "count": 11}, "income": {"amount": "1890000.00", "count": 9},
            "invest": {"amount": "225000.00", "count": 9}, "excluded": {"amount": "0.00", "count": 0},
            "card": {"amount": "345315.00", "count": 9}, "on_card": {"amount": "0.00", "count": 0}}}
```

`spend` + `income` + `invest` + `excluded` counts add up to `total`. `card` (bill payments standing in for card spend) and `on_card` (spend on card accounts) are parts of `spend`, not extra groups.

Each item also carries `settles`: `null`, or for either leg of a matched card bill `{"txn_id", "date", "card", "from_account"}`. The bank leg gets `card` (the card it paid), the card leg gets `from_account`.

## `GET /api/transactions/{id}`

One txn with everything behind it. Returns 404 when the txn doesn't exist or isn't yours.

```json
{
  "transaction": {"...": "Txn"},
  "observations": [{"id": 1157, "source": "statement", "parser": "sbi_statement", "parser_version": "1.0.0",
                    "occurred_at": "2026-04-05", "amount": "450.00", "direction": "debit",
                    "balance_after": "50430.00", "ref_no": "333333333333", "raw_message_id": 86,
                    "received_at": "2026-09-26T17:26:45.071340+00:00", "filename": "sbi_statement_synthetic.txt"}],
  "links": [],
  "payee": {"payee_key": "brand:blinkit", "count": 2, "total": "856.00",
            "history": [{"category_id": 1151, "category": "Groceries", "count": 2}],
            "recent": [{"id": 1125, "occurred_at": "2026-04-05", "amount": "450.00", "category": "Groceries"}]}
}
```

- `observations` lists every sighting behind the txn. Legacy-imported txns have none until their statement is uploaded.
- `links` is empty in M0; the resolver fills it in M1.
- `payee` gives this payee's totals in the same direction, and its 12 most recent txns. It is `null` without a `payee_key`. When you named the payee, every payee sharing the name counts: `alias` is `{"name", "original", "payee_keys"}`, where `original` is this payee's name before. Otherwise `alias` is `null`, and `suggest` is `{"name", "why", "like"}` when the payee's name matches one of your names (see Payee names), else `null`.

## `PATCH /api/transactions/{id}`

Body: `{"notes"?: string ≤ 2,000 chars or null, "tags"?: [string 1–40 chars] ≤ 20}`. At least one field. Tags are trimmed and de-duplicated case-insensitively. Returns `{"id", "notes", "tags"}`. Audit-logged; 404 when the txn isn't yours.

## `POST /api/transactions/{id}/category`

Body: `{"category_id": 1151, "scope": "this"}`. Name the category with **exactly one** of `category_id` or `category` (its name, e.g. `"Family"`). `scope` defaults to `this`.

| `scope` | Effect |
|---|---|
| `this` | Files this txn: `classified_by: "user"`, `rule_id: "user"`. That counts as a confirmation for payee memory |
| `payee` | As `this`, and also creates a member rule. The rule matches the payee's UPI handle, or its merchant name when there is no handle, in this direction. For a payee you named, the rule matches that name and covers every payee sharing it. It is applied at once to the payee's (or the name's) other txns in the same direction that you haven't filed by hand (`classified_by: "rule"`) |

Returns `{"updated": 1, "rule_id": null}`, or `{"updated": 4, "rule_id": "rule:12"}` for scope `payee`.

Errors:
- 404: the txn isn't yours.
- 422: unknown or invisible category, or a payee with nothing to build a rule from.

The call is audit-logged.

## `GET /api/categories`

Categories visible to the member: household-wide ones plus the member's own, in taxonomy order.

```json
[{"id": 1, "name": "Groceries", "description": "Supermarkets and quick commerce: Blinkit, JioMart, Zepto, BigBasket.",
  "kind": "spend", "bucket": "everyday", "credit_bucket": null, "parent_id": null, "scope": "household"}]
```

The default taxonomy has 28 categories:

- **Spend (16):** Groceries, Eating out, Shopping, Bills & subscriptions, Local shops, Travel, Health, Services, Bank charges, Family, Friends, Social circle, Entertainment, Insurance, Tax, Cash. Family, Friends and Social circle count credits as income (`credit_bucket`).
- **Non-spend (12):** Salary, Interest, Dividends, Other income, Refunds, Reversals, Self transfer, Card bill payment, Pass-through, Loans, Investments, Investment redemptions. Loans is filed through [loans](#loans), not the category endpoints.

## `GET /api/inbox`

Txns still waiting for a category, newest first.

| Param | Notes |
|---|---|
| `group` | `txn` (default): one item per txn. `payee`: one item per (payee, direction) |
| `page`, `page_size` | Paging |

`group=txn`:

```json
{"items": [{"txn": {"...": "Txn"}, "reason": "person",
            "payee_history": [{"category_id": 5, "category": "Family", "count": 1}]}],
 "page": 1, "page_size": 50, "total": 1}
```

`group=payee` (`total` counts groups):

```json
{"items": [{"payee_key": "vpa:9000000001", "direction": "debit", "display": "Meena Devi", "reason": "person",
            "count": 1, "total": "1500.00", "last_at": "2026-04-28", "suggestion": null,
            "history": [], "txns": [{"...": "Txn"}]}],
 "page": 1, "page_size": 50, "total": 1}
```

- `history` shows how this payee, in this direction, was filed before, most frequent first.
- `suggestion` is the top of `history`, or `null`.
- A txn with no payee identity forms its own group, keyed `txn:<id>`.

## `POST /api/inbox/{payee_key}/file`

Files the group's Inbox txns. `{payee_key}` must be URL-encoded.

| Body field | Type | Notes |
|---|---|---|
| `category_id` / `category` | int / string | Exactly one |
| `direction` | `debit` \| `credit` | Optional. Send the group's `direction` so a payee you both pay and get paid by is filed one side at a time |
| `remember` | bool, default false | Also create a member rule for the payee. `scope: "payee"` is accepted as an alias |
| `txn_ids` | list of ints, ≤ 500 | Optional: file only these txns in the group |

Returns `{"filed": 3, "rule_id": null}`, or `"rule:5"` when `remember` is set. Filed txns get `classified_by: "user"`, which is how payee memory learns. After two agreeing confirmations, the next statement files that payee automatically (`classified_by: "payee_memory"`).

Errors: 404 when the group has no Inbox txns, or isn't yours. The call is audit-logged.

## `GET /api/budgets?month=YYYY-MM`

```json
{"month": "2026-09", "day": 27, "days": 30,
 "items": [{"category_id": 2, "category": "Eating out", "amount": "3000.00", "carry": "0.00", "limit": "3000.00",
            "spent": "5596.00", "remaining": "-2596.00", "expected_by_today": "2700.00", "projected": "6217.78",
            "state": "over", "rollover": true}],
 "totals": {"limit": "3000.00", "spent": "5596.00"}}
```

- `spent` is the month cycle's spend in that category, on the summary's rules (see Conventions).
- `day` and `days` place today in the cycle; for a past cycle `day` equals `days`.
- `limit` = `amount` + `carry`. With `rollover` on, `carry` is what the previous cycle left unspent (never negative).
- `expected_by_today` = `limit` × `day` / `days`. `projected` = `spent` × `days` / `day`.
- `state`: `over` when `spent` > `limit`; `ahead` when `spent` > 1.10 × `expected_by_today`; else `ok`.

## `PUT /api/budgets/{category_id}`

`{"amount": "3000.00" | null, "rollover": false}`. It sets the monthly budget for an `everyday` or `oneoff` category. An `amount` of `null` or `"0"` removes the budget. Returns `{"category_id", "amount", "rollover"}`, with `amount` null when removed. It is audit-logged.

Errors: 404 for a category that isn't yours; 422 for another bucket or a bad amount.

## `GET /api/trends`

Spend over time. Aggregation happens in SQL, bucketed by `date_trunc`.

| Param | Default | Notes |
|---|---|---|
| `granularity` | `month` | `week` (weeks start Monday), `month`, `quarter`, `fy` (Indian financial year, Apr–Mar) |
| `periods` | 12 | 1–60. That many periods, ending with the one that contains `end` |
| `end` | today (IST) | `YYYY-MM-DD` |
| `group_by` | `total` | `total`, `category`, `merchant`, `kind`, `account` (series keyed by the account `label`; `No account` for none) |
| `limit` | 10 | 1–50. For `category` and `merchant`: the top N series by total, the rest merged into `"Other"` |

The member's `month_start_day` shifts month, quarter and FY boundaries. For example, day 25 gives months running 25th to 24th. Weeks ignore it.

- **Spend** uses the one definition (see Conventions): debits in `everyday`, `oneoff` and `card` plus uncategorized debits, with refunds not netted. A month's `total` equals `/api/summary` `expense` for the same month cycle, and `income`/`invested` equal its `income`/`invest`.
- **`group_by=total`** returns six series, all on the summary's rules:
  - `total`: spend.
  - `committed` + `discretionary`: spend split by whether the payee has a live recurring series (see [`/api/recurring`](#get-apirecurring)).
  - `income`: credits in the `income` bucket.
  - `refunds`: the refund part of income.
  - `invested`: debits in the `invest` bucket.
- **`group_by=category|merchant|account`** splits `total` (spend) into series.
- **`group_by=kind`** is a different view. It includes every kind, each measured in its natural direction: credits for `income` and `refund`, debits for the rest. The opposite direction subtracts.

```json
{"granularity": "month", "group_by": "total", "month_start_day": 1,
 "periods": [{"start": "2026-03-01", "end": "2026-03-31"}, {"start": "2026-04-01", "end": "2026-04-30"}],
 "series": [
   {"key": "total", "total": "23752.00", "points": [{"period_start": "2026-03-01", "amount": "0.00", "count": 0},
                                                    {"period_start": "2026-04-01", "amount": "23752.00", "count": 8}]},
   {"key": "committed", "total": "0.00", "points": ["..."]},
   {"key": "discretionary", "total": "23752.00", "points": ["..."]},
   {"key": "income", "total": "150925.50", "points": ["..."]},
   {"key": "refunds", "total": "0.00", "points": ["..."]},
   {"key": "invested", "total": "5000.00", "points": ["..."]}]}
```

- Every series has one point per period, including zeros.
- `count` is the number of contributing txns.
- For `category`, the key `Uncategorized` holds the Inbox.

## `GET /api/networth`

Replaces the sheet one to one. Snapshots are **newest first**.

```json
{"snapshots": [{"date": "2026-05-01",
                "components": {"sbi": "3000.00", "hdfc": "229425.00", "fd": "50000.00", "stocks": "20500.00",
                               "mf": "86000.00", "ppf": "60000.00", "epf": "80000.00", "gold": "10000.00", "other": "0.00"},
                "net_worth": "538925.00", "net_change": "116925.00", "liquid": "282425.00", "liquid_change": "110425.00",
                "remark": null, "commentary": "Net worth +₹1.17L. Biggest mover: HDFC savings +₹1.09L.",
                "commentary_source": "template", "locked": true}],
 "latest": {"date": "2026-05-01", "net_worth": "538925.00",
            "components": [{"key": "hdfc", "label": "HDFC savings", "asset_class": "cash", "amount": "229425.00", "share_pct": 42.6}],
            "by_asset_class": {"cash": "232425.00", "equity": "106500.00", "retirement": "140000.00",
                               "deposits": "50000.00", "gold": "10000.00", "other": "0.00"}}}
```

| Field | Notes |
|---|---|
| `components` | Always all nine keys. `null` means the sheet cell was blank |
| `liquid` | `sbi + hdfc + fd`, the sheet's definition (it matches every row of the real sheet) |
| `net_change`, `liquid_change` | Against the previous (older) snapshot. `null` on the oldest |
| `remark` | The member's own note (the sheet's "Remarks" column) |
| `commentary` | The monthly commentary (the sheet's "My Understanding" column). When none is stored, it is generated from a template with no AI; `commentary_source` then says `template` instead of `stored` |
| `latest.components` | Sorted by amount desc. `share_pct` is a number, not money |
| no snapshots | `{"snapshots": [], "latest": null}` |

## `PATCH /api/networth/snapshots/{date}`

Body: `{"remark": "Paid the annual premium"}`, where `remark` is a string of at most 2,000 chars, or `null`/`""` to clear it. Returns `{"date": "2026-05-01", "remark": "Paid the annual premium"}`.

Errors:
- 404: no snapshot on that date for you.
- 422: bad date or body.

The change is audit-logged. A later sheet re-import keeps a remark you edited here.

## `POST /api/networth/import`

Multipart, with the header `X-Requested-With: tijori`. Field `sheet` holds the Google Sheet exported as CSV, with the header row `Month,SBI,HDFC Bank,HDFC FD,Stocks,Mutual Funds,PPF,EPF,Gold,Other,Net Worth,...,Liquid Cash,...,Remarks,My Understanding`.

- Idempotent: it upserts by date.
- Returns `{"snapshots_upserted": 11}`.
- Returns 422 when the file isn't a sheet export.

## `GET /api/recurring`

Recurring series (the Subscriptions page), detected from the member's own history on every call. There's no AI. Only your decisions are stored.

- A series is debits to one payee (`payee_key`, else the merchant) in the last 800 days: at least 3 (2 for `yearly`).
- Every gap between charges fits one cadence: `weekly` 5–9 days, `monthly` 25–36, `quarterly` 84–98, `yearly` 350–380. One gap of twice the period (a skipped charge) is allowed once there are 4 charges.
- Amounts are steady, with at most one price change (>10% between consecutive charges) per six charges. Otherwise they must be bill-like: category `Bills & subscriptions` or `Insurance`, largest charge at most 3× the smallest (`variable: true`, and `amount_expected` is the median of the last 3).
- Card-bill payments and `excluded` txns never form a series. A payee you confirmed is listed even with fewer charges, on the cadence you gave.

`state` is judged against `seen_through`, the newest txn date on the series' account, so a charge that isn't in a statement yet is not called missed:

| `state` | Rule |
|---|---|
| `upcoming` | `next_due` is after today |
| `pending` | Due, but the account's statements don't reach `next_due` + grace yet (grace: weekly 2, monthly 4, quarterly 10, yearly 20 days) |
| `late` | Statements reach past `next_due` + grace, by up to half a period, and no charge |
| `stopped` | Statements reach more than half a period past `next_due` |
| `ended` | You marked it cancelled |

`kind` groups the page: `invest` (the `invest` bucket), `subscription` (brands in `classify/brands.SUBSCRIPTIONS`), `bill` (`Bills & subscriptions` or `Insurance`), else `other`. You can override it.

```json
{"items": [{"id": "brand:netflix", "merchant": "Netflix", "kind": "subscription", "cadence": "monthly",
            "state": "upcoming", "variable": false, "amount_expected": "799.00", "amount_min": "649.00",
            "amount_max": "799.00", "monthly_cost": "799.00", "yearly_cost": "9588.00", "next_due": "2026-10-14",
            "first_at": "2025-10-14", "last_at": "2026-09-14", "seen_through": "2026-09-26", "count": 12,
            "category": "Bills & subscriptions", "account": "HDFC Platinum ••4242", "confirmed": false,
            "manual": false, "change": {"from": "649.00", "to": "799.00", "at": "2026-07-14"},
            "charges": [{"date": "2026-09-14", "amount": "799.00", "txn_id": 612}]}],
 "dismissed": [{"id": "vpa:someone@okaxis", "merchant": "Someone"}],
 "totals": {"monthly": "10888.59", "yearly": "130663.08", "invest_monthly": "25000.00", "active": 8,
            "next_30_days": "32808.00", "next_30_days_count": 5}}
```

- `monthly_cost` normalises the charge (weekly × 52/12, quarterly ÷ 3, yearly ÷ 12). `totals.monthly` sums the active non-`invest` series; `yearly` is that × 12.
- `change` is the latest step of more than 5% between consecutive charges; always `null` for variable series.
- `charges` are the last 12. Items are sorted by `next_due`; dismissed payees are only in `dismissed`.

## `PUT /api/recurring/{payee_key}`

`{"decision": "confirmed" | "dismissed" | "auto", "cadence"?, "amount_expected"?, "kind"?, "ended"?}`

- `confirmed` keeps the payee listed (or adds it by hand), `dismissed` hides it and keeps it out of committed spend and alerts, `auto` forgets your decision.
- `ended: true` marks it cancelled; `false` makes it active again. `kind` moves it to another group.
- `cadence` and `amount_expected` (up to 2 decimals) are used when detection alone wouldn't list the payee.
- 404 when you have no debits to that payee. Returns `{"id", "decision"}`. Audit-logged.

## `GET /api/cards`

Card accounts and their bill payments. A card purchase is spend on the card. A bank-side bill payment is matched to the card's own "payment received" credit: same amount, within ±4 days. When matched, both legs become `excluded` transfers, linked as `card_payment`. The match only happens if the statement the payment settles is on file: the latest card statement that closed before the payment, and no more than 45 days before it. Otherwise the bank payment stays in the `card` bucket and stands in for the card's purchases. Matching runs after every statement upload.

```json
{"items": [{"account": {"id": 3, "label": "HDFC Platinum ••4242"},
            "statement": {"period_start": "2026-08-15", "period_end": "2026-09-14", "total": "40058.00",
                          "due_date": "2026-10-03", "purchases": 19, "paid": "0.00", "paid_at": null,
                          "paid_from": null, "state": "unpaid"},
            "cycle": {"since": "2026-09-15", "amount": "5831.88", "count": 8, "seen_through": "2026-09-26"}}],
 "stand_in": {"amount": "388454.00", "count": 10, "first": "2025-10-01", "last": "2026-07-01"}}
```

- `statement` is the newest parsed card statement (`null` before the first one). `total` is its total due, else its closing balance. `paid` sums the matched payments after it closed. `state` is `paid`, `part_paid` or `unpaid`.
- `cycle` is spend on the card since that statement closed.
- `stand_in` is every bank bill payment still counted as spend.

## `GET /api/alerts?month=YYYY-MM`

Rule flags for the month cycle. Every one is computed from data; none is written by a model.

| `kind` | `severity` | Rule |
|---|---|---|
| `duplicate` | `bad` | Two or more debits with the same account, payee, amount and day |
| `bounce_risk` | `warn` | A recurring series due in the next 7 days, whose account's last known `balance` is below the charge |
| `price_increase` | `warn` | A steady-priced series whose latest charge, this month, is more than 5% above the one before |
| `missed` | `warn` | A series in state `late` (see [`/api/recurring`](#get-apirecurring)), in the current cycle |
| `budget_over` | `bad` | A budget's `state` is `over` (see [`/api/budgets`](#get-apibudgetsmonthyyyy-mm)) |
| `budget_pace` | `warn` | A budget's `state` is `ahead` |

```json
{"month": "2026-09", "items": [{"id": "dup:579", "kind": "duplicate", "severity": "bad",
  "title": "OpenRouter charged 2×", "detail": "₹1,037.44 × 2 on 18 Sep", "txn_ids": [579, 580]}]}
```

## `GET /api/inbox/stats?month=YYYY-MM`

How the month's txns were filed:

```json
{"month": "2026-09", "total": 51, "automatic": 48, "rules": 0,
 "by": {"rules": 0, "payee_memory": 0, "dictionary": 48, "structural": 0, "user": 0, "waiting": 3}}
```

- `rules` in `by` counts member and household rules (`rule:<id>`).
- `structural` covers salary, self-transfer and card-bill rules, reversals and the merchant-QR heuristic.
- `waiting` is the Inbox.
- `automatic` is everything filed except by hand.
- The top-level `rules` is the member's enabled rule count.

## `POST /api/inbox/undo`

Body: `{"txn_ids": [int] (1–500), "rule_id"?: "rule:<id>"}`. Puts back txns the member filed by hand. With `rule_id`, it also puts back every txn that rule filed, and deletes the rule (the member's own rules only). Returns `{"restored": 1, "rule_removed": true}`. Audit-logged.

## `GET /api/rules` and `PATCH /api/rules/{rule_id}`

`GET` lists the member's and the household's rules, newest first:

```json
{"items": [{"id": "rule:1", "scope": "member", "match": {"vpa": "rahul.m@okaxis", "direction": "debit"},
            "category": "Family", "enabled": true, "created_by": "user", "created_at": "2026-09-26T19:26:24Z",
            "hits": 0, "last_hit_at": null, "editable": true}]}
```

`PATCH /api/rules/rule:1` `{"enabled": false}` pauses one of the member's own rules. It returns `{"id", "enabled"}`, or 404 for a household rule or someone else's.

## Payee names: `/api/payee-aliases`, `GET /api/payees`

Your own name for a payee, e.g. a shop paid through its owner's UPI handle, or one store printed several ways. The name replaces `merchant` on every txn of the payee, now and at ingest, so payees given one name group as one merchant in Spending, trends and search. `payee_key` never changes. A merchant-name rule matches either the name or the name the payee had before.

`POST /api/payee-aliases` `{"name": "Sharma Sweets", "payee_keys": ["vpa:ramesh1234"]}` names 1–50 payees. A name equal to an existing merchant name but for case takes that spelling. Returns `{"name", "payee_keys", "updated", "similar"}`: txns renamed, and how many other payees now match the name. 404 when a payee has no txns.

`POST /api/payee-aliases/reset` `{"payee_keys": [...]}` drops the names; each payee's txns take back the name they had before the first rename. Returns `{"payee_keys", "restored"}`; 404 when none of them was named.

`GET /api/payee-aliases` lists your names and the payees that match one:

```json
{"items": [{"payee_key": "vpa:ramesh1234", "name": "Sharma Sweets", "original": "Ramesh", "count": 3}],
 "suggestions": [{"payee_key": "text:sharma sweets pune in", "merchant": "Sharma Sweets Pune In",
                  "counterparty": null, "vpa": null, "count": 1, "total": "1200.00", "last_at": "2026-09-09",
                  "suggest": {"name": "Sharma Sweets", "why": "prefix", "like": "Sharma Sweets"}}]}
```

A payee matches when any of its names (merchant or counterparty), compared on letters and digits only, matches a name or a named payee's old name (`like`):

| `why` | Rule |
|---|---|
| `prefix` | 8+ leading characters in common, or one is the start of the other (5+ characters) |
| `words` | At least half the words shared, one of 4+ letters. `pvt`, `ltd`, `store`, `traders` and the like don't count |
| `spelling` | 6+ characters, similarity ratio ≥ 0.85 |

`POST /api/payee-aliases/dismiss` `{"payee_key", "name"}` stops suggesting that name for that payee (the last 500 are kept).

`GET /api/payees?q=Style%20Hub` finds up to 20 payees whose name contains `q` (ignoring case, spaces and punctuation, `why: "contains"`) or matches it by the rules above: the candidates to give one name. Each item is `{"payee_key", "merchant", "counterparty", "vpa", "alias", "why", "count", "total", "last_at"}`; `alias` is your name for it, if any.

## `GET /api/networth/live`

The current net worth. Each component takes its **newest** dated value. The imported sheet isn't read: its EPF, gold and other rows were imported as values set by hand.

- **`statement`:** for `sbi` and `hdfc`, each statement's closing balance and the balance after the newest line. For others, a value read from a statement: the CAS month-end `stocks` and `mf`, PPF, FD, or an EPF passbook.
- **`prices`:** `stocks` and `mf` at the newest daily price on the latest CAS holdings. That's NSE's closing price for shares and ETFs, and AMFI's NAV for funds. It's used only when the price is newer than the CAS.
- **`manual`:** a value the member set (below). Only `epf`, `gold` and `other` can be set (`editable: true`); the rest have feeds, and `PUT` on them is a 422.
- **`estimate`:** EPF after its newest value. The usual monthly credit (the commonest month-on-month rise over the last 6 months) is added on each 1st, for up to 6 months.
- **Invested since:** money invested into a component after what its value includes is added at cost, until the next statement shows it. Examples: an SIP after the CAS date, or a PPF or FD deposit before the next statement. The component is picked by the payee label: SIP/NACH → `mf`, stocks → `stocks`, PPF → `ppf`, FD → `fd`.
- **Gold and other** hold their first value before it was set, as a base.

```json
{"as_of": "2026-09-27", "net_worth": "4875000.00", "liquid": "752300.00",
 "components": [{"key": "gold", "label": "Gold", "asset_class": "gold", "amount": "235000.00", "share_pct": 4.8,
                 "source": "manual", "as_of": "2026-09-27", "stale": false, "editable": true, "change_since": "15000.00"}],
 "by_asset_class": {"cash": "542300.00", "...": "..."},
 "changes": [{"period": "month", "since": "2026-09-01", "amount": "15000.00", "pct": 0.3},
             {"period": "year", "since": "2026-01-01", "amount": "645000.00", "pct": 15.2},
             {"period": "fy", "since": "2026-04-01", "amount": "485000.00", "pct": 11.1}],
 "history": [{"date": "2026-09-01", "net_worth": "4860000.00", "kind": "snapshot"},
             {"date": "2026-09-27", "net_worth": "4875000.00", "kind": "live"}],
 "months": [{"start": "2026-09-01", "end": "2026-09-27", "start_value": "4860000.00", "end_value": "4875000.00",
             "change": "15000.00", "cash_change": null, "contributions": "25000.00", "market": null, "live": true}],
 "projection": {"monthly_change": "79090.91", "basis_months": 11,
                "points": [{"date": "2026-12-27", "net_worth": "5097272.73"}, {"date": "2027-03-27", "net_worth": "5334545.45"}]}}
```

| Field | Notes |
|---|---|
| `stale` | The value is more than 30 days old |
| `change_since` | Against the month-end before the 1st of this month |
| `changes` | Against the month-end before the 1st of the month, 1 Jan, and 1 Apr (FY). `null` before history starts |
| `history` | Month-end points, oldest first, from the first month each core component held (`sbi`, `hdfc`, `stocks`, `mf`, `ppf`, `epf`) has a value; then today as `live`. Each point is every component's value on that date, computed the same way |
| `months` | The last 6 intervals between points, newest first: `change = cash_change + contributions + market`. Cash is `sbi + hdfc + fd`. Contributions are `invest`-bucket debits in the interval. Market is the rest, including anything set by hand. |
| `projection` | The average monthly change over up to the last 12 intervals, continued 3 and 6 months. `null` with fewer than 3 points |

No data at all gives `net_worth: null` and empty lists.

## `PUT /api/networth/components/{key}`

Body: `{"amount": "235000.00" | 235000, "as_of"?: "YYYY-MM-DD"}`. `key` is one of the nine sheet keys. `as_of` defaults to today, and a future date is a 422. It upserts by (key, `as_of`) and returns `{"key", "amount", "as_of"}`. Audit-logged.

## `GET /api/holdings`

Funds and stocks. Each holding shows its latest units times the newest price. The list is empty until the CDSL CAS parser lands (M1/M5).

```json
{"items": [{"name": "…", "isin": "INF…", "units": "6812.450000", "units_as_of": "2026-09-03", "source": "cas",
            "price": "86.4200", "price_date": "2026-09-26", "value": "588722.98"}]}
```

## `GET /api/sources/queue`

The Settings view of source health.

- `collected`: collector messages by `parse_status` (`parsed`, `ignored`, `needs_password`, `parser_needed`, `failed`).
- `unparsed`: raw messages no parser could read, grouped by sender and status (`subject` is the newest one). `needs_password` means no stored password opened the PDF.
- `uploads`: the last 5 statement uploads.

```json
{"unparsed": [{"sender": "upload", "subject": "icici_card_aug.pdf", "status": "parser_needed", "count": 1,
               "first_seen": "2026-09-26T17:26:45Z", "last_seen": "2026-09-26T17:26:45Z"}],
 "uploads": [{"id": 86, "filename": "sbi_aug.pdf", "received_at": "2026-09-02T09:10:00Z", "status": "parsed",
              "account": "SBI savings ••7373", "period_start": "2026-08-01", "period_end": "2026-08-31",
              "diff": "0.00", "reconciled": true}]}
```

---

## `POST /api/uploads`

Uploads one bank statement. The api stores the raw file, then parses → resolves → classifies → reconciles, synchronously.

- **Request:** `multipart/form-data` with the header `X-Requested-With: tijori`.

  | Field | Notes |
  |---|---|
  | `file` | Required. A statement **PDF** or its **`pdftotext -layout` text** (UTF-8). Max 15 MiB by default (`TIJORI_MAX_UPLOAD_BYTES`) |
  | `password` | Optional. The PDF password. It reaches `qpdf` on stdin only, and is never logged, stored or echoed back. Without it, a locked PDF is retried with the member's [saved statement passwords](#statement-passwords), most recent first |

- **Parsers:** SBI savings e-statements and HDFC savings statements. Anything else is kept as `parser_needed`.
- **Resolve:** each statement line has a stable identity, so re-sending a statement never duplicates a txn. That covers both the same file and another export of the same lines, and lines already loaded by the legacy import. Matching txns gain `"statement"` in `sources` and turn `reconciled` when the statement balances.
- **Storage:** the raw file is kept for re-parsing, content-addressed per member.

| Status | Body |
|---|---|
| 201 | New statement (below) |
| 200 | The exact same file again: `{"duplicate": true, "raw_message_id", "statement_id", "parse_status", "reconciliation"}` |
| 422 | Not a statement Tijori can read: `{"detail": "...", "raw_message_id": 87, "parse_status": "parser_needed" \| "failed"}` (kept for a future parser); or an unreadable or encrypted PDF without the right password |
| 415 | Neither PDF nor UTF-8 text |
| 503 | PDF tools missing on the server |

```json
{
  "statement_id": 84, "raw_message_id": 86, "duplicate": false,
  "parser": "sbi_statement", "parser_version": "1.0.0", "institution": "SBI",
  "account": {"id": 50, "institution": "SBI", "name": "SBI savings", "kind": "bank", "mask": "1234", "label": "SBI ••1234"},
  "period_start": "2026-04-01", "period_end": "2026-04-30", "opening": "1000.00", "closing": "43748.50",
  "reconciliation": {"ok": true, "line_count": 8, "debit_count": 5, "credit_count": 3,
                     "total_debits": "7365.00", "total_credits": "50113.50", "closing_diff": "0.00",
                     "chain_breaks": [], "footer_mismatches": []},
  "txns": {"lines": 8, "created": 8, "matched_existing": 0, "filed": 7, "inbox": 1}
}
```

| Field | Notes |
|---|---|
| `reconciliation.ok` | True only when the balance chain holds, `opening + credits − debits = closing` (`closing_diff: "0.00"`), and the counts and totals match the statement footer |
| `chain_breaks[]` | `{index, occurred_at, expected_balance, printed_balance}` |
| `footer_mismatches[]` | Human-readable strings |
| `txns.filed` / `txns.inbox` | Split of the newly created txns |

---

## Onboarding

### Invites

| Endpoint | Behaviour |
|---|---|
| `POST /api/invites` `{"email"}` | Household admin only (403 otherwise). 422 if the email is already a member. Returns 201 `{"email", "token", "url", "expires_at"}`. The token is shown **once**; only its SHA-256 is stored. Invites last 7 days and are single use |
| `GET /api/invites/{token}` | **No sign-in needed** (the landing page at `/invite/<token>` calls it). Returns `{"email", "household", "expires_at", "status": "valid"\|"used"\|"expired"}`, or 404 |

The landing page sends the invitee to `/auth/login?invite=<token>`. Sign-in must use exactly the invited email.

| Endpoint | Behaviour |
|---|---|
| `GET /api/household` | `{"id", "name", "members": [{"id", "name", "email", "role", "joined_at"}], "invites": [{"id", "email", "status": "pending"\|"accepted"\|"expired", "created_at", "expires_at"}]}`. Tokens are never listed |
| `DELETE /api/invites/{id}` | Admin only. Revokes a pending invite; 204, or 404 when it doesn't exist or was already used |

### `GET /api/onboarding` and `PATCH /api/onboarding`

```json
{"step": "profile", "completed_at": null,
 "steps": ["profile", "mail", "statement_passwords", "first_upload", "done"],
 "gmail_filter": "from:(alerts@hdfcbank.bank.in OR alerts@hdfcbank.net OR … OR ecas@cdslstatement.com) -subject:(OTP OR \"Instalment due\" OR \"Payment Reminder\" OR \"Daily Margin\" OR \"Portfolio Disclosure\")",
 "label": "tijori",
 "checklist": {"profile": true, "mail_source": false, "statement_passwords": false, "first_upload": false}}
```

- `step` is where the UI left the member.
- `gmail_filter` is the Gmail search to copy into **Create filter** for the label step, with **Apply the label** set to `label`.
  - The `from:` list holds exact transactional sender addresses (28 by default: HDFC, SBI, ICICI, Amazon Pay, CRED, Groww, CAMS, KFintech, CDSL). Exact addresses rather than domains keep bank marketing mail out of the label.
  - The `-subject:` part keeps OTPs and non-transaction notices out.
  - A member whose banks differ gets their own list from `settings.gmail_senders`. It's stored per member; there's no endpoint to edit it yet.
- `checklist` is derived from the data: own names set, a mail source exists, a statement password saved, a statement parsed.
- `PATCH` takes `{"step": "<one of steps>"}` and/or `{"completed": true|false}`. `completed: true` sets the step to `done` and stamps `completed_at`.

### Classifier profile: `GET /api/profile/classify` and `PUT /api/profile/classify`

These are the facts the structural rules need to recognise a member's own money. `PUT` replaces all five lists. The member's `local_shop_cap` is kept.

```json
{"own_names": ["ASHA VERMA"], "own_vpas": ["asha.verma@okhdfcbank"], "own_account_masks": ["1234", "9876"],
 "investment_account_masks": ["5555"], "employer_patterns": ["ACME WIDGETS"]}
```

| Field | Rule |
|---|---|
| `own_names` | ≤ 10 names, 1–120 chars. Banks truncate names, so a long enough prefix counts |
| `own_vpas` | ≤ 20 handles, 3–120 chars |
| `own_account_masks`, `investment_account_masks` | ≤ 20 each, exactly 4 digits |
| `employer_patterns` | ≤ 10, 3–80 chars. Matched as **case-insensitive substrings** of the narration, never as regexes |

It applies to statements uploaded after the change.

### Mail sources (IMAP with an app password)

| Endpoint | Behaviour |
|---|---|
| `GET /api/mail-sources` | `{"items": [MailSource]}`. The password is never returned |
| `POST /api/mail-sources` | Body `{"provider": "gmail"\|"outlook"\|"yahoo"\|"custom", "host"?, "port"?, "email", "app_password", "label": "tijori"}`. Returns 201 `MailSource` |
| `PATCH /api/mail-sources/{id}` | `{"app_password"?, "label"?}`: rotate the password or rename the label. Status goes back to `untested` |
| `DELETE /api/mail-sources/{id}` | Removes the source and its sealed password. 204 |
| `POST /api/mail-sources/test` | The same body as `POST /api/mail-sources`, tested **without storing anything**. It returns the same `{"ok", "message_count", "error_code"}` and shares the rate limit |
| `POST /api/mail-sources/{id}/test` | Connects over IMAP-TLS (10 s timeout), logs in, and `EXAMINE`s (read-only) the label. Returns `{"ok", "message_count", "error_code"}`. Rate-limited to 5 tests per member per 10 minutes (429) |

- **Presets:** `gmail` → `imap.gmail.com:993`, `outlook` → `outlook.office365.com:993`, `yahoo` → `imap.mail.yahoo.com:993`.
- **`custom`:**
  - needs a DNS hostname (no IP literals);
  - the port is 993 or 1024–65535;
  - the host must resolve to public addresses only, otherwise the test answers `host_not_public`.
- **Validation:** `label` must be 1–100 printable ASCII chars; `app_password` 1–256 chars without line breaks.
- **`error_code`:** server banners and exception text are never echoed, only these codes:
  - Login refused: `auth_failed` (wrong email or password), `app_password_required` (Gmail wants an App Password, not the account password), `imap_disabled` (IMAP is off for the account), `web_login_required` (the provider wants a browser sign-in first), `rate_limited` (the provider throttled logins; try later).
  - Everything else: `mailbox_not_found`, `dns_failed`, `host_not_public`, `tls_failed`, `timeout`, `connect_refused`, `connect_failed`, `protocol_error`.
- **Gmail App Passwords** are accepted with or without the spaces Gmail shows.

```json
{"id": 1, "provider": "gmail", "host": "imap.gmail.com", "port": 993, "email": "asha@example.test",
 "label": "tijori", "status": "ok", "last_tested_at": "2026-09-26T18:10:22Z", "last_error_code": null,
 "last_message_count": 38, "created_at": "2026-09-26T18:10:21Z"}
```

`status` is one of `untested`, `ok`, `error`. `last_message_count` is how many messages the label held at the last successful test.

### Statement passwords

| Endpoint | Behaviour |
|---|---|
| `PUT /api/accounts/{id}/statement-password` `{"password", "slot"?}` | Write-only; 204. 404 if the account isn't yours. `slot` is `main` (default) or `extra` |
| `DELETE /api/accounts/{id}/statement-password?slot=main\|extra` | 204, or 404 when none is saved |

Each account holds two passwords: `main`, and `extra` for a second code on the same account (the 4-digit code chosen in an SBI Quick `ESTMT` request). `GET /api/accounts` shows `has_statement_password` and `has_extra_statement_password`. The collector and uploads try every saved password on a locked PDF.

### Where secrets live

App passwords and statement passwords are sealed by SecretBox (AES-256-GCM envelope encryption):

- Each secret gets its own data key. The data key is wrapped by `TIJORI_MASTER_KEY`.
- The AAD is `<member_id>:<name>`, which binds each row to its member and purpose.
- Secrets never appear in a response, a log, or an error.

Stored mail and files (`TIJORI_BLOB_DIR`) are sealed the same way:

- Each file starts with `TJB1`, then a JSON header (wrapped data key, key name, nonce), then the AES-GCM ciphertext. The AAD is `<member_id>:blob:<sha256>`.
- The file name stays the SHA-256 of the plaintext, so dedupe still works.
- At start-up the worker seals any plaintext files left from before, in place. Reads accept both forms.

---

## Same-origin UI

When the image carries a web build (`TIJORI_WEB_DIST`, containing `index.html`):

- The api serves it as static files.
- Any other `GET` outside `/api`, `/auth`, `/health` and `/mcp` returns `index.html`, for client-side routes such as `/inbox`.
- Unknown `/api/...` paths, and missing files (any last path segment with a dot), return a JSON 404 rather than the app shell.
- `/favicon.ico` serves `favicon.svg` from the build.

HTML responses carry a CSP of `default-src 'self'` with no third-party origins (the UI bundles its fonts). API and `/auth` responses carry `Cache-Control: no-store`.

## Google sign-in (`/auth`)

Browser flow: OIDC authorization code with PKCE (S256), `state` and `nonce`, scope `openid email profile`. The API talks to Google's token endpoint directly over verified TLS. Tokens, codes and the client secret are never logged or stored.

| Endpoint | Behaviour |
|---|---|
| `GET /auth/login?return_to=/path&invite=<token>` | 302 to Google. `return_to` must be a relative path (anything else falls back to `/`). `invite` is optional, from an invite link. Sets a short-lived `tijori_login` cookie (10 minutes, path `/auth`) that ties the callback to this browser. 503 when sign-in isn't configured; 429 when too many sign-ins are pending |
| `GET /auth/callback` | Google redirects here. On success: 302 to `return_to` (default `/`) and sets the session cookie. On failure: 302 to `/?auth_error=<reason>` |
| `POST /auth/logout` | Needs a same-origin `Origin`. Ends the session server-side and clears the cookie. 204 |

- **ID token checks:** `iss` is Google; `aud` is our client id (`azp` too when `aud` is a list); `exp` and `iat` hold, with 60 s of skew; `nonce` matches; `email_verified` is true.
- **Who gets in:**
  - An email that is already a member signs in.
  - Otherwise, a valid [invite](#invites) for that exact email (sign in via `/auth/login?invite=<token>`) makes it a member of the inviting household.
  - Otherwise, an email in `TIJORI_ALLOWED_EMAILS` is registered as the admin of a new household, with the default categories.
  - Anyone else gets `auth_error=not_invited`.
- **`auth_error` values:** `cancelled`, `bad_request`, `state_invalid` (expired, reused, or opened in another browser), `exchange_failed`, `token_invalid`, `token_expired`, `email_unverified`, `not_invited`.
- **Session cookie:**
  - Named `__Host-tijori_session` when `TIJORI_PUBLIC_URL` is https (`tijori_session` on plain-http dev).
  - A random opaque id: HttpOnly, Secure, SameSite=Lax, Path=/, 30 days.
  - Only its SHA-256 is stored.
  - Each sign-in issues a new id and ends this browser's previous session.

## Mail collector

The `worker` service (`python -m tijori.collector`) polls each connected mailbox every 2 minutes. It uses read-only IMAP (EXAMINE, BODY.PEEK) and a UID watermark per mailbox (`mail_source.last_uid`, reset when UIDVALIDITY changes). `GET /api/mail-sources` adds `last_poll_at` and `last_poll_error`.

- Every message is stored raw first (`raw_message`, blobs), then routed:
  - Alert emails go to `parsers/alerts.py`: HDFC UPI, account and card alerts, SBI CBS alerts, ICICI card alerts and payments. Only INR amounts are taken.
  - Statement PDFs go to the statement parsers: HDFC card, ICICI card, HDFC combined email statement, SBI e-statement, plus the two netbanking formats.
  - CAMS account statements set holdings (units as of the NAV date).
- **Matching.** An alert becomes a `pending` txn, unless a txn on the same account with the same amount and direction exists within ±3 days, in which case it's attached as a sighting. A statement line matches first on its key. Otherwise it matches the same line from another statement format (same date, amount and direction), or an alert-only txn within ±3 days, which takes the statement's date, narration and key.
- **Passwords.** Statement PDFs open with vault entries named `statement_password:scheme:<hdfc|hdfc_custid|icici_card|sbi|pan>` (the scheme each bank states in its mail), or any `statement_password:account:<id>`. Stored messages that were `failed`, `parser_needed` or `needs_password` are re-read hourly, so a new parser or password picks them up.
- **CDSL e-CAS** (monthly) sets holdings for every demat ISIN and MF folio, and the `stocks` and `mf` net-worth components; AMFI's scheme names replace the wrapped names from the statement. Equity shares and ETFs held in demat count as `stocks`, other funds (folios, or units held in demat) as `mf`. The holdings must add up to the statement's Total Portfolio Value, or the parse fails and the message is retried.
- **Not in statement.** Once an account's statement is parsed, an alert-only txn still `pending` inside its period (3 days in from each edge) is flagged (`status: "flagged"`, `review_reason: "not_in_statement"`) and leaves the totals: a card hold, or a declined or reversed payment.
- **Net worth.** SBI e-statements print the PPF balance and HDFC statements the FD total. Both are stored as `component_value` with `source: "statement"`.
- **Prices.** Once a day the worker fetches AMFI's NAVAll.txt (fund NAVs) and NSE's end-of-day bhavcopy (share and ETF closes). It stores prices only for the ISINs you hold. The NSE request carries only the date, and a weekend or holiday (404) falls back to the day before.

## `POST /api/transactions/{id}/split` and `POST /api/transactions/{id}/unsplit`

`{"parts": [{"amount": "2000.00", "category_id": 4, "note"?: "…"}, …]}`, with 2–10 parts.

- The parts must add up to the txn to the paisa.
- The original stays, with `bucket` `excluded` and `split_parts: n`. Each part is a txn of its own, with `split_of`.
- Splitting again replaces the earlier parts. `unsplit` removes the parts and puts the original back on its category's bucket.
- `GET /api/transactions/{id}` adds `split_parts` (amount, category, note).

## Links: `POST /api/transactions/{id}/links`, `…/links/remove`, `GET …/link-candidates`

`{"txn_id": 123, "kind": "transfer" | "refund" | "dup" | "pass_through"}`.

- `transfer` and `pass_through` take both legs out of spend and income.
- `dup` takes the linked (second) txn out.
- `refund` only records the link.
- `card_payment` links are made automatically (see [`/api/cards`](#get-apicards)). They can be removed, which puts the bill payment back to standing in for card spend, but not added by hand.
- `link-candidates` lists up to 8 txns within 10 days: the same amount the other way (`suggest: "transfer"`), or the same way on the same day (`suggest: "dup"`).

## `GET /api/transactions/{id}/sources` and `GET /api/raw/attachments/{id}`

The emails and files a txn was read from.

- `sources` returns `[{raw_message_id, kind: "email"|"file", sender, subject, received_at, text, purged, files: [{id, filename}]}]`. `text` is the email's plain text (HTML is stripped and never rendered), up to 20,000 characters. Once [retention](#retention) has removed a message, `purged` is true and `text` is empty.
- `raw/attachments/{id}` downloads the original file (`Content-Disposition: attachment`, `nosniff`, `no-store`). It returns 404 once the file has been removed.

## `GET /api/recurring`: candidates

`candidates` lists subscription-like payees (a known subscription brand, or Bills & subscriptions) charged only once or twice in the last 400 days. `PUT /api/recurring/{id}` with `confirmed` tracks one. A payee with several fixed charges (four SIPs to one fund house) gets one series per amount, with the id `payee@amount`.

## Retention

Once a day the worker removes stored mail and files older than the member's `raw_retention_days`. Nothing is removed until the member picks a window (the default, `0`, keeps everything):

- A message that was `parsed` or `ignored` goes after N days. One still waiting gets 2N days, so a later parser or password can still read it: `failed`, `parser_needed`, `needs_password`, or a statement that didn't reconcile.
- The row stays, with `purged_at` set. Its txns, statements and holdings are untouched: only the email and the files go.
- A file is deleted only when no unremoved message or attachment still points at it.
- `0` turns retention off.

## Notifications

Pushes go through ntfy (`TIJORI_NTFY_URL`, with `TIJORI_NTFY_TOKEN` when the server needs one) to the member's `notify_topic`.

- Each [alert](#get-apialertsmonthyyyy-mm) of the current cycle is pushed once, when it first appears. `bad` alerts go at high priority. Sent ids are kept as `alert` rows of kind `push`.
- The first run after notifications are turned on records what is already open without sending it.
- On Mondays from 09:00 IST, a digest of the week before: spend and count against the week before that, the top 3 categories, charges due in the next 7 days, budgets over, and the Inbox count. Figures only.
- A failed post is logged and not retried, so nothing is sent twice.

`GET /api/ops/backup` returns the nightly off-site backup's last report: `{configured, last_run_at, last_ok, last_detail, last_ok_at, stale}`. `stale` means no good backup in 2 days, which also raises a `backup_stale` alert.

`POST /api/notify/test` sends a test push and returns `{"sent": true}`, or `false` when ntfy isn't configured or didn't answer. 422 without a topic.

## MCP (`POST /mcp`)

A Model Context Protocol server (protocol `2025-06-18`), so Claude can read your data. It speaks JSON-RPC 2.0 over HTTP POST, one JSON response per request; notifications get 202 with no body.

- **Auth:** `Authorization: Bearer tjm_…` only; the site cookie is not accepted. A missing or unknown token gets 401. The token binds row-level security like a web session.
- **Methods:** `initialize`, `ping`, `tools/list`, `tools/call`.
- **Masking:** person UPI handles come back as `ra***@okaxis`, since the output leaves Tijori. Merchant QR handles are kept.
- **Errors:** a tool's bad argument or missing row returns `isError: true` with a short message.

| Tool | Arguments | Returns |
|---|---|---|
| `get_month_summary` | `month` (default: this cycle) | The [summary](#get-apisummary) plus spend by category |
| `list_transactions` | `from`, `to`, `q`, `min`, `max`, `sort`, `limit` ≤ 100 | A page of txns and the totals of the whole filtered set |
| `list_subscriptions` | none | Recurring series: cost per month, next due date, state |
| `get_net_worth` | none | Live net worth by component and class, with month, year and FY changes |
| `list_alerts` | `month` | [Alerts](#get-apialertsmonthyyyy-mm), budgets included |
| `list_inbox` | none | Payees waiting to be filed, with their payments |
| `categorize` | `txn_id`, `category` (name), `scope` `this` \| `payee` | Files the txn. `payee` also files that payee's future payments. Audit-logged as `mcp:<token id>` |

### Tokens: `GET /api/mcp/tokens`, `POST /api/mcp/tokens`, `POST /api/mcp/tokens/{id}/revoke`

- `POST` `{"name": "Claude"}` creates a token and returns it once, in `token`; only its SHA-256 is stored. A member can have at most 10 live tokens.
- `GET` lists `[{id, name, created_at, last_used_at, revoked, token: null}]`.
- `revoke` stops a token at once. It is audit-logged.

| `list_loans` | none | [Loans](#loans): who, what's still owed, repayments |

Not built yet: OAuth for MCP (tokens are pasted by hand).

## Loans

Money lent to or borrowed from one person. A loan's txns carry `loan_id` and are filed under the **Loans** category (bucket `excluded`), so they never count as spend or income.

- **What's owed:** opening + money out − money in for a loan you `lent`; opening + money in − money out for one you `borrowed`. `opening_amount` is what was owed before the first txn on file.
- **Net worth:** open loans add `loans_given` (an asset) and `loans_taken` (negative) to [`/api/networth/live`](#get-apinetworthlive) components, with `source: "loans"` and `editable: false`.
- **Summary:** `/api/summary` adds `loans: {lent, repaid_to_you, borrowed, repaid_by_you}`, each `{amount, count, people}`, for the month cycle. Spending shows these outside the total.
- **Write-off:** what's left becomes one txn under the category you pick, dated today. A lent loan's becomes a debit, so a spend category counts it as spend. A borrowed loan's becomes a credit, so a people category counts it as income. Reopening removes that txn.
- **Auto-attach:** when payee memory files a payment under Loans, the worker attaches it to that handle's one open loan (not when the handle has two).

| Endpoint | Body | Notes |
|---|---|---|
| `GET /api/loans` | | `{items, unassigned, totals}`. `items`: open loans by amount, then closed ones. `unassigned`: txns under Loans without a loan. `totals`: `owed_to_you`, `you_owe`, `open_lent`, `open_borrowed`, `repaid_fy` (+ count, `fy_start`) |
| `GET /api/loans/{id}` | | `{loan, txns, suggestions}`. `txns` newest first with `balance_after`. `suggestions`: up to 10 unfiled payments from the loan's handles since a week before it started |
| `GET /api/loans/for-txn/{txn_id}` | | The filing picker: open loans with `same_payee` first, `new_direction` (a debit starts a loan you lent), and the handle's other unfiled payments |
| `POST /api/loans` | `{direction?, counterparty?, started_on?, opening_amount?, note?, txn_ids?}` | With `txn_ids`, direction, person and start come from the earliest txn. Without, `direction`, `counterparty` and `started_on` are required |
| `PATCH /api/loans/{id}` | `{counterparty?, note?, opening_amount?, started_on?}` | |
| `POST /api/loans/{id}/txns` | `{txn_ids}` (1–200) | Files them under Loans on this loan. Split originals are refused |
| `POST /api/loans/{id}/txns/remove` | `{txn_ids}` | Back to the Inbox, uncategorized (`review_reason: "loan_removed"`) |
| `POST /api/loans/{id}/settle`, `…/reopen` | | Closing sets `closed_on`; a closed loan owes nothing and leaves net worth |
| `POST /api/loans/{id}/write-off` | `{category_id}` | A spend category (`everyday` or `oneoff`) |

Every write is audit-logged. Txns in list and detail responses carry `loan_id`.
