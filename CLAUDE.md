# Tijori: notes for coding agents

Self-hosted personal finance for India: statements and alerts from the owner's mailbox, parsed, reconciled to the
paisa, classified without AI, shown in a web UI and served to agents over MCP. `README.md` has the overview;
`api/README.md` and `web/README.md` the layouts; `docs/api.md` is the HTTP and MCP contract.

## Open source, for anyone's money

Tijori is MIT-licensed and public, and is built for any self-hoster in India, not for the person writing the code.
- **No one's financial details in the repo**: not in code, comments, tests, fixtures, docs or commit messages. That
  covers statements, amounts, balances, account and card numbers, payees, merchants someone actually paid, order
  counts, salary days, names, emails and hosts. Examples are synthetic (`example.com`, masks like `••0001`, round
  made-up amounts) and look made up.
- **No solutions shaped around one person's setup.** Support a bank, card, mail format or merchant because it is a
  format real users have, built from its documented or observed layout; never special-case one person's accounts,
  categories, employer or habits. Personal preferences are settings or data a member enters, not code.
- **Justify a design with reasoning, not someone's history.** "Card charges land −1 to +2 days around the order" is
  fine; "40 of my 60 orders matched" is not. Measure on real data locally, and commit only the conclusion.
- Real data lives outside the repo (`.gitignore` covers `data/`, `samples/`, `*.pdf`, `*.local.*`, `PLAN.md`).

## Check your work

`scripts/check.sh` runs everything: api tests, web typecheck and build. `scripts/check.sh --db` also runs the
database tests against a throwaway, migrated Postgres 17 under real RLS. It uses uv/pnpm if installed, otherwise
Docker, so it works on a machine with neither. Run `--db` before committing anything that touches
`api/`; it takes about 30 s.

## Invariants that break silently

- **Money is `Decimal`, never float**, end to end. On the wire it is a 2-place string (`money.fmt`); parse with
  `money.parse_amount`.
- **Row-level security.** The api connects as `tijori_app` (no `BYPASSRLS`). Every request runs in one
  `member_session` that binds the RLS context, and queries still filter `member_id` explicitly so rows shared with
  the member never land in their own totals. A new table needs, in its migration: the grant to `tijori_app`,
  `ENABLE` and `FORCE ROW LEVEL SECURITY`, and a policy (copy `0019_item_category_rules.py`).
- **Migrations are expand-only**: the previous release must keep running on the new schema (`docs/deploy.md`).
  Revisions are sequential four-digit ids (`0020`, revises `0019`).
- **Every `/api` route is reachable over MCP or deliberately not.** `build_tools()` in `app/mcp.py` refuses to boot
  when a route is neither an action in `TOOLS` nor in `EXCLUDED` with a reason. Anything returned over MCP leaves
  Tijori: person UPI handles are masked there.
- **`docs/api.md` is the contract.** Change it in the same commit as the route; `web/src/lib/types.ts` mirrors it.
- **Statements reconcile against their own footer** (`parsers/reconcile.py`). One that doesn't is still stored,
  with `reconciled_at` null and the diff recorded: never "fix" a parser by nudging lines until the totals agree.
  `ParseError` means the layout isn't the one the parser knows. Registration order in `parsers/__init__.py` is
  most specific first.
- **Classification is deterministic**: rules, the brand dictionary and payee memory. No model calls in the api.
- **Web**: React and Vite only, exact pins, no router, state or chart library. The build must run under the
  api's `default-src 'self'` CSP: no inline scripts, no third-party requests. Secret inputs are write-only, and
  server error text is never shown; codes map to the UI's own messages.

## Dependencies

Exact pins, subject to the uv `exclude-newer` cooldown. After a lock change, regenerate `api/requirements.lock.txt`
(command in `api/README.md`).

## Tests

Tests exist to hold the owner's requirements, not to restate the code. Write one only when it encodes:
- a security or data rule: RLS, the owner lock, token scopes, masking, money handling;
- a bug that actually happened, written from the incident rather than from the fix (say which in the docstring);
- the API or MCP contract other agents depend on.

Never derive expected values by running the code under test. Never change an existing test's expectation to make
it pass without saying so, and why, in the commit body: that is the moment a bug becomes "correct". Parsers are
checked by reconciliation, not unit tests.

## Commits and deploys

Subject: `Area: outcome in plain words` (`Orders: …`, `Net worth: …`, `MCP: …`). The body says why: the problem
a user would see, and the reasoning behind the choice. Never skip hooks.
`main` may be deployed by the pull timer within minutes (`deploy/pull-update.sh`): never push a red `main`.

## Style

Match the surrounding code: dense module docstrings that explain why, comments only where the reason isn't
obvious, short precise prose in docs. Write user-facing text for whoever runs their own instance, not for the maintainer.
