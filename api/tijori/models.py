"""Database schema (PLAN §6). Every table holding personal data carries `member_id` so
row-level security can scope it; see migrations/versions/0001_initial.py for the policies."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tijori.classify.taxonomy import BUCKETS, KINDS

Money = Numeric(14, 2)

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _enum(name: str, *values: str) -> Enum:
    return Enum(*values, name=name, native_enum=False, create_constraint=True, length=24)


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(), primary_key=True)


def _member_fk(nullable: bool = False) -> Mapped[Any]:
    return mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"), nullable=nullable, index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


DIRECTION = ("debit", "credit")
ONBOARDING_STEPS = ("profile", "mail", "statement_passwords", "first_upload", "done")
TXN_SOURCES = ("statement", "alert", "sms", "upload", "expected", "import")
CLASSIFIED_BY = ("rule", "payee_memory", "dictionary", "heuristic", "user", "system")


class Household(Base):
    __tablename__ = "household"
    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created()


class Member(Base):
    __tablename__ = "member"
    id: Mapped[int] = _pk()
    household_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("household.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(_enum("member_role", "member", "admin"), server_default="member")
    # MemberProfile for the classifier: own names, handles, account masks, local-shop cap.
    classify_config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # UI/reporting preferences, e.g. {"month_start_day": 1}.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    onboarding_step: Mapped[str] = mapped_column(
        _enum("onboarding_step", *ONBOARDING_STEPS), server_default="profile"
    )
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("uq_member_email_lower", func.lower(email), unique=True),)


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    kind: Mapped[str] = mapped_column(_enum("account_kind", "bank", "card", "wallet", "deposit", "holding", "cash"))
    institution: Mapped[str] = mapped_column(String(80))
    name: Mapped[str | None] = mapped_column(String(120))
    mask: Mapped[str | None] = mapped_column(String(8))  # last 4 digits only, never the full number
    currency: Mapped[str] = mapped_column(String(3), server_default="INR")
    opened_at: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[date | None] = mapped_column(Date)


class Source(Base):
    __tablename__ = "source"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    kind: Mapped[str] = mapped_column(_enum("source_kind", "gmail", "apps_script", "sms", "upload"))
    secret_hash: Mapped[str | None] = mapped_column(String(128))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(_enum("source_status", "active", "paused", "error"), server_default="active")


class RawMessage(Base):
    """Immutable: parsers can be re-run over history from here."""

    __tablename__ = "raw_message"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source.id", ondelete="SET NULL"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(String(998))  # RFC 5322 Message-ID
    sender: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    blob_ref: Mapped[str] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(
        _enum("parse_status", "pending", "parsed", "failed", "parser_needed", "needs_password", "ignored"),
        server_default="pending",
    )
    mail_source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mail_source.id", ondelete="SET NULL"))
    mail_uid: Mapped[int | None] = mapped_column(BigInteger)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # file deleted under retention
    __table_args__ = (UniqueConstraint("member_id", "sha256"),)


class RawAttachment(Base):
    __tablename__ = "raw_attachment"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    raw_message_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("raw_message.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    blob_ref: Mapped[str] = mapped_column(Text)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Observation(Base):
    __tablename__ = "observation"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    raw_message_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("raw_message.id", ondelete="CASCADE"))
    parser: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("account.id", ondelete="SET NULL"))
    occurred_at: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    direction: Mapped[str] = mapped_column(_enum("obs_direction", *DIRECTION))
    merchant_raw: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(Text)
    ref_no: Mapped[str | None] = mapped_column(String(64), index=True)
    balance_after: Mapped[Decimal | None] = mapped_column(Money)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="1.0")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Category(Base):
    __tablename__ = "category"
    id: Mapped[int] = _pk()
    household_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("household.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int | None] = _member_fk(nullable=True)  # NULL: shared by the household
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("category.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(_enum("category_kind", *KINDS))
    bucket: Mapped[str] = mapped_column(_enum("category_bucket", *BUCKETS))
    credit_bucket: Mapped[str | None] = mapped_column(_enum("category_credit_bucket", *BUCKETS))  # credits, if not `bucket`
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    __table_args__ = (
        UniqueConstraint("household_id", "member_id", "name", postgresql_nulls_not_distinct=True),
    )


class Txn(Base):
    __tablename__ = "txn"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("account.id", ondelete="SET NULL"))
    occurred_at: Mapped[date] = mapped_column(Date)
    posted_at: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), server_default="INR")
    fx_amount: Mapped[Decimal | None] = mapped_column(Money)
    direction: Mapped[str] = mapped_column(_enum("txn_direction", *DIRECTION))
    kind: Mapped[str] = mapped_column(_enum("txn_kind", *KINDS))
    merchant_norm: Mapped[str | None] = mapped_column(String(120))
    counterparty: Mapped[str | None] = mapped_column(Text)
    ref_no: Mapped[str | None] = mapped_column(String(64))
    narration: Mapped[str | None] = mapped_column(Text)
    vpa: Mapped[str | None] = mapped_column(String(120))
    payee_key: Mapped[str | None] = mapped_column(String(80))
    category_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("category.id", ondelete="SET NULL"))
    bucket: Mapped[str | None] = mapped_column(_enum("txn_bucket", *BUCKETS))
    classified_by: Mapped[str | None] = mapped_column(_enum("classified_by", *CLASSIFIED_BY))
    rule_id: Mapped[str | None] = mapped_column(String(80))  # "rule:12", "dict:blinkit", "kind:salary", ...
    review_reason: Mapped[str | None] = mapped_column(String(40))  # why it sits in the Inbox
    status: Mapped[str] = mapped_column(
        _enum("txn_status", "pending", "posted", "reconciled", "flagged"), server_default="posted"
    )
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    sources: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    split_of: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("txn.id", ondelete="CASCADE"), index=True)
    loan_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("loan.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        UniqueConstraint("member_id", "dedupe_key"),
        Index("ix_txn_member_occurred", "member_id", "occurred_at"),
        Index("ix_txn_member_category", "member_id", "category_id"),
        Index("ix_txn_member_payee", "member_id", "payee_key"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(f"sources <@ ARRAY[{', '.join(repr(x) for x in TXN_SOURCES)}]::text[]", name="known_sources"),
    )


class TxnObservation(Base):
    __tablename__ = "txn_observation"
    txn_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("txn.id", ondelete="CASCADE"), primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("observation.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[int] = _member_fk()


class TxnLink(Base):
    __tablename__ = "txn_link"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    a_txn_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("txn.id", ondelete="CASCADE"))
    b_txn_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("txn.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(
        _enum("txn_link_kind", "transfer", "refund", "dup", "pass_through", "card_payment", "reversal")
    )
    __table_args__ = (UniqueConstraint("a_txn_id", "b_txn_id", "kind"),)


class Rule(Base):
    __tablename__ = "rule"
    id: Mapped[int] = _pk()
    household_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("household.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(_enum("rule_scope", "household", "member"))
    member_id: Mapped[int | None] = _member_fk(nullable=True)
    # {"vpa"|"merchant"|"narration_regex": ..., "min_amount", "max_amount", "account_id", "direction"}
    match_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("category.id", ondelete="CASCADE"))
    kind: Mapped[str | None] = mapped_column(_enum("rule_kind", *KINDS))
    priority: Mapped[int] = mapped_column(Integer, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("(scope = 'household') = (member_id IS NULL)", name="scope_matches_member"),
    )


class RuleHit(Base):
    __tablename__ = "rule_hit"
    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rule.id", ondelete="CASCADE"), primary_key=True)
    txn_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("txn.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int] = _member_fk()
    at: Mapped[datetime] = _created()


class Merchant(Base):
    """Shared dictionary (not personal): mirrors tijori.classify.brands for the UI and overrides."""

    __tablename__ = "merchant"
    id: Mapped[int] = _pk()
    norm_name: Mapped[str] = mapped_column(String(120), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    default_category: Mapped[str | None] = mapped_column(String(80))


class PayeeAlias(Base):
    """The member's name for a payee (services/aliases). Payees sharing a name group as one merchant."""

    __tablename__ = "payee_alias"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    payee_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    original: Mapped[str] = mapped_column(String(120))  # merchant_norm before the alias; restored on reset
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("member_id", "payee_key"),
        Index("ix_payee_alias_member_name", "member_id", "name"),
    )


