# Tijori API (M0)

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
| Spend | **One definition everywhere:** debits in the `everyday`, `oneoff` and `card` buckets, plus uncategorized (Inbox) debits. Refunds are reported separately and never netted. `/api/summary` `expense` and the `/api/trends` totals use exactly this rule, so a headline number always equals its trend bar |
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
  "account": {"id": 50, "institution": "SBI", "name": "SBI savings", "label": "SBI ••1234", "kind": "bank", "mask": "1234"},
  "category": {"id": 1151, "name": "Groceries"}, "bucket": "everyday",
  "classified_by": "dictionary", "rule_id": "dict:blinkit", "review_reason": null,
  "status": "reconciled", "sources": ["statement"], "notes": null, "tags": []
}
```

| Field | Notes |
|---|---|
| `merchant` | Normalised display name |
| `counterparty` | The payee as the bank printed it |
| `vpa` | The UPI handle. **A person's handle is masked** (`"90•••"`, `"me•••@okaxis"`), and so is its appearance inside `narration`. Merchant and brand handles are left as they are |
| `payee_key` | The stable payee identity used by the Inbox and payee memory. It can contain `:` and `\|`, so URL-encode it in paths |
| `account` | `null` when the txn has no account. `mask` is the last 4 digits only; `label` is ready to display |
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

`GET` returns `{"month_start_day": 1, "local_shop_cap": "500.00"}`.

`PATCH` takes a JSON body with at least one of the fields below, and returns the new settings. It is audit-logged.

| Field | Type | Notes |
|---|---|---|
| `month_start_day` | integer 1–28 | The salary-cycle start. It moves every month boundary: summary, months, the transactions `month` filter, budgets, and trend months, quarters and FYs |
| `local_shop_cap` | money string (`"750.00"`) or integer, 0–1,00,000 | A merchant-QR payment up to this amount auto-files as Local shops. It applies to statements uploaded after the change |

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
            "has_statement_password": false, "last_seen_at": null, "coverage_pct": null}]}
```

`last_seen_at` (last live alert) and `coverage_pct` (share of statement lines seen live) stay `null` until the collectors land in M1.

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
| `everyday`, `oneoff`, `card` | Sum of **debits** in that bucket. `card` is CRED/credit-card bill payments, which stand in for card spend until card statements are itemised (M1) |
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
| `account` | int | Account id |
| `category` | int id, or `none` | `none` returns only Inbox txns |
| `kind` | enum | |
| `direction` | `debit` \| `credit` | |
| `q` | string, 1–100 chars | Case-insensitive substring match on narration or merchant. `%` and `_` match literally |
| `min`, `max` | decimal ≥ 0, up to 2 places | Inclusive. `min > max` is a 422 |
| `page`, `page_size` | int | See paging |

Returns `{"items": [Txn], "page": 1, "page_size": 50, "total": 16}`.

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
            "history": [{"category_id": 1151, "category": "Groceries", "count": 2}]}
}
```

- `observations` lists every sighting behind the txn. Legacy-imported txns have none until their statement is uploaded.
- `links` is empty in M0; the resolver fills it in M1.
- `payee` gives this payee's totals in the same direction. It is `null` without a `payee_key`.

## `POST /api/transactions/{id}/category`

Body: `{"category_id": 1151, "scope": "this"}`. Name the category with **exactly one** of `category_id` or `category` (its name, e.g. `"Family"`). `scope` defaults to `this`.

| `scope` | Effect |
|---|---|
| `this` | Files this txn: `classified_by: "user"`, `rule_id: "user"`. That counts as a confirmation for payee memory |
| `payee` | As `this`, and also creates a member rule. The rule matches the payee's UPI handle, or its merchant name when there is no handle, in this direction. It is applied at once to the payee's other txns in the same direction that you haven't filed by hand (`classified_by: "rule"`) |

Returns `{"updated": 1, "rule_id": null}`, or `{"updated": 4, "rule_id": "rule:12"}` for scope `payee`.

Errors:
- 404: the txn isn't yours.
- 422: unknown or invisible category, or a payee with nothing to build a rule from.

The call is audit-logged.

## `GET /api/categories`

Categories visible to the member: household-wide ones plus the member's own, in taxonomy order.

```json
[{"id": 1, "name": "Groceries", "description": "Supermarkets and quick commerce: Blinkit, JioMart, Zepto, BigBasket.",
  "kind": "spend", "bucket": "everyday", "parent_id": null, "scope": "household"}]
