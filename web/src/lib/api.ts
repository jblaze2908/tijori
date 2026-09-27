import { dayIST, dayLong, inr, monthEnd, toPaise } from "./format";
import type {
  Account,
  ApiInboxGroup,
  ApiInboxItem,
  ApiMonths,
  ApiNetWorth,
  ApiPage,
  ApiCards,
  LinkCandidate,
  LinkKind,
  RawSource,
  ApiRecurringList,
  Cadence,
  RecurringKind,
  RecurringList,
  ApiSnapshot,
  ApiSummary,
  ApiTotals,
  ApiTransactionDetail,
  ApiTrends,
  ApiTxn,
  ApiTxnDetail,
  ApiTxnPage,
  Granularity,
  Holding,
  InboxStats,
  ISODate,
  LiveNetWorth,
  ParseQueue,
  Rule,
  TxnPage,
  TxnQuery,
  Budgets,
  Category,
  Component,
  Inbox,
  InboxPayee,
  Me,
  MonthKey,
  MonthSummary,
  Recurring,
  Scope,
  ServerAlert,
  Settings,
  Snapshot,
  Totals,
  Transaction,
  TransactionDetail,
  TrendPoint,
  TxnSource,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const DEV_HINT = import.meta.env.DEV ? " Is the backend running on :8310?" : "";

// User-facing text only; response bodies are never shown, so server internals and untrusted text stay out of the UI.
function messageFor(status: number): string {
  if (status === 0 || status === 502 || status === 503 || status === 504) return `Can't reach the Tijori server.${DEV_HINT}`;
  if (status === 401) return "You're signed out.";
  if (status === 403) return "This login isn't a Tijori member.";
  if (status === 404 || status === 405) return "The server doesn't support that yet.";
  if (status === 422) return "The server couldn't accept that. Check the fields and try again.";
  if (status === 429) return "Too many attempts. Wait a few minutes and try again.";
  if (status >= 500) return `The server hit an error. Try again in a moment.${DEV_HINT}`;
  return `The request failed (${status}).`;
}

type Method = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export async function request<T>(path: string, method: Method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, messageFor(0));
  }
  if (!res.ok) throw new ApiError(res.status, messageFor(res.status));
  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(res.status, "The server sent a response Tijori couldn't read.");
  }
}

const toApiError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(-1, "Tijori couldn't read the server's data."));

// ---------- resource cache ----------
// Keyed by request path so screens that need the same data share one request. Mutations mark keys stale;
// a stale entry keeps showing its old data until the refetch lands, so pages never flash back to loading.

export type ResourceState<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: ApiError };

export interface Resource<T> {
  key: string;
  load: () => Promise<T>;
}

interface Entry {
  state: ResourceState<unknown>;
  stale: boolean;
  inflight: boolean;
}

const store = new Map<string, Entry>();
const listeners = new Set<() => void>();
let queued = false;
let version = 0;

function emit() {
  if (queued) return;
  queued = true;
  queueMicrotask(() => {
    queued = false;
    version++;
    for (const f of listeners) f();
  });
}
export function onChange(f: () => void) {
  listeners.add(f);
  return () => void listeners.delete(f);
}
/** Bumped once per batch of cache changes; React subscribes with useSyncExternalStore. */
export const getVersion = () => version;

/** Current state of r, starting (or restarting a stale) fetch as a side effect. */
export function read<T>(r: Resource<T>): ResourceState<T> {
  let e = store.get(r.key);
  if (!e) {
    e = { state: { status: "loading" }, stale: true, inflight: false };
    store.set(r.key, e);
  }
  if (e.stale && !e.inflight) {
    const entry = e;
    entry.stale = false;
    entry.inflight = true;
    if (entry.state.status === "error") entry.state = { status: "loading" };
    r.load()
      .then(
        (data) => void (entry.state = { status: "ready", data }),
        (err) => void (entry.state = { status: "error", error: toApiError(err) }),
      )
      .finally(() => {
        entry.inflight = false;
        if (store.get(r.key) === entry) emit();
      });
  }
  return e.state as ResourceState<T>;
}