class Recurring(Base):
    __tablename__ = "recurring"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    merchant_norm: Mapped[str] = mapped_column(String(120))
    cadence: Mapped[str] = mapped_column(_enum("recurring_cadence", "weekly", "monthly", "quarterly", "yearly"))
    amount_expected: Mapped[Decimal] = mapped_column(Money)
    next_due: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(_enum("recurring_status", "active", "paused", "ended"), server_default="active")
    # The series identity (services/recurring.payee_key_expr); decision null = detection decides.
    payee_key: Mapped[str | None] = mapped_column(String(120))
    decision: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str | None] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("member_id", "payee_key"),
        CheckConstraint("decision IS NULL OR decision IN ('confirmed', 'dismissed')", name="known_decision"),
        CheckConstraint("kind IS NULL OR kind IN ('subscription', 'bill', 'invest', 'other')", name="known_kind"),
    )


class Budget(Base):
    __tablename__ = "budget"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("category.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(_enum("budget_period", "monthly"), server_default="monthly")
    amount: Mapped[Decimal] = mapped_column(Money)
    rollover: Mapped[bool] = mapped_column(Boolean, server_default="false")
    __table_args__ = (UniqueConstraint("member_id", "category_id", "period"),)


class Statement(Base):
    __tablename__ = "statement"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("account.id", ondelete="CASCADE"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    opening: Mapped[Decimal] = mapped_column(Money)
    closing: Mapped[Decimal] = mapped_column(Money)
    raw_attachment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_attachment.id", ondelete="SET NULL")
    )
    parser: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    diff: Mapped[Decimal | None] = mapped_column(Money)
    # Card statements only: what the bank asks you to pay, and by when.
    total_due: Mapped[Decimal | None] = mapped_column(Money)
    due_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("member_id", "account_id", "period_start", "period_end"),)