```

The default taxonomy has 25 categories:

- **Spend (14):** Groceries, Eating out, Shopping, Bills & subscriptions, Local shops, Travel, Health, Services, Bank charges, Family, Entertainment, Insurance, Tax, Cash.
- **Non-spend (11):** Salary, Interest, Dividends, Other income, Refunds, Reversals, Self transfer, Card bill payment, Pass-through, Investments, Investment redemptions.

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
{"month": "2026-04", "items": [{"category_id": 5, "category": "Groceries", "amount": "8000.00",
                                "spent": "1372.00", "remaining": "6628.00", "rollover": false}]}
```

`spent` covers the month cycle. `items` is empty until budgets can be set (M4).

## `GET /api/trends`

Spend over time. Aggregation happens in SQL, bucketed by `date_trunc`.

| Param | Default | Notes |
|---|---|---|
| `granularity` | `month` | `week` (weeks start Monday), `month`, `quarter`, `fy` (Indian financial year, Apr–Mar) |
| `periods` | 12 | 1–60. That many periods, ending with the one that contains `end` |
| `end` | today (IST) | `YYYY-MM-DD` |
| `group_by` | `total` | `total`, `category`, `merchant`, `kind` |
| `limit` | 10 | 1–50. For `category` and `merchant`: the top N series by total, the rest merged into `"Other"` |

The member's `month_start_day` shifts month, quarter and FY boundaries. For example, day 25 gives months running 25th to 24th. Weeks ignore it.

- **Spend** uses the one definition (see Conventions): debits in `everyday`, `oneoff` and `card` plus uncategorized debits, with refunds not netted. A month's `total` equals `/api/summary` `expense` for the same month cycle, and `income`/`invested` equal its `income`/`invest`.
- **`group_by=total`** returns six series, all on the summary's rules:
  - `total`: spend.
  - `committed` + `discretionary`: spend split by whether the merchant has an active recurring series. `committed` stays 0 until recurring detection lands in M2.
  - `income`: credits in the `income` bucket.
  - `refunds`: the refund part of income.
  - `invested`: debits in the `invest` bucket.
- **`group_by=category|merchant`** splits `total` (spend) into series.
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
 "gmail_filter": "from:(hdfcbank OR sbi OR icicibank OR amazonpay OR cred.club OR groww OR camsonline OR kfintech OR cdslindia OR cdslstatement OR pluxee) -subject:(OTP OR \"Instalment due\" OR \"Payment Reminder\" OR \"Daily Margin\" OR \"Portfolio Disclosure\")",
 "label": "tijori",
 "checklist": {"profile": true, "mail_source": false, "statement_passwords": false, "first_upload": false}}
```

- `step` is where the UI left the member.
- `gmail_filter` is the Gmail search to copy into **Create filter** for the label step, with **Apply the label** set to `label`. Its `from:` tokens are the sender domains of the supported institutions, a starting point the member can widen in Gmail. The `-subject:` part keeps OTPs and non-transaction notices out.
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
 "created_at": "2026-09-26T18:10:21Z"}
```

`status` is one of `untested`, `ok`, `error`.

### Statement passwords

| Endpoint | Behaviour |
|---|---|
| `PUT /api/accounts/{id}/statement-password` `{"password"}` | Write-only; 204. 404 if the account isn't yours |
| `DELETE /api/accounts/{id}/statement-password` | 204, or 404 when none is saved |

`GET /api/accounts` shows `has_statement_password`. Uploads use saved passwords for locked PDFs.

### Where secrets live

App passwords and statement passwords are sealed by SecretBox (AES-256-GCM envelope encryption):

- Each secret gets its own data key. The data key is wrapped by `TIJORI_MASTER_KEY`.
- The AAD is `<member_id>:<name>`, which binds each row to its member and purpose.
- Secrets never appear in a response, a log, or an error.

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

Not in M0: recurring, alerts, split/notes/tags writes, MCP. The UI should treat a 404 on those routes as "section unavailable".
