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


class TxnOut(BaseModel):
    id: int
    occurred_at: date
    posted_at: date | None
    amount: Money
    currency: str
    direction: str
    kind: str
    merchant: str | None
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


class TxnPage(BaseModel):
    items: list[TxnOut]
    page: int
    page_size: int
    total: int


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


class PayeeStats(BaseModel):
    payee_key: str
    count: int
    total: Money
    history: list[PayeeHistory]


class TxnDetail(BaseModel):
    transaction: TxnOut
    observations: list[ObservationOut]
    links: list[LinkOut]
    payee: PayeeStats | None


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
    parent_id: int | None
    scope: str  # household | member


class BudgetLine(BaseModel):
    category_id: int
    category: str
    amount: Money
    spent: Money
    remaining: Money
    rollover: bool


class Budgets(BaseModel):
    month: str
    items: list[BudgetLine]


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


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month_start_day: int | None = Field(default=None, ge=1, le=28, strict=True)
    local_shop_cap: str | int | None = None

    @model_validator(mode="after")
    def _something(self) -> "SettingsIn":
        if self.month_start_day is None and self.local_shop_cap is None:
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
    has_statement_password: bool
    last_seen_at: datetime | None
    coverage_pct: float | None


class Accounts(BaseModel):
    items: list[AccountOut]


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


Summary.model_rebuild()


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