class Holding(Base):
    __tablename__ = "holding"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("account.id", ondelete="SET NULL"))
    isin: Mapped[str | None] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(160))
    units: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(40))


class Price(Base):
    """Public market data (AMFI NAV, NSE close); not personal, so no member_id."""

    __tablename__ = "price"
    isin_or_symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(Numeric(14, 4))  # NAVs carry 4 decimals
    source: Mapped[str] = mapped_column(String(16))


class Snapshot(Base):
    __tablename__ = "snapshot"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    date: Mapped[date] = mapped_column(Date)
    components_json: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"sbi": "33786.00", ...}
    net_worth: Mapped[Decimal] = mapped_column(Money)
    liquid: Mapped[Decimal | None] = mapped_column(Money)
    remark: Mapped[str | None] = mapped_column(Text)  # the member's own note ("Remarks" in the sheet)
    commentary: Mapped[str | None] = mapped_column(Text)  # "My Understanding", or templated
    locked: Mapped[bool] = mapped_column(Boolean, server_default="false")
    __table_args__ = (UniqueConstraint("member_id", "date"),)


class Alert(Base):
    __tablename__ = "alert"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    kind: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Share(Base):
    __tablename__ = "share"
    id: Mapped[int] = _pk()
    grantor_member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"), index=True)
    grantee_member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"), index=True)
    # {"all": true} or {"account_ids": [1, 2]}; summary-only sharing is a TODO.
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("grantor_member_id", "grantee_member_id"),
        CheckConstraint("grantor_member_id <> grantee_member_id", name="not_self"),
    )


