// Wire types follow docs/api.md (M0). Recurring and alerts aren't in M0: the UI treats their 404 as "section
// unavailable". Money arrives as a 2-place decimal string; api.ts converts it once to paise.

export type Decimal = string;
export type Paise = number;
/** YYYY-MM-DD */
export type ISODate = string;
/** YYYY-MM */
export type MonthKey = string;

export type TxnKind = "spend" | "income" | "transfer" | "investment" | "refund" | "fee" | "cash";
export type Direction = "debit" | "credit";
export type Bucket = "everyday" | "oneoff" | "card" | "invest" | "income" | "excluded";
export type AccountKind = "bank" | "card" | "wallet" | "deposit" | "holding" | "cash";
export type TxnStatus = "pending" | "posted" | "reconciled" | "flagged";
export type ClassifiedBy = "rule" | "payee_memory" | "dictionary" | "heuristic" | "user" | "system";
export type Health = "good" | "warn" | "bad";
export type Scope = "this" | "payee";
export type Granularity = "week" | "month" | "quarter" | "fy";

// ---------- wire: docs/api.md ----------

export interface ApiPage<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface ApiTxn {
  id: number;
  occurred_at: ISODate;
  posted_at: ISODate | null;
  amount: Decimal;
  currency: string;
  direction: Direction;
  kind: TxnKind;
  merchant: string | null;
  counterparty: string | null;
  vpa: string | null;
  payee_key: string | null;
  narration: string | null;
  account: { id: number; institution: string; name: string | null; label: string; kind: AccountKind; mask: string | null } | null;
  category: { id: number; name: string } | null;
  bucket: Bucket | null;
  classified_by: ClassifiedBy | null;
  rule_id: string | null;
  review_reason: string | null;
  status: TxnStatus;
  sources: TxnSource[];
  notes: string | null;
  tags: string[];
}
export type TxnSource = "statement" | "alert" | "sms" | "upload" | "expected" | "import";

export interface ApiTotals {
  expense: Decimal;
  everyday: Decimal;
  card: Decimal;
  oneoff: Decimal;
  uncategorized: Decimal;
  invest: Decimal;
  income: Decimal;
  salary: Decimal;
  salary_minus_expense: Decimal;
  txn_count: number;
  refunds?: Decimal;
}

export interface ApiSummary {
  month: MonthKey;
  previous_month: MonthKey;
  totals: ApiTotals;
  previous_totals: ApiTotals;
  categories: { category_id: number | null; name: string; amount: Decimal; previous_amount: Decimal }[];
}

export interface Category {
  id: number;
  name: string;
  description: string | null;
  kind: TxnKind;
  bucket: Bucket;
  parent_id: number | null;
  scope: "household" | "member";
}

export interface ApiSnapshot {
  date: ISODate;
  net_worth: Decimal;
  liquid: Decimal | null;
  /** All nine sheet keys; null means the sheet cell was blank. */
  components: Record<string, Decimal | null>;
  remark: string | null;
  commentary: string | null;
  commentary_source: "stored" | "template";
  locked: boolean;
  net_change: Decimal | null;
  liquid_change: Decimal | null;
}
export interface ApiNetWorth {
  snapshots: ApiSnapshot[];
  latest: { components: { key: string; label: string; asset_class: string }[] } | null;
}

export interface PayeeHistory {
  category_id: number;
  category: string;
  count: number;
}
/** /api/inbox without grouping (docs/api.md). */
export interface ApiInboxItem {
  txn: ApiTxn;
  reason: string | null;
  payee_history: PayeeHistory[];
}
/** /api/inbox?group=payee: one item per (payee, direction). */
export interface ApiInboxGroup {
  payee_key: string;
  direction: Direction;
  display: string;
  reason: string | null;
  count: number;
  total: Decimal;
  last_at: ISODate;
  suggestion: PayeeHistory | null;
  history: PayeeHistory[];
  txns: ApiTxn[];
}