export const dataOf = <T>(s: ResourceState<T>): T | null => (s.status === "ready" ? s.data : null);

/** Marks cached paths starting with any prefix stale; the next read refetches them. */
export function invalidate(prefixes: string[]) {
  for (const [key, e] of store) if (prefixes.some((p) => key.startsWith(p))) e.stale = true;
  emit();
}

/** Seeds a cache entry with what the server just returned, so readers see it now rather than after a refetch. */
export function prime<T>(key: string, data: T) {
  store.set(key, { state: { status: "ready", data }, stale: false, inflight: false });
  emit();
}

/** Failures are retried on the next visit rather than cached for the session. */
export function retryFailed() {
  let any = false;
  for (const e of store.values())
    if (e.state.status === "error") {
      e.stale = true;
      any = true;
    }
  if (any) emit();
}

/** Folds required resources into one state: first error wins, then loading, else all data. */
export function all<T extends unknown[]>(...rs: { [K in keyof T]: ResourceState<T[K]> }): ResourceState<T> {
  for (const r of rs) if (r.status === "error") return r;
  for (const r of rs) if (r.status === "loading") return r;
  return { status: "ready", data: rs.map((r) => (r as { data: unknown }).data) as T };
}

export function resource<W, T>(key: string, map: (w: W) => T): Resource<T> {
  return { key, load: () => request<W>(key).then(map) };
}

/** Endpoints not in docs/api.md yet: a 404/405 resolves to null so the UI hides that section. */
export function optional<W, T>(key: string, map: (w: W) => T): Resource<T | null> {
  return {
    key,
    load: () =>
      request<W>(key).then(map, (e: unknown) => {
        if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return null;
        throw e;
      }),
  };
}

const qs = (o: Record<string, string | number>) => new URLSearchParams(Object.entries(o).map(([k, v]) => [k, String(v)])).toString();

/** A filter as a stable query string: repeated keys for multi-selects, empty values dropped. */
export function txnQueryString(f: TxnQuery, page: number, pageSize: number): string {
  const p = new URLSearchParams();
  const put = (k: string, v: string | undefined) => v && p.append(k, v);
  put("from", f.from);
  put("to", f.to);
  put("q", f.q?.trim());
  put("min", f.min);
  put("max", f.max);
  for (const a of f.accounts ?? []) p.append("account", String(a));
  for (const c of f.categories ?? []) p.append("category", c);
  put("kind", f.kind);
  put("direction", f.direction);
  put("sort", f.sort && f.sort !== "date_desc" ? f.sort : undefined);
  put("paid_with", f.paidWith);
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
  return p.toString();
}

const mapPage = (r: ApiTxnPage): TxnPage => ({
  items: r.items.map(mapTxn),
  total: r.total,
  totals: Object.fromEntries(Object.entries(r.totals).map(([k, v]) => [k, { amount: toPaise(v.amount), count: v.count }])) as TxnPage["totals"],
});

// ---------- mapping wire → model ----------

function accountLabel(a: ApiTxn["account"]): string {
  if (!a) return "Unknown account";
  return a.label || [a.name ?? a.institution, a.mask ? `••${a.mask}` : null].filter(Boolean).join(" ");
}

const mapTxn = (t: ApiTxn): Transaction => ({
  id: String(t.id),
  date: t.occurred_at,
  account_id: t.account ? String(t.account.id) : null,
  account: accountLabel(t.account),
  account_kind: t.account?.kind ?? null,
  sources: t.sources ?? [],
  merchant: t.merchant ?? t.narration ?? "Unknown payee",
  category: t.category?.name ?? null,
  category_id: t.category?.id ?? null,
  bucket: t.bucket,
  kind: t.kind,
  amount: toPaise(t.amount),
  direction: t.direction,
  status: t.status,
  classified_by: t.classified_by,
  rule_id: t.rule_id,
  payee_key: t.payee_key,
  vpa: t.vpa,
  narration: t.narration,
  counterparty: t.counterparty,
  review_reason: t.review_reason,
  notes: t.notes,
  tags: t.tags ?? [],
  settles: t.settles ?? null,
  split_of: t.split_of != null ? String(t.split_of) : null,
  split_parts: t.split_parts ?? 0,
  loan_id: t.loan_id ?? null,
});

