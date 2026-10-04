"""Request and response models (docs/api.md). Money is a 2-place decimal string, never a float."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = str


class Health(BaseModel):
    status: str
    database: str


# --- transactions ---------------------------------------------------------------------------

class AccountRef(BaseModel):
    id: int
    institution: str | None
    name: str | None
    label: str | None
    kind: str | None
    mask: str | None


class CategoryRef(BaseModel):
    id: int
    name: str


class Settles(BaseModel):
    txn_id: int  # the other leg
    date: date
    card: str | None  # on the bank leg: the card it paid
    from_account: str | None  # on the card leg: where the money came from


class TxnOut(BaseModel):
    id: int
    occurred_at: date
    posted_at: date | None
    amount: Money
    currency: str
    direction: str
    kind: str
    merchant: str | None
    named: bool = False  # merchant is the member's own name for the payee (payee-aliases)
    counterparty: str | None
    vpa: str | None  # a person's handle is masked
    payee_key: str | None
    narration: str | None
    account: AccountRef | None
    category: CategoryRef | None
    bucket: str | None
    classified_by: str | None
    rule_id: str | None
    review_reason: str | None
    status: str
    sources: list[str]
    notes: str | None
    tags: list[str]
    settles: Settles | None = None  # a bill payment matched to a card's payment line
    split_of: int | None = None  # this is a part of that txn's split
    split_parts: int = 0  # this txn was split into this many parts
    loan_id: int | None = None  # filed under Loans, on this loan


class TotalLine(BaseModel):
    amount: Money
    count: int


class TxnPage(BaseModel):
    items: list[TxnOut]
    page: int
    page_size: int
    total: int
    # The whole filtered set, not just this page: spend/income/invest/excluded; card (bill payments standing
    # in for card spend) and on_card (spend on card accounts) are both parts of spend.
    totals: dict[str, TotalLine]


class PayeeHistory(BaseModel):
    category_id: int
    category: str
    count: int


class ObservationOut(BaseModel):
    id: int
    source: str
    parser: str
    parser_version: str
    occurred_at: date
    amount: Money
    direction: str
    balance_after: Money | None
    ref_no: str | None
    raw_message_id: int | None
    received_at: datetime | None
    filename: str | None


class LinkOut(BaseModel):
    kind: str
    txn_id: int


class PayeeRecent(BaseModel):
    id: int
    occurred_at: date
    amount: Money
    category: str | None


class PayeeGroup(BaseModel):
    name: str
    original: str | None  # this payee's name before the alias
    payee_keys: list[str]  # every payee sharing the name, this one included


class AliasSuggestion(BaseModel):
    name: str  # the alias it looks like
    why: Literal["prefix", "words", "spelling"]
    like: str  # the name it matched: the alias, or one of its payees' originals


class PayeeStats(BaseModel):
    payee_key: str
    count: int
    total: Money
    history: list[PayeeHistory]
    recent: list[PayeeRecent]
    alias: PayeeGroup | None = None
    suggest: AliasSuggestion | None = None  # an alias this payee looks like, when it has none


class SplitPartOut(BaseModel):
    id: int
    amount: Money
    category: str | None
    note: str | None


class TxnDetail(BaseModel):
    transaction: TxnOut
    observations: list[ObservationOut]
    links: list[LinkOut]
    payee: PayeeStats | None
    split_parts: list[SplitPartOut] = []


class SplitPartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Annotated[str, Field(pattern=r"^\d{1,12}(\.\d{1,2})?$")]
    category_id: Annotated[int, Field(ge=1)]
    note: Annotated[str, Field(max_length=200)] | None = None


class SplitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parts: Annotated[list[SplitPartIn], Field(min_length=2, max_length=10)]


class SplitOut(BaseModel):
    id: int
    parts: int


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    txn_id: Annotated[int, Field(ge=1)]
    kind: Literal["transfer", "refund", "dup", "pass_through", "card_payment"]


class LinkResult(BaseModel):
    txn_id: int
    other_id: int
    kind: str


class LinkCandidate(BaseModel):
    id: int
    occurred_at: date
    amount: Money
    direction: str
    merchant: str | None
    account_id: int | None
    suggest: Literal["transfer", "dup", "refund"]


class LinkCandidates(BaseModel):
    items: list[LinkCandidate]


class RawFile(BaseModel):
    id: int
    filename: str | None


class RawSource(BaseModel):
    raw_message_id: int
    kind: Literal["email", "file"]
    sender: str | None
    subject: str | None
    received_at: datetime
    text: str | None
    purged: bool = False  # the stored file was deleted under the retention setting
    files: list[RawFile]


class RawSources(BaseModel):
    items: list[RawSource]


class InboxItem(BaseModel):
    txn: TxnOut
    reason: str | None
    payee_history: list[PayeeHistory]


class InboxPage(BaseModel):
    items: list[InboxItem]
    page: int
    page_size: int
    total: int


class InboxGroup(BaseModel):
    payee_key: str
    direction: str
    display: str | None
    reason: str | None
    count: int
    total: Money
    last_at: date
    suggestion: str | None
    history: list[PayeeHistory]
    txns: list[TxnOut]


class InboxGroupPage(BaseModel):
    items: list[InboxGroup]
    page: int
    page_size: int
    total: int


class _CategoryChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: int | None = Field(default=None, ge=1)
    category: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def _exactly_one(self) -> "_CategoryChoice":
        if (self.category_id is None) == (self.category is None):
            raise ValueError("give exactly one of category_id or category")
        return self


class CategorizeIn(_CategoryChoice):
    scope: Literal["this", "payee"] = "this"


class CategorizeOut(BaseModel):
    updated: int
    rule_id: str | None


class FileInboxIn(_CategoryChoice):
    direction: Literal["debit", "credit"] | None = None
    remember: bool | None = None
    scope: Literal["this", "payee"] | None = None  # alias: payee == remember
    txn_ids: list[int] | None = Field(default=None, max_length=500)


class FileInboxOut(BaseModel):
    filed: int
    rule_id: str | None


# --- summary, months, categories, budgets, trends ---------------------------------------------

class Totals(BaseModel):
    expense: Money
    everyday: Money
    card: Money
    oneoff: Money
    uncategorized: Money
    invest: Money
    income: Money
    refunds: Money
    salary: Money
    salary_minus_expense: Money
    txn_count: int


class BucketLine(BaseModel):
    bucket: str
    amount: Money
    previous_amount: Money
    change: Money


class CategoryLine(BaseModel):
    category_id: int | None
    name: str
    bucket: str | None
    amount: Money
    previous_amount: Money
    change: Money
    txn_count: int


class Summary(BaseModel):
    month: str
    previous_month: str
    currency: str
    month_start_day: int
    period: "Period"
    totals: Totals
    previous_totals: Totals
    buckets: list[BucketLine]
    categories: list[CategoryLine]
    income_categories: list[CategoryLine]
    loans: "LoanLines | None" = None


class MonthOut(BaseModel):
    month: str
    start: date
    end: date
    through: date
    complete: bool
    txn_count: int


class Months(BaseModel):
    as_of: date
    month_start_day: int
    items: list[MonthOut]


class CategoryOut(BaseModel):
    id: int
    name: str
    description: str | None
    kind: str
    bucket: str
    credit_bucket: str | None  # a credit's bucket when it differs: the people categories count it as income
    parent_id: int | None
    scope: str  # household | member


class BudgetLine(BaseModel):
    category_id: int
    category: str
    amount: Money
    carry: Money
    limit: Money
    spent: Money
    remaining: Money
    expected_by_today: Money
    projected: Money
    state: Literal["ok", "ahead", "over"]
    rollover: bool


class BudgetTotals(BaseModel):
    limit: Money
    spent: Money


class Budgets(BaseModel):
    month: str
    day: int
    days: int
    items: list[BudgetLine]
    totals: BudgetTotals


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Annotated[str, Field(pattern=r"^\d{1,12}(\.\d{1,2})?$")] | None
    rollover: bool = False


class BudgetOut(BaseModel):
    category_id: int
    amount: Money | None
    rollover: bool


class Period(BaseModel):
    start: date
    end: date


class TrendPoint(BaseModel):
    period_start: date
    amount: Money
    count: int


class TrendSeries(BaseModel):
    key: str
    total: Money
    points: list[TrendPoint]


class Trends(BaseModel):
    granularity: str
    group_by: str
    month_start_day: int
    periods: list[Period]
    series: list[TrendSeries]


# --- member, settings, accounts --------------------------------------------------------------

class SettingsOut(BaseModel):
    month_start_day: int
    local_shop_cap: Money
    raw_retention_days: int
    notify_topic: str | None
    notify_enabled: bool


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month_start_day: int | None = Field(default=None, ge=1, le=28, strict=True)
    local_shop_cap: str | int | None = None
    raw_retention_days: Literal[0, 90, 180, 365, 730] | None = None
    # An ntfy topic: long and unguessable, since anyone who knows it can read the pushes.
    notify_topic: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{12,64}$")] | None = None
    notify_enabled: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> "SettingsIn":
        if not self.model_fields_set:
            raise ValueError("nothing to update")
        return self


class HouseholdRef(BaseModel):
    id: int
    name: str


class Me(BaseModel):
    name: str
    email: str
    role: str
    household: HouseholdRef
    settings: SettingsOut


class StatementRef(BaseModel):
    period_start: date
    period_end: date
    reconciled: bool
    diff: Money | None


class AccountOut(BaseModel):
    id: int
    institution: str
    name: str | None
    kind: str
    mask: str | None
    label: str | None
    currency: str
    txn_count: int
    first_txn_at: date | None
    last_txn_at: date | None
    last_statement: StatementRef | None
    statement_passwords: list["StatementPasswordOut"] = []
    has_statement_password: bool  # the "main" slot; kept for older clients
    has_extra_statement_password: bool
    balance: "Balance | None"
    last_seen_at: datetime | None  # when the newest alert for the account arrived
    coverage_pct: float | None  # % of statement lines since the first alert that were also seen as alerts
    covered_through: date | None  # end of the newest reconciled statement
    live_through: datetime | None  # alerts are read up to here; null when no rule reads this account's alerts
    alerts: bool  # a rule reads alerts for this institution and kind


class Accounts(BaseModel):
    items: list[AccountOut]


class CoverageMailbox(BaseModel):
    id: int
    label: str
    email: str
    status: str
    collecting: bool  # status ok: the collector reads it
    last_poll_at: datetime | None
    last_ok_poll_at: datetime | None
    last_poll_error: str | None
    last_tested_at: datetime | None
    healthy: bool
    problem: str | None


class CoverageDay(BaseModel):
    date: date
    state: Literal["confirmed", "live", "unknown"]
    reason: str | None
    txn_count: int


class CoverageAccount(BaseModel):
    account: AccountRef
    alerts: bool
    covered_through: date | None
    last_seen_at: datetime | None
    live_through: datetime | None
    coverage_pct: float | None
    days: list[CoverageDay]


class Coverage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: date = Field(alias="from")
    to: date
    timezone: str
    now: datetime
    mailboxes: list[CoverageMailbox]
    accounts: list[CoverageAccount]


# --- net worth -------------------------------------------------------------------------------

class SnapshotOut(BaseModel):
    date: date
    components: dict[str, Money | None]
    net_worth: Money
    net_change: Money | None
    liquid: Money | None
    liquid_change: Money | None
    remark: str | None
    commentary: str | None
    commentary_source: str | None
    locked: bool


class Component(BaseModel):
    key: str
    label: str
    asset_class: str
    amount: Money
    share_pct: float


class LatestNetWorth(BaseModel):
    date: date
    net_worth: Money
    components: list[Component]
    by_asset_class: dict[str, Money]


class NetWorth(BaseModel):
    snapshots: list[SnapshotOut]
    latest: LatestNetWorth | None


class RemarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remark: str | None = Field(max_length=2000)


class RemarkOut(BaseModel):
    date: date
    remark: str | None


class SheetImportOut(BaseModel):
    snapshots_upserted: int


# --- onboarding ------------------------------------------------------------------------------

class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]{1,64}@[^@\s]{1,255}$")


class InviteCreated(BaseModel):
    email: str
    token: str  # shown once; only its hash is stored
    url: str
    expires_at: datetime


class InviteInfo(BaseModel):
    email: str
    household: str
    expires_at: datetime
    status: Literal["valid", "used", "expired"]


class Checklist(BaseModel):
    profile: bool
    mail_source: bool
    statement_passwords: bool
    first_upload: bool


class OnboardingOut(BaseModel):
    step: str
    completed_at: datetime | None
    steps: list[str]
    gmail_filter: str  # paste into Gmail → Create filter, then apply the label below
    label: str
    checklist: Checklist


class OnboardingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Literal["profile", "mail", "statement_passwords", "first_upload", "done"] | None = None
    completed: bool | None = None


_Name = Annotated[str, Field(min_length=1, max_length=120)]
_Mask = Annotated[str, Field(pattern=r"^\d{4}$")]


class ClassifyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    own_names: list[_Name] = Field(default_factory=list, max_length=10)
    own_vpas: list[Annotated[str, Field(min_length=3, max_length=120)]] = Field(default_factory=list, max_length=20)
    own_account_masks: list[_Mask] = Field(default_factory=list, max_length=20)
    investment_account_masks: list[_Mask] = Field(default_factory=list, max_length=20)
    # Matched as case-insensitive substrings of the narration, not as regexes.
    employer_patterns: list[Annotated[str, Field(min_length=3, max_length=80)]] = Field(default_factory=list,
                                                                                      max_length=10)


class MailSourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gmail", "outlook", "yahoo", "custom"]
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    email: str = Field(max_length=320)
    app_password: str = Field(min_length=1, max_length=256, repr=False)
    label: str = Field(default="tijori", min_length=1, max_length=100)


class MailSourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_password: str | None = Field(default=None, min_length=1, max_length=256, repr=False)
    label: str | None = Field(default=None, min_length=1, max_length=100)


class MailSourceOut(BaseModel):
    id: int
    provider: str
    host: str
    port: int
    email: str
    label: str
    status: str
    last_tested_at: datetime | None
    last_error_code: str | None
    last_message_count: int | None
    last_poll_at: datetime | None  # every attempt
    last_poll_error: str | None
    last_ok_poll_at: datetime | None = None  # the last poll that read the whole label
    collecting: bool = False  # status ok: the collector reads it
    created_at: datetime


class MailSources(BaseModel):
    items: list[MailSourceOut]


class MailTestOut(BaseModel):
    ok: bool
    message_count: int | None
    error_code: str | None


class StatementPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256, repr=False)
    slot: Literal["main", "extra"] = "main"


class StatementPasswordAddIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256, repr=False)
    label: Annotated[str, Field(max_length=40)] | None = None


class StatementPasswordOut(BaseModel):
    """Which passwords an account holds; the value is never returned."""
    slot: str
    label: str | None
    updated_at: datetime




class HouseholdMember(BaseModel):
    id: int
    name: str
    email: str
    role: str
    joined_at: datetime


class HouseholdInvite(BaseModel):
    id: int
    email: str
    status: Literal["pending", "accepted", "expired"]
    created_at: datetime
    expires_at: datetime


class HouseholdOut(BaseModel):
    id: int
    name: str | None
    members: list[HouseholdMember]
    invites: list[HouseholdInvite]


class MeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class AccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution: str = Field(min_length=1, max_length=80)
    kind: Literal["bank", "card", "wallet", "deposit", "holding", "cash"]
    name: str | None = Field(default=None, max_length=120)
    mask: str | None = Field(default=None, pattern=r"^\d{4}$")


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    mask: str | None = Field(default=None, pattern=r"^\d{4}$")


class Balance(BaseModel):
    amount: Money
    as_of: date


AccountOut.model_rebuild()


# --- recurring, alerts, filing, rules ----------------------------------------------------------

class PriceChange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: Money = Field(alias="from")
    to: Money
    at: date


class RecurringCharge(BaseModel):
    date: date
    amount: Money
    txn_id: int


class CardRef(BaseModel):
    id: int
    label: str | None


class CardStatement(BaseModel):
    period_start: date
    period_end: date
    total: Money
    due_date: date | None
    purchases: int
    paid: Money
    paid_at: date | None
    paid_from: str | None
    state: Literal["paid", "part_paid", "unpaid"]


class CardCycle(BaseModel):
    since: date
    amount: Money
    count: int
    seen_through: date | None


class CardOut(BaseModel):
    account: CardRef
    statement: CardStatement | None
    cycle: CardCycle


class StandIn(BaseModel):
    amount: Money
    count: int
    first: date | None
    last: date | None


class CardList(BaseModel):
    items: list[CardOut]
    stand_in: StandIn


class RecurringOut(BaseModel):
    id: str
    merchant: str
    kind: Literal["subscription", "bill", "invest", "other"]
    cadence: str
    state: Literal["upcoming", "pending", "late", "stopped", "ended"]
    variable: bool
    amount_expected: Money
    amount_min: Money
    amount_max: Money
    monthly_cost: Money
    yearly_cost: Money
    next_due: date
    first_at: date
    last_at: date
    seen_through: date
    count: int
    category: str | None
    account: str | None
    confirmed: bool
    manual: bool
    change: PriceChange | None
    charges: list[RecurringCharge]


class RecurringRef(BaseModel):
    id: str
    merchant: str


class RecurringTotals(BaseModel):
    monthly: Money
    yearly: Money
    invest_monthly: Money
    active: int
    next_30_days: Money
    next_30_days_count: int


class RecurringCandidate(BaseModel):
    id: str
    merchant: str
    count: int
    last_at: date
    amount: Money
    category: str | None
    account: str | None
    kind: Literal["subscription", "bill"]


class RecurringList(BaseModel):
    items: list[RecurringOut]
    dismissed: list[RecurringRef]
    candidates: list[RecurringCandidate]
    totals: RecurringTotals


class RecurringIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["confirmed", "dismissed", "auto"]
    cadence: Literal["weekly", "monthly", "quarterly", "yearly"] | None = None
    amount_expected: Annotated[str, Field(pattern=r"^\d{1,12}(\.\d{1,2})?$")] | None = None
    kind: Literal["subscription", "bill", "invest", "other"] | None = None
    ended: bool | None = None


class RecurringDecision(BaseModel):
    id: str
    decision: Literal["confirmed", "dismissed"] | None


class AlertOut(BaseModel):
    id: str
    kind: str
    severity: Literal["bad", "warn", "good"]
    title: str
    detail: str
    txn_ids: list[int]


class Alerts(BaseModel):
    month: str
    items: list[AlertOut]


class FilingStats(BaseModel):
    month: str
    total: int
    automatic: int
    by: dict[str, int]
    rules: int


class RuleOut(BaseModel):
    id: str
    scope: str
    match: dict
    category: str
    enabled: bool
    created_by: str
    created_at: datetime
    hits: int
    last_hit_at: datetime | None
    editable: bool


class Rules(BaseModel):
    items: list[RuleOut]


# --- payee aliases ---------------------------------------------------------------------------

PayeeKey = Annotated[str, Field(min_length=1, max_length=80)]


class PayeeRenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=120)]
    payee_keys: list[PayeeKey] = Field(min_length=1, max_length=50)


class PayeeRenameOut(BaseModel):
    name: str
    payee_keys: list[str]
    updated: int
    similar: int  # payees left without an alias that look like this name


class PayeeResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payee_keys: list[PayeeKey] = Field(min_length=1, max_length=50)


class PayeeResetOut(BaseModel):
    payee_keys: list[str]
    restored: int


class PayeeDismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payee_key: PayeeKey
    name: Annotated[str, Field(min_length=1, max_length=120)]


class PayeeDismissOut(BaseModel):
    payee_key: str
    name: str


class PayeeAliasOut(BaseModel):
    payee_key: str
    name: str
    original: str
    count: int


class PayeeSuggested(BaseModel):
    payee_key: str
    merchant: str | None
    counterparty: str | None
    vpa: str | None
    count: int
    total: Money
    last_at: date
    suggest: AliasSuggestion


class PayeeAliases(BaseModel):
    items: list[PayeeAliasOut]
    suggestions: list[PayeeSuggested]


class PayeeMatch(BaseModel):
    payee_key: str
    merchant: str | None
    counterparty: str | None
    vpa: str | None
    alias: str | None
    why: Literal["contains", "prefix", "words", "spelling"]
    count: int
    total: Money
    last_at: date


class PayeeMatches(BaseModel):
    items: list[PayeeMatch]


class RulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class RuleState(BaseModel):
    id: str
    enabled: bool


class TxnPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str | None = Field(default=None, max_length=2000)
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] | None = Field(default=None, max_length=20)


class TxnNotes(BaseModel):
    id: int
    notes: str | None
    tags: list[str]


class UnfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    txn_ids: list[int] = Field(min_length=1, max_length=500)
    rule_id: str | None = Field(default=None, max_length=40)


class UnfileOut(BaseModel):
    restored: int
    rule_removed: bool


# --- live net worth ------------------------------------------------------------------------------

class LiveComponent(BaseModel):
    key: str
    label: str
    asset_class: str
    amount: Money
    share_pct: float
    source: Literal["sheet", "statement", "manual", "loans", "prices", "estimate"]
    as_of: date
    stale: bool
    editable: bool
    change_since: Money | None


class NetWorthChange(BaseModel):
    period: Literal["month", "year", "fy"]
    since: date
    amount: Money | None
    pct: float | None


class HistoryPoint(BaseModel):
    date: date
    net_worth: Money
    kind: Literal["snapshot", "live"]


class MonthChange(BaseModel):
    start: date
    end: date
    start_value: Money
    end_value: Money
    change: Money
    cash_change: Money | None  # null on the live interval until every cash balance is newer than its start
    contributions: Money
    market: Money | None
    live: bool


class ProjectionPoint(BaseModel):
    date: date
    net_worth: Money


class Projection(BaseModel):
    monthly_change: Money
    basis_months: int
    points: list[ProjectionPoint]


class LiveNetWorth(BaseModel):
    as_of: date
    net_worth: Money | None
    liquid: Money | None
    components: list[LiveComponent]
    by_asset_class: dict[str, Money]
    changes: list[NetWorthChange]
    history: list[HistoryPoint]
    months: list[MonthChange]
    projection: Projection | None


class ComponentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str | int = Field()
    as_of: date | None = None


class ComponentOut(BaseModel):
    key: str
    amount: Money
    as_of: date


class HoldingOut(BaseModel):
    name: str
    isin: str | None
    units: str
    units_as_of: date
    source: str
    price: str | None
    price_date: date | None
    value: Money | None


class Holdings(BaseModel):
    items: list[HoldingOut]


# --- source health -------------------------------------------------------------------------------

class Unparsed(BaseModel):
    sender: str | None
    subject: str | None
    status: str
    count: int
    first_seen: datetime
    last_seen: datetime


class UploadOut(BaseModel):
    id: int
    filename: str | None
    received_at: datetime
    status: str
    account: str | None
    period_start: date | None
    period_end: date | None
    diff: Money | None
    reconciled: bool


class ParseQueue(BaseModel):
    collected: dict[str, int]  # collector messages by parse_status
    unparsed: list[Unparsed]
    uploads: list[UploadOut]


class McpTokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool
    kind: Literal["token", "app"] = "token"  # app: an MCP client connected by signing in (OAuth)
    can_write: bool = True
    token: str | None = None  # only in the create response


class McpTokens(BaseModel):
    items: list[McpTokenOut]


class McpTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=60)]


class BackupStatus(BaseModel):
    configured: bool  # a backup has reported at least once
    last_run_at: datetime | None
    last_ok: bool
    last_detail: str | None
    last_ok_at: datetime | None
    stale: bool  # no good backup in the last 2 days


class NotifyTestOut(BaseModel):
    sent: bool


LoanAmount = Annotated[str, Field(pattern=r"^\d{1,12}(\.\d{1,2})?$")]
TxnIds = Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1, max_length=200)]


class LoanItem(BaseModel):
    id: int
    direction: str  # lent | borrowed
    counterparty: str
    payee_key: str | None
    started_on: date
    opening_amount: Money
    note: str | None
    status: str  # open | settled | written_off
    closed_on: date | None
    given: Money  # lent out (or borrowed), opening included
    returned: Money  # paid back so far
    outstanding: Money  # 0 once closed
    repayments: int
    last_at: date | None


class LoanPickItem(LoanItem):
    same_payee: bool


class LoanTotals(BaseModel):
    owed_to_you: Money
    you_owe: Money
    open_lent: int
    open_borrowed: int
    repaid_fy: Money
    repaid_fy_count: int
    fy_start: date


class Loans(BaseModel):
    items: list[LoanItem]
    unassigned: list[TxnOut]  # filed under Loans without a loan
    totals: LoanTotals


class LoanTxn(TxnOut):
    balance_after: Money


class LoanDetail(BaseModel):
    loan: LoanItem
    txns: list[LoanTxn]
    suggestions: list[TxnOut]


class LoanPicker(BaseModel):
    txn_id: int
    loan_id: int | None
    new_direction: str
    counterparty: str | None
    loans: list[LoanPickItem]
    other_txns: list[TxnOut]


class LoanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["lent", "borrowed"] | None = None
    counterparty: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    started_on: date | None = None
    opening_amount: LoanAmount = "0"
    note: Annotated[str, Field(max_length=2000)] | None = None
    txn_ids: Annotated[list[Annotated[int, Field(ge=1)]], Field(max_length=200)] = []


class LoanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counterparty: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    note: Annotated[str, Field(max_length=2000)] | None = None
    opening_amount: LoanAmount | None = None
    started_on: date | None = None


class LoanTxnsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    txn_ids: TxnIds


class LoanWriteOffIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: Annotated[int, Field(ge=1)]


class LoanAttachOut(BaseModel):
    loan_id: int
    attached: int


class LoanDetachOut(BaseModel):
    loan_id: int
    removed: int


class LoanLine(BaseModel):
    amount: Money
    count: int
    people: list[str]


class LoanLines(BaseModel):
    lent: LoanLine
    repaid_to_you: LoanLine
    borrowed: LoanLine
    repaid_by_you: LoanLine


Summary.model_rebuild()
