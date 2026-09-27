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
  /** A bill payment matched to a card's payment line: the other leg. */
  settles?: ApiSettles | null;
  split_of?: number | null;
  split_parts?: number;
  loan_id?: number | null;
}
export interface ApiSettles {
  txn_id: number;
  date: ISODate;
  card: string | null;
  from_account: string | null;
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

export type RecurringKind = "subscription" | "bill" | "invest" | "other";
export type Cadence = "weekly" | "monthly" | "quarterly" | "yearly";
export type RecurringState = "upcoming" | "pending" | "late" | "stopped" | "ended";
export interface ApiRecurring {
  id: string;
  merchant: string;
  kind: RecurringKind;
  cadence: Cadence;
  state: RecurringState;
  variable: boolean;
  amount_expected: Decimal;
  amount_min: Decimal;
  amount_max: Decimal;
  monthly_cost: Decimal;
  yearly_cost: Decimal;
  next_due: ISODate;
  first_at: ISODate;
  last_at: ISODate;
  seen_through: ISODate;
  count: number;
  category: string | null;
  account: string | null;
  confirmed: boolean;
  manual: boolean;
  change: { from: Decimal; to: Decimal; at: ISODate } | null;
  charges: { date: ISODate; amount: Decimal; txn_id: number }[];
}
export interface RecurringCandidate {
  id: string;
  merchant: string;
  count: number;
  last_at: ISODate;
  amount: Decimal;
  category: string | null;
  account: string | null;
  kind: "subscription" | "bill";
}
export interface ApiRecurringList {
  items: ApiRecurring[];
  dismissed: { id: string; merchant: string }[];
  candidates: RecurringCandidate[];
  totals: { monthly: Decimal; yearly: Decimal; invest_monthly: Decimal; active: number; next_30_days: Decimal; next_30_days_count: number };
}
export interface ApiCards {
  items: {
    account: { id: number; label: string | null };
    statement: {
      period_start: ISODate;
      period_end: ISODate;
      total: Decimal;
      due_date: ISODate | null;
      purchases: number;
      paid: Decimal;
      paid_at: ISODate | null;
      paid_from: string | null;
      state: "paid" | "part_paid" | "unpaid";
    } | null;
    cycle: { since: ISODate; amount: Decimal; count: number; seen_through: ISODate | null };
  }[];
  stand_in: { amount: Decimal; count: number; first: ISODate | null; last: ISODate | null };
}
export interface ServerAlert {
  id: string;
  kind: "duplicate" | "bounce_risk" | "price_increase" | string;
  severity: Health;
  title: string;
  detail: string;
  txn_ids?: number[];
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
  /** Balance printed after the newest statement line. */
  balance: { amount: Decimal; as_of: ISODate } | null;
  /** Last live alert; null until the collectors land (M1). */
  last_seen_at: string | null;
  coverage_pct: number | null;
}
export interface Me {
  name: string;
  email: string;
  role: string;
  household: { id: number; name: string } | null;
  settings?: { month_start_day: number; local_shop_cap: Decimal };
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
  vpa: string | null;
  narration: string | null;
  counterparty: string | null;
  review_reason: string | null;
  notes: string | null;
  tags: string[];
  settles: ApiSettles | null;
  /** Set on a part of a split: the original's id. */
  split_of: string | null;
  /** On the original: how many parts it was split into. */
  split_parts: number;
  /** Filed under Loans, on this loan. */
  loan_id: number | null;
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
export type Recurring = Omit<ApiRecurring, "amount_expected" | "amount_min" | "amount_max" | "monthly_cost" | "yearly_cost" | "change" | "charges"> & {
  amountExpected: Paise;
  amountMin: Paise;
  amountMax: Paise;
  monthly: Paise;
  yearly: Paise;
  change: { from: Paise; to: Paise; at: ISODate } | null;
  charges: { date: ISODate; amount: Paise; txn_id: number }[];
  active: boolean;
};
export interface RecurringList {
  items: Recurring[];
  dismissed: { id: string; merchant: string }[];
  candidates: RecurringCandidate[];
  totals: { monthly: Paise; yearly: Paise; investMonthly: Paise; active: number; next30: Paise; next30Count: number };
}

export interface InboxPayee {
  key: string;
  payeeKey: string | null;
  payee: string;
  vpa: string | null;
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

// ---------- wire + models: transactions page, net worth live, inbox stats, rules, sources ----------

export type Sort = "date_desc" | "date_asc" | "amount_desc" | "amount_asc";
export type TotalKey = "spend" | "income" | "invest" | "excluded" | "card" | "on_card";
export interface ApiTxnPage extends ApiPage<ApiTxn> {
  totals: Record<TotalKey, { amount: Decimal; count: number }>;
}
export interface TxnQuery {
  from?: ISODate;
  to?: ISODate;
  q?: string;
  min?: string;
  max?: string;
  accounts?: number[];
  categories?: string[];
  kind?: TxnKind;
  direction?: Direction;
  sort?: Sort;
  paidWith?: "bank" | "card";
}
export interface TxnPage {
  items: Transaction[];
  total: number;
  totals: Record<TotalKey, { amount: Paise; count: number }>;
}

export interface ApiTxnDetail {
  transaction: ApiTxn;
  observations: (ApiObservation & { filename: string | null; raw_message_id: number | null })[];
  links: { kind: string; txn_id: number }[];
  split_parts: { id: number; amount: Decimal; category: string | null; note: string | null }[];
  payee: {
    payee_key: string;
    count: number;
    total: Decimal;
    history: PayeeHistory[];
    recent: { id: number; occurred_at: ISODate; amount: Decimal; category: string | null }[];
  } | null;
}

export interface LiveComponent {
  key: string;
  label: string;
  asset_class: string;
  amount: Decimal;
  share_pct: number;
  source: "sheet" | "statement" | "manual" | "loans";
  as_of: ISODate;
  stale: boolean;
  editable: boolean;
  change_since: Decimal | null;
}
export interface LiveNetWorth {
  as_of: ISODate;
  net_worth: Decimal | null;
  liquid: Decimal | null;
  components: LiveComponent[];
  by_asset_class: Record<string, Decimal>;
  changes: { period: "month" | "year" | "fy"; since: ISODate; amount: Decimal | null; pct: number | null }[];
  history: { date: ISODate; net_worth: Decimal; kind: "snapshot" | "live" }[];
  months: {
    start: ISODate;
    end: ISODate;
    start_value: Decimal;
    end_value: Decimal;
    change: Decimal;
    cash_change: Decimal | null;
    contributions: Decimal;
    market: Decimal | null;
    live: boolean;
  }[];
  projection: { monthly_change: Decimal; basis_months: number; points: { date: ISODate; net_worth: Decimal }[] } | null;
}
export interface Holding {
  name: string;
  isin: string | null;
  units: string;
  units_as_of: ISODate;
  source: string;
  price: string | null;
  price_date: ISODate | null;
  value: Decimal | null;
}

export interface InboxStats {
  month: MonthKey;
  total: number;
  automatic: number;
  by: Record<"rules" | "payee_memory" | "dictionary" | "structural" | "user" | "waiting", number>;
  rules: number;
}
export interface Rule {
  id: string;
  scope: "member" | "household";
  match: Record<string, string | number>;
  category: string;
  enabled: boolean;
  created_by: string;
  created_at: string;
  hits: number;
  last_hit_at: string | null;
  editable: boolean;
}
export interface LinkCandidate {
  id: number;
  occurred_at: ISODate;
  amount: Decimal;
  direction: Direction;
  merchant: string | null;
  account_id: number | null;
  suggest: "transfer" | "dup";
}
export interface RawSource {
  raw_message_id: number;
  kind: "email" | "file";
  sender: string | null;
  subject: string | null;
  received_at: string;
  text: string | null;
  files: { id: number; filename: string | null }[];
}
export type LinkKind = "transfer" | "refund" | "dup" | "pass_through" | "card_payment";
export interface ParseQueue {
  collected?: Record<string, number>;
  unparsed: { sender: string | null; subject: string | null; status: string; count: number; first_seen: string; last_seen: string }[];
  uploads: {
    id: number;
    filename: string | null;
    received_at: string;
    status: string;
    account: string | null;
    period_start: ISODate | null;
    period_end: ISODate | null;
    diff: Decimal | null;
    reconciled: boolean;
  }[];
}