const byDate = (a: { date: string }, b: { date: string }) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0);

const mapTotals = (t: ApiTotals): Totals => ({
  expense: toPaise(t.expense),
  income: toPaise(t.income),
  invest: toPaise(t.invest),
  card: toPaise(t.card),
  refunds: t.refunds != null ? toPaise(t.refunds) : null,
});

const mapSummary = (s: ApiSummary): MonthSummary => ({
  totals: mapTotals(s.totals),
  previous: mapTotals(s.previous_totals),
  categories: s.categories.map((c) => ({ category: c.name, amount: toPaise(c.amount), previous: toPaise(c.previous_amount) })),
});

function payee(
  key: string,
  t: Transaction,
  reason: string | null,
  history: InboxPayee["history"],
): InboxPayee {
  return {
    key,
    payeeKey: t.payee_key,
    payee: t.merchant,
    vpa: t.vpa,
    direction: t.direction,
    account: t.account,
    reason,
    suggestedCategory: history[0]?.category ?? null,
    history,
    payments: [],
    total: 0,
  };
}

/** group=payee (docs/api.md) is what the UI asks for; a per-transaction page is grouped here the same way. */
function mapInbox(page: ApiPage<ApiInboxItem | ApiInboxGroup>): Inbox {
  const groups = new Map<string, InboxPayee>();
  for (const it of page.items) {
    const grouped = !("txn" in it);
    for (const raw of grouped ? it.txns : [it.txn]) {
      const t = mapTxn(raw);
      const key = `${t.direction}:${grouped ? it.payee_key : (t.payee_key ?? `txn:${t.id}`)}`;
      let g = groups.get(key);
      if (!g) {
        g = payee(key, t, it.reason, grouped ? it.history : it.payee_history);
        if (grouped) {
          g.payeeKey = it.payee_key;
          g.payee = it.display || t.merchant;
          g.suggestedCategory = it.suggestion?.category ?? null;
        }
        groups.set(key, g);
      }
      g.payments.push({ id: t.id, date: t.date, amount: t.amount });
      g.total += t.amount;
    }
  }
  for (const g of groups.values()) g.payments.sort(byDate);
  return { items: [...groups.values()], total: page.total, fetched: page.items.length };
}

/** Snapshots arrive newest first (docs/api.md); the UI works oldest first. Blank sheet cells (null) are left out. */
function mapNetWorth(r: ApiNetWorth): Snapshot[] {
  const meta = new Map(r.latest?.components.map((c) => [c.key, c]));
  const title = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const liquidClasses = new Set(["cash", "deposits"]);
  const snaps = [...r.snapshots].sort((a, b) => (a.date < b.date ? -1 : 1));
  const out: Snapshot[] = [];
  snaps.forEach((s: ApiSnapshot, i) => {
    const components: Component[] = Object.entries(s.components).flatMap(([key, amount]) =>
      amount == null ? [] : [{ key, label: meta.get(key)?.label ?? title(key), assetClass: meta.get(key)?.asset_class ?? "other", amount: toPaise(amount) }],
    );
    // docs/api.md's liquid is sbi + hdfc + fd (cash + deposits); recomputed the same way only if it's null.
    const liquid = s.liquid != null ? toPaise(s.liquid) : components.filter((c) => liquidClasses.has(c.assetClass)).reduce((a, c) => a + c.amount, 0);
    const netWorth = toPaise(s.net_worth);
    const prev = out[i - 1];
    out.push({
      date: s.date,
      netWorth,
      liquid,
      netChange: s.net_change != null ? toPaise(s.net_change) : prev ? netWorth - prev.netWorth : null,
      liquidChange: s.liquid_change != null ? toPaise(s.liquid_change) : prev ? liquid - prev.liquid : null,
      components,
      remark: s.remark,
      commentary: s.commentary,
      commentarySource: s.commentary_source ?? null,
    });
  });
  return out;
}

const PAGE_SIZE = 200;
const MAX_PAGES = 25;