export interface MonthMeta {
  month: MonthKey;
  /** The cycle's first and last day (docs/api.md: months follow month_start_day). */
  start?: ISODate;
  end?: ISODate;
  through: ISODate;
  complete: boolean;
}
export interface ApiMonths {
  as_of: ISODate;
  month_start_day?: number;
  items: MonthMeta[];
}
export interface ApiObservation {
  id: number;
  source: TxnSource;
  parser: string | null;
  parser_version: string | null;
  occurred_at: ISODate;
  amount: Decimal;
  direction: Direction;
  balance_after: Decimal | null;
  ref_no: string | null;
  received_at: string | null;
}
export interface ApiTransactionDetail {
  transaction: ApiTxn;
  observations: ApiObservation[];
  payee: { payee_key: string; count: number; total: Decimal; history: PayeeHistory[] } | null;
}

// ---------- wire: not in M0 (docs/api.md: a 404 means "section unavailable") ----------

export interface ApiRecurring {
  id: string;
  merchant: string;
  cadence: string;
  amount_expected: Decimal;
  next_due: ISODate;
}
export interface ServerAlert {
  id: string;
  kind: string;
  severity: Health;
  title: string;
  detail: string;
}

// ---------- wire: docs/api.md, continued ----------

export interface Account {
  id: number;
  institution: string;
  name: string | null;
  kind: AccountKind;
  mask: string | null;
  label: string;
  txn_count: number;
  first_txn_at: ISODate | null;
  last_txn_at: ISODate | null;
  last_statement: { period_start: ISODate; period_end: ISODate; reconciled: boolean; diff: Decimal } | null;
  has_statement_password: boolean;
  /** Last live alert; null until the collectors land (M1). */
  last_seen_at: string | null;
  coverage_pct: number | null;
}
export interface Me {
  name: string;
  email: string;
  role: string;
  household: { id: number; name: string } | null;
}
export interface ApiTrends {
  granularity: Granularity;
  group_by: "total" | "category" | "merchant" | "kind";
  month_start_day: number;
  periods: { start: ISODate; end: ISODate }[];
  series: { key: string; total: Decimal; points: { period_start: ISODate; amount: Decimal; count: number }[] }[];
}

// ---------- client models ----------

export interface Transaction {
  id: string;
  date: ISODate;
  account_id: string | null;
  account: string;
  account_kind: AccountKind | null;
  merchant: string;
  category: string | null;
  category_id: number | null;
  bucket: Bucket | null;
  kind: TxnKind;
  amount: Paise;
  direction: Direction;
  status: TxnStatus;
  classified_by: ClassifiedBy | null;
  /** Opaque (docs/api.md): shown on hover, never parsed. */
  rule_id: string | null;
  payee_key: string | null;
  sources: TxnSource[];
}

export interface Totals {
  expense: Paise;
  income: Paise;
  invest: Paise;
  card: Paise;
  /** Only when the server reports it. */
  refunds: Paise | null;
}

export interface MonthSummary {
  totals: Totals;
  previous: Totals;
  categories: { category: string; amount: Paise; previous: Paise }[];
}

export interface TransactionDetail {
  payee: { count: number; total: Paise } | null;
  observations: { label: string; detail: string }[];
}

export type Budgets = Map<string, Paise>;
export type Recurring = Omit<ApiRecurring, "amount_expected"> & { amountExpected: Paise };

export interface InboxPayee {
  key: string;
  payeeKey: string | null;
  payee: string;
  direction: Direction;
  account: string;
  reason: string | null;
  suggestedCategory: string | null;
  history: PayeeHistory[];
  payments: { id: string; date: ISODate; amount: Paise }[];
  total: Paise;
}
export interface Inbox {
  items: InboxPayee[];
  /** Groups on the server (group=payee counts groups); may exceed what was fetched. */
  total: number;
  fetched: number;
}

export interface Component {
  key: string;
  label: string;
  assetClass: string;
  amount: Paise;
}
export interface Snapshot {
  date: ISODate;
  netWorth: Paise;
  liquid: Paise;
  netChange: Paise | null;
  liquidChange: Paise | null;
  components: Component[];
  remark: string | null;
  commentary: string | null;
  /** "template" when the server generated the commentary (no AI), "stored" when it came from the sheet. */
  commentarySource: "stored" | "template" | null;
}

export interface Settings {
  monthStartDay: number;
}

/** A reporting period, both ends inclusive. */
export interface Period {
  key: string;
  start: ISODate;
  end: ISODate;
  label: string;
  short: string;
}

export interface TrendPoint {
  start: ISODate;
  key: string | null;
  amount: Paise;
  count: number;
}