class Secret(Base):
    """App and statement passwords, sealed by SecretBox (AES-256-GCM envelope; see secretbox.py)."""

    __tablename__ = "secret"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    name: Mapped[str] = mapped_column(String(80))
    key_version: Mapped[str] = mapped_column(String(16))
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    key_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    label: Mapped[str | None] = mapped_column(String(40))  # the member's name for it; never part of the secret
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("member_id", "name"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str | None] = mapped_column(String(160))
    at: Mapped[datetime] = _created()
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class AuthSession(Base):
    """A signed-in browser. The cookie holds a random id; only its SHA-256 is stored."""

    __tablename__ = "auth_session"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[int] = _member_fk()
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthState(Base):
    """One pending Google sign-in: single use, ten minutes. Holds no member data."""

    __tablename__ = "oauth_state"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Hash of the short-lived login cookie: the callback must come back to the same browser.
    browser_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(128))
    code_verifier: Mapped[str] = mapped_column(String(128))
    invite_hash: Mapped[str | None] = mapped_column(String(64))
    return_to: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Invite(Base):
    """Household invite: single use, 7 days; only the token's SHA-256 is stored."""

    __tablename__ = "invite"
    id: Mapped[int] = _pk()
    household_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("household.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="SET NULL"))


class MailSource(Base):
    """An IMAP mailbox the member connected with an app password (kept in `secret`)."""

    __tablename__ = "mail_source"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    provider: Mapped[str] = mapped_column(_enum("mail_provider", "gmail", "outlook", "yahoo", "custom"))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(320))
    label: Mapped[str] = mapped_column(String(100), server_default="tijori")
    status: Mapped[str] = mapped_column(_enum("mail_status", "untested", "ok", "error"), server_default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    last_message_count: Mapped[int | None] = mapped_column(Integer)  # messages in the label at the last test
    # Collector watermark: UIDs are only comparable within one UIDVALIDITY.
    uid_validity: Mapped[int | None] = mapped_column(BigInteger)
    last_uid: Mapped[int | None] = mapped_column(BigInteger)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_error: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _created()


class ComponentValue(Base):
    """A net-worth component value the member set by hand (EPF passbook, gold, anything without a feed)."""

    __tablename__ = "component_value"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    key: Mapped[str] = mapped_column(String(24))  # one of networth.COMPONENT_KEYS
    amount: Mapped[Decimal] = mapped_column(Money)
    as_of: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()
    source: Mapped[str | None] = mapped_column(String(16))  # null or "manual": set by hand; "statement": read from one
    __table_args__ = (UniqueConstraint("member_id", "key", "as_of"),)


class Loan(Base):
    """Money lent to or borrowed from one person; its txns carry loan_id and sit outside spend and income."""

    __tablename__ = "loan"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    direction: Mapped[str] = mapped_column(_enum("loan_direction", "lent", "borrowed"))
    counterparty: Mapped[str] = mapped_column(String(120))
    payee_key: Mapped[str | None] = mapped_column(String(80))  # the handle its payments come from, for suggestions
    started_on: Mapped[date] = mapped_column(Date)
    opening_amount: Mapped[Decimal] = mapped_column(Money, server_default="0")  # owed before the first txn on file
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(_enum("loan_status", "open", "settled", "written_off"), server_default="open")
    closed_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (CheckConstraint("opening_amount >= 0", name="opening_non_negative"),)


class OpsEvent(Base):
    """A host job's report, such as one off-site backup run; written by the job as the owner, read by the app."""

    __tablename__ = "ops_event"
    id: Mapped[int] = _pk()
    kind: Mapped[str] = mapped_column(String(40))
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_ops_event_kind_at", "kind", "at"),)


class McpToken(Base):
    """MCP access for one member: a pasted bearer token (only its SHA-256 is stored), or a connected app's
    OAuth grant (client_id set, no token_hash; its tokens are in oauth_token)."""

    __tablename__ = "mcp_token"
    id: Mapped[int] = _pk()
    member_id: Mapped[int] = _member_fk()
    name: Mapped[str] = mapped_column(String(60))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    client_id: Mapped[str | None] = mapped_column(String(512))
    scope: Mapped[str | None] = mapped_column(String(200))  # None: everything
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthClient(Base):
    """An MCP client: self-registered (RFC 7591), or a cached client ID metadata document."""

    __tablename__ = "oauth_client"
    client_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(120))
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text))
    auth_method: Mapped[str] = mapped_column(String(24))
    secret_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthRequest(Base):
    """An authorization request waiting for sign-in and consent: ten minutes."""

    __tablename__ = "oauth_request"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(512))
    redirect_uri: Mapped[str] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(200))
    member_id: Mapped[int | None] = _member_fk(nullable=True)
    csrf_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthCode(Base):
    """An authorization code: single use, a few minutes."""

    __tablename__ = "oauth_code"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(512))
    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"))
    household_id: Mapped[int] = mapped_column(BigInteger)
    redirect_uri: Mapped[str] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthToken(Base):
    """A connected app's access or refresh token; only its SHA-256 is stored."""

    __tablename__ = "oauth_token"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mcp_token.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"))
    household_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