/** The first page gives the total; the remaining pages load in parallel, capped at MAX_PAGES. */
async function rangeTxns(from: string, to: string): Promise<Transaction[]> {
  const url = (page: number) => `/api/transactions?${qs({ from, to, page, page_size: PAGE_SIZE })}`;
  const first = await request<ApiPage<ApiTxn>>(url(1));
  const pages = Math.min(MAX_PAGES, Math.ceil(first.total / PAGE_SIZE));
  const rest = await Promise.all(Array.from({ length: Math.max(0, pages - 1) }, (_, i) => request<ApiPage<ApiTxn>>(url(i + 2))));
  // Clipped to the range as well, so slices can never overlap even if a server ignored from/to.
  return [first, ...rest]
    .flatMap((p) => p.items.map(mapTxn))
    .filter((t) => t.date >= from && t.date <= to)
    .sort(byDate);
}

// ---------- endpoints ----------

export const api = {
  summary: (month: MonthKey) => resource(`/api/summary?${qs({ month })}`, mapSummary),
  /**
   * Calendar-month slices by explicit from/to dates (the server's `month=` follows month_start_day); every range
   * view is assembled from these, so each calendar month is fetched once.
   */
  month: (month: MonthKey): Resource<Transaction[]> => {
    const from = `${month}-01`;
    const to = monthEnd(month);
    return { key: `/api/transactions?${qs({ from, to })}`, load: () => rangeTxns(from, to) };
  },
  categories: () => resource("/api/categories", (r: Category[]) => r),
  inbox: () => resource(`/api/inbox?${qs({ group: "payee", page_size: PAGE_SIZE })}`, mapInbox),
  networth: () => resource("/api/networth", mapNetWorth),

  months: () => optional("/api/months", (m: ApiMonths) => m),
  me: () => optional("/api/me", (m: Me) => m),
  accounts: () => optional("/api/accounts", (r: { items: Account[] }) => r.items),
  settings: () =>
    optional("/api/settings", (r: { month_start_day?: number }): Settings => {
      const d = Number(r.month_start_day);
      return { monthStartDay: Number.isInteger(d) && d >= 1 && d <= 28 ? d : 1 };
    }),
  transaction: (id: string) =>
    optional(`/api/transactions/${encodeURIComponent(id)}`, (d: ApiTransactionDetail): TransactionDetail => ({
      payee: d.payee ? { count: d.payee.count, total: toPaise(d.payee.total) } : null,
      observations: d.observations.map((o) => ({
        label: [SOURCE_LABEL[o.source] ?? o.source, o.parser].filter(Boolean).join(" · "),
        detail: [o.received_at ? `received ${dayLong(dayIST(o.received_at))}` : null, o.balance_after ? `balance after ${inr(toPaise(o.balance_after))}` : null]
          .filter(Boolean)
          .join(" · "),
      })),
    })),
  budgets: (month: MonthKey) =>
    optional(`/api/budgets?${qs({ month })}`, (r: { items: { category: string; amount: string }[] }): Budgets =>
      new Map(r.items.map((b) => [b.category, toPaise(b.amount)])),
    ),
  /** Spend by category per period, server-side (docs/api.md); `limit` high enough that the UI does its own "Other". */
  trends: (granularity: Granularity, periods: number, end: string) =>
    optional(`/api/trends?${qs({ granularity, periods, end, group_by: "category", limit: 50 })}`, flattenTrends),
  /** group_by=total: total, committed, discretionary, income, refunds and invested per period, on the summary's rules. */
  trendTotals: (granularity: Granularity, periods: number, end: string) =>
    optional(`/api/trends?${qs({ granularity, periods, end, group_by: "total" })}`, flattenTrends),
  /** Active series only: what Spending counts as recurring. */
  recurring: () => optional("/api/recurring", (r: ApiRecurringList): Recurring[] => mapRecurring(r).items.filter((x) => x.active)),
  subscriptions: () => resource("/api/recurring", mapRecurring),
  cards: () => resource("/api/cards", (r: ApiCards) => r),
  linkCandidates: (id: string) => resource(`/api/transactions/${encodeURIComponent(id)}/link-candidates`, (r: { items: LinkCandidate[] }) => r.items),
  txnSources: (id: string) => resource(`/api/transactions/${encodeURIComponent(id)}/sources`, (r: { items: RawSource[] }) => r.items),
  alerts: (month: MonthKey) => optional(`/api/alerts?${qs({ month })}`, (r: { items: ServerAlert[] }) => r.items),
  /** One page of a filtered list, with the whole set's totals. */
  txnPage: (f: TxnQuery, page: number, pageSize = 50) => resource(`/api/transactions?${txnQueryString(f, page, pageSize)}`, mapPage),
  txnDetail: (id: string) =>
    resource(`/api/transactions/${encodeURIComponent(id)}`, (d: ApiTxnDetail) => ({ ...d, txn: mapTxn(d.transaction) })),
  trendsBy: (group: "category" | "merchant" | "account", granularity: Granularity, periods: number, end: string) =>
    resource(`/api/trends?${qs({ granularity, periods, end, group_by: group, limit: 50 })}`, flattenTrends),
  liveNetWorth: () => resource("/api/networth/live", (r: LiveNetWorth) => r),
  holdings: () => resource("/api/holdings", (r: { items: Holding[] }) => r.items),
  inboxStats: (month: MonthKey) => resource(`/api/inbox/stats?${qs({ month })}`, (r: InboxStats) => r),
  rules: () => resource("/api/rules", (r: { items: Rule[] }) => r.items),
  sourcesQueue: () => resource("/api/sources/queue", (r: ParseQueue) => r),
};

