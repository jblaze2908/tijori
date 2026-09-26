# Tijori web

The UI is React 19 + TypeScript + Vite 8, with exact pinned versions and nothing else. There is no router, state or chart library:
- routing is a small History API router (`src/lib/router.ts`)
- data comes through a shared request cache (`src/lib/api.ts`)
- charts are hand-written SVG (`src/charts.ts`), hosted by `components/ChartView.tsx`

## Run in development

Run the backend on `localhost:8310` with `TIJORI_ENV=dev` (see `docs/api.md`). Then:

```sh
pnpm install --ignore-scripts
TIJORI_DEV_MEMBER=you@example.com pnpm run dev   # http://localhost:5173
```

Vite proxies `/api`, `/auth` and `/health` to the backend. The proxy also adds `X-Tijori-Dev-Member` from `TIJORI_DEV_MEMBER`, which is the header a dev backend uses to identify you. The browser code never sends it. To point the proxy elsewhere, use `TIJORI_API_URL`. Both variables can also go in `web/.env.local`, which is gitignored.

A `401` from `/api/me` sends the app to `/welcome`, where sign-in with Google goes through `/auth/login`. `/invite/:token` is the other public page. Landing on `/` with setup unfinished resumes `/onboarding/:step`. When the backend is down, each page shows an error with a retry button.

## Check and build

```sh
pnpm run typecheck       # tsc --noEmit
pnpm run build           # writes dist/: index.html, favicon.svg, assets/
```

The api serves `dist/` from the same origin as `TIJORI_WEB_DIST` (docs/api.md, "Same-origin UI"), with an SPA fallback to `index.html`. The build has no inline scripts, no inline favicon and no third-party requests, so it runs under the api's `default-src 'self'` CSP with no exceptions.

**Fonts.** Geist and Geist Mono are self-hosted in `public/fonts/` as the variable woff2 files from the official [vercel/geist-font](https://github.com/vercel/geist-font) release **v1.7.2** (published 2026-06-01, the maintainers' Latest). The release zip's SHA-256 was checked against GitHub's digest `7fc800d2…82b04e2`. They're licensed under SIL OFL 1.1 (`public/fonts/OFL.txt`).

## Contract

`docs/api.md` is the source of truth. `src/lib/types.ts` mirrors it, and `src/lib/api.ts` holds every call and wire-to-model conversion. What the UI calls beyond `docs/api.md` is listed in `API_ASSUMPTIONS.md`.

Transactions are fetched per calendar month by explicit dates (`from=`/`to=`, `page_size=200`, pages in parallel) and cached. Every range view, whether a salary cycle, a week or a period from Trends, is assembled from those slices.

Every total shown is the server's: `/api/summary` and `/api/trends` apply `month_start_day` themselves. The client only derives comparison views over the same transactions: pace, "at this point" deltas, movers, the heatmap and alerts.

## Layout

| Path | What's there |
|---|---|
| `src/App.tsx` | shell, month switcher, 401 → `/welcome` |
| `src/ctx.tsx` | month context (respects `month_start_day`), month gate, range helpers |
| `src/lib/api.ts`, `useStore.ts` | request cache and React subscription; mutations `categorize`, `fileInboxPayee`, `saveRemark` |
| `src/lib/insights.ts`, `periods.ts`, `format.ts` | pure maths: pace, period stats, movers, alerts, allocation; weeks, cycles, quarters, FY; money and dates |
| `src/charts.ts` | SVG line, columns, diverging bars, heatmap, donut, sparkline. `html``` escapes all text |
| `src/pages/` | Overview, Activity, Trends (week/month/quarter/FY, derived from cached month slices or `GET /api/trends`), Inbox, Net worth, Welcome, Invite |
| `src/pages/Onboarding.tsx`, `setup/` | `/onboarding/:step`, six steps (profile, accounts, mail, Gmail label, statement passwords, first import) that resume where you left off. The same forms back `/settings/:section` (general, mail sources, accounts, statement passwords, household) |
| `src/lib/setup.ts`, `components/forms.tsx` | onboarding and settings calls; write-only secret inputs (never prefilled or echoed, cleared after every submit) |

API text reaches the DOM only through React's own escaping, or through `html``` inside charts.

There are no unit tests, because the project tests end to end only.