/** Every page of a filtered list (CSV export), capped like range reads. */
export async function allTxns(f: TxnQuery): Promise<Transaction[]> {
  const first = await request<ApiTxnPage>(`/api/transactions?${txnQueryString(f, 1, PAGE_SIZE)}`);
  const pages = Math.min(MAX_PAGES, Math.ceil(first.total / PAGE_SIZE));
  const rest = await Promise.all(
    Array.from({ length: Math.max(0, pages - 1) }, (_, i) => request<ApiTxnPage>(`/api/transactions?${txnQueryString(f, i + 2, PAGE_SIZE)}`)),
  );
  return [first, ...rest].flatMap((p) => p.items.map(mapTxn));
}

const ACTIVE_STATES = new Set(["upcoming", "pending", "late"]);
function mapRecurring(r: ApiRecurringList): RecurringList {
  return {
    items: r.items.map(({ amount_expected, amount_min, amount_max, monthly_cost, yearly_cost, change, charges, ...x }) => ({
      ...x,
      amountExpected: toPaise(amount_expected),
      amountMin: toPaise(amount_min),
      amountMax: toPaise(amount_max),
      monthly: toPaise(monthly_cost),
      yearly: toPaise(yearly_cost),
      change: change ? { from: toPaise(change.from), to: toPaise(change.to), at: change.at } : null,
      charges: charges.map((c) => ({ ...c, amount: toPaise(c.amount) })),
      active: ACTIVE_STATES.has(x.state),
    })),
    dismissed: r.dismissed,
    candidates: r.candidates ?? [],
    totals: {
      monthly: toPaise(r.totals.monthly),
      yearly: toPaise(r.totals.yearly),
      investMonthly: toPaise(r.totals.invest_monthly),
      active: r.totals.active,
      next30: toPaise(r.totals.next_30_days),
      next30Count: r.totals.next_30_days_count,
    },
  };
}

function flattenTrends(r: ApiTrends): TrendPoint[] {
  return r.series.flatMap((s) => s.points.map((p) => ({ start: p.period_start, key: s.key, amount: toPaise(p.amount), count: p.count })));
}

export const SOURCE_LABEL: Record<TxnSource, string> = {
  statement: "Bank statement",
  alert: "Email alert",
  sms: "SMS",
  upload: "Upload",
  expected: "Predicted from history",
  import: "Sheet import",
};

// ---------- mutations ----------

const AFTER_CLASSIFY = ["/api/transactions", "/api/summary", "/api/inbox", "/api/alerts", "/api/trends"];

/** Re-files one transaction; scope "payee" also creates a payee rule so future payments follow. */
export async function categorize(id: string, categoryId: number, scope: Scope): Promise<{ updated: number }> {
  const r = await request<{ updated: number }>(`/api/transactions/${encodeURIComponent(id)}/category`, "POST", { category_id: categoryId, scope });
  invalidate(AFTER_CLASSIFY);
  return r;
}

/** Files an Inbox payee's listed payments; scope "payee" remembers the choice for future payments. */
export async function fileInboxPayee(p: InboxPayee, categoryId: number, scope: Scope): Promise<{ filed: number; rule_id: string | null }> {
  const r = await request<{ filed: number; rule_id: string | null }>(`/api/inbox/${encodeURIComponent(p.payeeKey ?? `txn:${p.payments[0]?.id}`)}/file`, "POST", {
    category_id: categoryId,
    direction: p.direction,
    remember: scope === "payee",
    txn_ids: p.payments.map((x) => Number(x.id)),
  });
  invalidate(AFTER_CLASSIFY);
  return r;
}

/** Saves the member's remark on a net-worth snapshot (the sheet's Remarks column). */
export async function saveRemark(date: string, remark: string): Promise<void> {
  await request<unknown>(`/api/networth/snapshots/${encodeURIComponent(date)}`, "PATCH", { remark });
  invalidate(["/api/networth"]);
}

/** Puts filed txns back in the Inbox; with the rule the filing created, that rule is deleted too. */
export async function undoFiling(txnIds: number[], ruleId: string | null): Promise<{ restored: number }> {
  const r = await request<{ restored: number }>("/api/inbox/undo", "POST", { txn_ids: txnIds, ...(ruleId ? { rule_id: ruleId } : {}) });
  invalidate(AFTER_CLASSIFY);
  return r;
}

export async function patchTxn(id: string, body: { notes?: string | null; tags?: string[] }): Promise<void> {
  await request<unknown>(`/api/transactions/${encodeURIComponent(id)}`, "PATCH", body);
  invalidate(["/api/transactions"]);
}

export async function setComponent(key: string, amount: string, asOf?: ISODate): Promise<void> {
  await request<unknown>(`/api/networth/components/${encodeURIComponent(key)}`, "PUT", { amount, ...(asOf ? { as_of: asOf } : {}) });
  invalidate(["/api/networth"]);
}

const AFTER_EDIT = ["/api/transactions", "/api/summary", "/api/trends", "/api/recurring", "/api/cards", "/api/inbox"];

/** Splits a txn into parts that add up to it; the original stays, out of every total. */
export async function splitTxn(id: string, parts: { amount: string; category_id: number; note?: string }[]): Promise<void> {
  await request<unknown>(`/api/transactions/${encodeURIComponent(id)}/split`, "POST", { parts });
  invalidate(AFTER_EDIT);
}

export async function unsplitTxn(id: string): Promise<void> {
  await request<unknown>(`/api/transactions/${encodeURIComponent(id)}/unsplit`, "POST", {});
  invalidate(AFTER_EDIT);
}

export async function linkTxn(id: string, other: number, kind: LinkKind, remove = false): Promise<void> {
  await request<unknown>(`/api/transactions/${encodeURIComponent(id)}/links${remove ? "/remove" : ""}`, "POST", { txn_id: other, kind });
  invalidate(AFTER_EDIT);
}

/** Your call on a payee's series; "auto" forgets it so detection decides again. */
export async function decideRecurring(
  key: string,
  body: { decision: "confirmed" | "dismissed" | "auto"; cadence?: Cadence; kind?: RecurringKind; ended?: boolean; amount_expected?: string },
): Promise<void> {
  await request<unknown>(`/api/recurring/${encodeURIComponent(key)}`, "PUT", body);
  invalidate(["/api/recurring", "/api/trends", "/api/alerts"]);
}

export async function setRuleEnabled(id: string, enabled: boolean): Promise<void> {
  await request<unknown>(`/api/rules/${encodeURIComponent(id)}`, "PATCH", { enabled });
  invalidate(["/api/rules", "/api/inbox"]);
}
