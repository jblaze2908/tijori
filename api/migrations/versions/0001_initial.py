"""Initial schema plus row-level security.

Revision ID: 0001
Revises: 
Create Date: 2026-09-26 22:11:18.667053
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('household',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_household'))
    )
    op.create_table('merchant',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('norm_name', sa.String(length=120), nullable=False),
    sa.Column('aliases', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('default_category', sa.String(length=80), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchant')),
    sa.UniqueConstraint('norm_name', name=op.f('uq_merchant_norm_name'))
    )
    op.create_table('price',
    sa.Column('isin_or_symbol', sa.String(length=32), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('close', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.PrimaryKeyConstraint('isin_or_symbol', 'date', name=op.f('pk_price'))
    )
    op.create_table('member',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('household_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('role', sa.Enum('member', 'admin', name='member_role', native_enum=False, create_constraint=True, length=24), server_default='member', nullable=False),
    sa.Column('classify_config', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], name=op.f('fk_member_household_id_household'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_member'))
    )
    op.create_index(op.f('ix_member_household_id'), 'member', ['household_id'], unique=False)
    op.create_index('uq_member_email_lower', 'member', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('account',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('bank', 'card', 'wallet', 'deposit', 'holding', 'cash', name='account_kind', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('institution', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=True),
    sa.Column('mask', sa.String(length=8), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('opened_at', sa.Date(), nullable=True),
    sa.Column('closed_at', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_account_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_account'))
    )
    op.create_index(op.f('ix_account_member_id'), 'account', ['member_id'], unique=False)
    op.create_table('alert',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_alert_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alert'))
    )
    op.create_index(op.f('ix_alert_member_id'), 'alert', ['member_id'], unique=False)
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('actor', sa.String(length=80), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('target', sa.String(length=160), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('detail_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_audit_log_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_member_id'), 'audit_log', ['member_id'], unique=False)
    op.create_table('category',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('household_id', sa.BigInteger(), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=True),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('kind', sa.Enum('spend', 'income', 'transfer', 'investment', 'refund', 'fee', 'cash', name='category_kind', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('bucket', sa.Enum('everyday', 'oneoff', 'card', 'invest', 'income', 'excluded', name='category_bucket', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], name=op.f('fk_category_household_id_household'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_category_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['category.id'], name=op.f('fk_category_parent_id_category'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_category')),
    sa.UniqueConstraint('household_id', 'member_id', 'name', name=op.f('uq_category_household_id_member_id_name'), postgresql_nulls_not_distinct=True)
    )
    op.create_index(op.f('ix_category_household_id'), 'category', ['household_id'], unique=False)
    op.create_index(op.f('ix_category_member_id'), 'category', ['member_id'], unique=False)
    op.create_table('recurring',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('merchant_norm', sa.String(length=120), nullable=False),
    sa.Column('cadence', sa.Enum('weekly', 'monthly', 'quarterly', 'yearly', name='recurring_cadence', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('amount_expected', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('next_due', sa.Date(), nullable=True),
    sa.Column('status', sa.Enum('active', 'paused', 'ended', name='recurring_status', native_enum=False, create_constraint=True, length=24), server_default='active', nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_recurring_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recurring'))
    )
    op.create_index(op.f('ix_recurring_member_id'), 'recurring', ['member_id'], unique=False)
    op.create_table('secret',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_secret_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_secret')),
    sa.UniqueConstraint('member_id', 'name', name=op.f('uq_secret_member_id_name'))
    )
    op.create_index(op.f('ix_secret_member_id'), 'secret', ['member_id'], unique=False)
    op.create_table('share',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('grantor_member_id', sa.BigInteger(), nullable=False),
    sa.Column('grantee_member_id', sa.BigInteger(), nullable=False),
    sa.Column('scope_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('grantor_member_id <> grantee_member_id', name=op.f('ck_share_not_self')),
    sa.ForeignKeyConstraint(['grantee_member_id'], ['member.id'], name=op.f('fk_share_grantee_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['grantor_member_id'], ['member.id'], name=op.f('fk_share_grantor_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_share')),
    sa.UniqueConstraint('grantor_member_id', 'grantee_member_id', name=op.f('uq_share_grantor_member_id_grantee_member_id'))
    )
    op.create_index(op.f('ix_share_grantee_member_id'), 'share', ['grantee_member_id'], unique=False)
    op.create_index(op.f('ix_share_grantor_member_id'), 'share', ['grantor_member_id'], unique=False)
    op.create_table('snapshot',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('components_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('net_worth', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('liquid', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('commentary', sa.Text(), nullable=True),
    sa.Column('locked', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_snapshot_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_snapshot')),
    sa.UniqueConstraint('member_id', 'date', name=op.f('uq_snapshot_member_id_date'))
    )
    op.create_index(op.f('ix_snapshot_member_id'), 'snapshot', ['member_id'], unique=False)
    op.create_table('source',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('gmail', 'apps_script', 'sms', 'upload', name='source_kind', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('secret_hash', sa.String(length=128), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Enum('active', 'paused', 'error', name='source_status', native_enum=False, create_constraint=True, length=24), server_default='active', nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_source_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source'))
    )
    op.create_index(op.f('ix_source_member_id'), 'source', ['member_id'], unique=False)
    op.create_table('budget',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=False),
    sa.Column('period', sa.Enum('monthly', name='budget_period', native_enum=False, create_constraint=True, length=24), server_default='monthly', nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('rollover', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['category.id'], name=op.f('fk_budget_category_id_category'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_budget_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_budget')),
    sa.UniqueConstraint('member_id', 'category_id', 'period', name=op.f('uq_budget_member_id_category_id_period'))
    )
    op.create_index(op.f('ix_budget_member_id'), 'budget', ['member_id'], unique=False)
    op.create_table('holding',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('isin', sa.String(length=12), nullable=True),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('units', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_holding_account_id_account'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_holding_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_holding'))
    )
    op.create_index(op.f('ix_holding_member_id'), 'holding', ['member_id'], unique=False)
    op.create_table('raw_message',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('source_id', sa.BigInteger(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('message_id', sa.String(length=998), nullable=True),
    sa.Column('sender', sa.String(length=320), nullable=True),
    sa.Column('subject', sa.Text(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('blob_ref', sa.Text(), nullable=False),
    sa.Column('parse_status', sa.Enum('pending', 'parsed', 'failed', 'parser_needed', name='parse_status', native_enum=False, create_constraint=True, length=24), server_default='pending', nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_raw_message_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['source.id'], name=op.f('fk_raw_message_source_id_source'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_raw_message')),
    sa.UniqueConstraint('member_id', 'sha256', name=op.f('uq_raw_message_member_id_sha256'))
    )
    op.create_index(op.f('ix_raw_message_member_id'), 'raw_message', ['member_id'], unique=False)
    op.create_table('rule',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('household_id', sa.BigInteger(), nullable=False),
    sa.Column('scope', sa.Enum('household', 'member', name='rule_scope', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=True),
    sa.Column('match_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('spend', 'income', 'transfer', 'investment', 'refund', 'fee', 'cash', name='rule_kind', native_enum=False, create_constraint=True, length=24), nullable=True),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(scope = 'household') = (member_id IS NULL)", name=op.f('ck_rule_scope_matches_member')),
    sa.ForeignKeyConstraint(['category_id'], ['category.id'], name=op.f('fk_rule_category_id_category'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], name=op.f('fk_rule_household_id_household'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_rule_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rule'))
    )
    op.create_index(op.f('ix_rule_household_id'), 'rule', ['household_id'], unique=False)
    op.create_index(op.f('ix_rule_member_id'), 'rule', ['member_id'], unique=False)
    op.create_table('txn',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('occurred_at', sa.Date(), nullable=False),
    sa.Column('posted_at', sa.Date(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('fx_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('direction', sa.Enum('debit', 'credit', name='txn_direction', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('kind', sa.Enum('spend', 'income', 'transfer', 'investment', 'refund', 'fee', 'cash', name='txn_kind', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('merchant_norm', sa.String(length=120), nullable=True),
    sa.Column('counterparty', sa.Text(), nullable=True),
    sa.Column('ref_no', sa.String(length=64), nullable=True),
    sa.Column('narration', sa.Text(), nullable=True),
    sa.Column('vpa', sa.String(length=120), nullable=True),
    sa.Column('payee_key', sa.String(length=80), nullable=True),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('bucket', sa.Enum('everyday', 'oneoff', 'card', 'invest', 'income', 'excluded', name='txn_bucket', native_enum=False, create_constraint=True, length=24), nullable=True),
    sa.Column('classified_by', sa.Enum('rule', 'payee_memory', 'dictionary', 'heuristic', 'user', 'system', name='classified_by', native_enum=False, create_constraint=True, length=24), nullable=True),
    sa.Column('rule_id', sa.String(length=80), nullable=True),
    sa.Column('review_reason', sa.String(length=40), nullable=True),
    sa.Column('status', sa.Enum('pending', 'posted', 'reconciled', 'flagged', name='txn_status', native_enum=False, create_constraint=True, length=24), server_default='posted', nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('tags', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('dedupe_key', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount >= 0', name=op.f('ck_txn_amount_non_negative')),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_txn_account_id_account'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['category.id'], name=op.f('fk_txn_category_id_category'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_txn_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_txn')),
    sa.UniqueConstraint('member_id', 'dedupe_key', name=op.f('uq_txn_member_id_dedupe_key'))
    )
    op.create_index('ix_txn_member_category', 'txn', ['member_id', 'category_id'], unique=False)
    op.create_index(op.f('ix_txn_member_id'), 'txn', ['member_id'], unique=False)
    op.create_index('ix_txn_member_occurred', 'txn', ['member_id', 'occurred_at'], unique=False)
    op.create_index('ix_txn_member_payee', 'txn', ['member_id', 'payee_key'], unique=False)
    op.create_table('observation',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('raw_message_id', sa.BigInteger(), nullable=True),
    sa.Column('parser', sa.String(length=64), nullable=False),
    sa.Column('parser_version', sa.String(length=32), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('occurred_at', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('direction', sa.Enum('debit', 'credit', name='obs_direction', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('merchant_raw', sa.Text(), nullable=True),
    sa.Column('counterparty', sa.Text(), nullable=True),
    sa.Column('ref_no', sa.String(length=64), nullable=True),
    sa.Column('balance_after', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=3, scale=2), server_default='1.0', nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_observation_account_id_account'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_observation_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['raw_message_id'], ['raw_message.id'], name=op.f('fk_observation_raw_message_id_raw_message'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_observation'))
    )
    op.create_index(op.f('ix_observation_member_id'), 'observation', ['member_id'], unique=False)
    op.create_index(op.f('ix_observation_ref_no'), 'observation', ['ref_no'], unique=False)
    op.create_table('raw_attachment',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('raw_message_id', sa.BigInteger(), nullable=False),
    sa.Column('filename', sa.Text(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('blob_ref', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_raw_attachment_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['raw_message_id'], ['raw_message.id'], name=op.f('fk_raw_attachment_raw_message_id_raw_message'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_raw_attachment'))
    )
    op.create_index(op.f('ix_raw_attachment_member_id'), 'raw_attachment', ['member_id'], unique=False)
    op.create_index(op.f('ix_raw_attachment_raw_message_id'), 'raw_attachment', ['raw_message_id'], unique=False)
    op.create_table('rule_hit',
    sa.Column('rule_id', sa.BigInteger(), nullable=False),
    sa.Column('txn_id', sa.BigInteger(), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_rule_hit_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_id'], ['rule.id'], name=op.f('fk_rule_hit_rule_id_rule'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['txn_id'], ['txn.id'], name=op.f('fk_rule_hit_txn_id_txn'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('rule_id', 'txn_id', name=op.f('pk_rule_hit'))
    )
    op.create_index(op.f('ix_rule_hit_member_id'), 'rule_hit', ['member_id'], unique=False)
    op.create_table('txn_link',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('a_txn_id', sa.BigInteger(), nullable=False),
    sa.Column('b_txn_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('transfer', 'refund', 'dup', 'pass_through', 'card_payment', 'reversal', name='txn_link_kind', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.ForeignKeyConstraint(['a_txn_id'], ['txn.id'], name=op.f('fk_txn_link_a_txn_id_txn'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['b_txn_id'], ['txn.id'], name=op.f('fk_txn_link_b_txn_id_txn'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_txn_link_member_id_member'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_txn_link')),
    sa.UniqueConstraint('a_txn_id', 'b_txn_id', 'kind', name=op.f('uq_txn_link_a_txn_id_b_txn_id_kind'))
    )
    op.create_index(op.f('ix_txn_link_member_id'), 'txn_link', ['member_id'], unique=False)
    op.create_table('statement',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('opening', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('closing', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('raw_attachment_id', sa.BigInteger(), nullable=True),
    sa.Column('parser', sa.String(length=64), nullable=True),
    sa.Column('parser_version', sa.String(length=32), nullable=True),
    sa.Column('reconciled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('diff', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_statement_account_id_account'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_statement_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['raw_attachment_id'], ['raw_attachment.id'], name=op.f('fk_statement_raw_attachment_id_raw_attachment'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_statement'))
    )
    op.create_index(op.f('ix_statement_member_id'), 'statement', ['member_id'], unique=False)
    op.create_table('txn_observation',
    sa.Column('txn_id', sa.BigInteger(), nullable=False),
    sa.Column('observation_id', sa.BigInteger(), nullable=False),
    sa.Column('member_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_txn_observation_member_id_member'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['observation_id'], ['observation.id'], name=op.f('fk_txn_observation_observation_id_observation'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['txn_id'], ['txn.id'], name=op.f('fk_txn_observation_txn_id_txn'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('txn_id', 'observation_id', name=op.f('pk_txn_observation'))
    )
    op.create_index(op.f('ix_txn_observation_member_id'), 'txn_observation', ['member_id'], unique=False)
    _security_up()


def downgrade() -> None:
    _security_down()
    op.drop_index(op.f('ix_txn_observation_member_id'), table_name='txn_observation')
    op.drop_table('txn_observation')
    op.drop_index(op.f('ix_statement_member_id'), table_name='statement')
    op.drop_table('statement')
    op.drop_index(op.f('ix_txn_link_member_id'), table_name='txn_link')
    op.drop_table('txn_link')
    op.drop_index(op.f('ix_rule_hit_member_id'), table_name='rule_hit')
    op.drop_table('rule_hit')
    op.drop_index(op.f('ix_raw_attachment_raw_message_id'), table_name='raw_attachment')
    op.drop_index(op.f('ix_raw_attachment_member_id'), table_name='raw_attachment')
    op.drop_table('raw_attachment')
    op.drop_index(op.f('ix_observation_ref_no'), table_name='observation')
    op.drop_index(op.f('ix_observation_member_id'), table_name='observation')
    op.drop_table('observation')
    op.drop_index('ix_txn_member_payee', table_name='txn')
    op.drop_index('ix_txn_member_occurred', table_name='txn')
    op.drop_index(op.f('ix_txn_member_id'), table_name='txn')
    op.drop_index('ix_txn_member_category', table_name='txn')
    op.drop_table('txn')
    op.drop_index(op.f('ix_rule_member_id'), table_name='rule')
    op.drop_index(op.f('ix_rule_household_id'), table_name='rule')
    op.drop_table('rule')
    op.drop_index(op.f('ix_raw_message_member_id'), table_name='raw_message')
    op.drop_table('raw_message')
    op.drop_index(op.f('ix_holding_member_id'), table_name='holding')
    op.drop_table('holding')
    op.drop_index(op.f('ix_budget_member_id'), table_name='budget')
    op.drop_table('budget')
    op.drop_index(op.f('ix_source_member_id'), table_name='source')
    op.drop_table('source')
    op.drop_index(op.f('ix_snapshot_member_id'), table_name='snapshot')
    op.drop_table('snapshot')
    op.drop_index(op.f('ix_share_grantor_member_id'), table_name='share')
    op.drop_index(op.f('ix_share_grantee_member_id'), table_name='share')
    op.drop_table('share')
    op.drop_index(op.f('ix_secret_member_id'), table_name='secret')
    op.drop_table('secret')
    op.drop_index(op.f('ix_recurring_member_id'), table_name='recurring')
    op.drop_table('recurring')
    op.drop_index(op.f('ix_category_member_id'), table_name='category')
    op.drop_index(op.f('ix_category_household_id'), table_name='category')
    op.drop_table('category')
    op.drop_index(op.f('ix_audit_log_member_id'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index(op.f('ix_alert_member_id'), table_name='alert')
    op.drop_table('alert')
    op.drop_index(op.f('ix_account_member_id'), table_name='account')
    op.drop_table('account')
    op.drop_index('uq_member_email_lower', table_name='member')
    op.drop_index(op.f('ix_member_household_id'), table_name='member')
    op.drop_table('member')
    op.drop_table('price')
    op.drop_table('merchant')
    op.drop_table('household')


APP_ROLE = "tijori_app"

# Tables whose every row belongs to exactly one member.
MEMBER_TABLES = (
    "account", "source", "raw_message", "raw_attachment", "observation", "txn", "txn_observation",
    "txn_link", "recurring", "budget", "statement", "holding", "snapshot", "alert", "secret",
    "audit_log", "rule_hit",
)
# Readable by a grantee through `share`: {"all": true}, or {"account_ids": [...]} where the
# table has an account column.
SHAREABLE = {
    "account": "id", "txn": "account_id", "statement": "account_id", "holding": "account_id",
    "snapshot": None, "recurring": None, "budget": None,
}
APP_DML_TABLES = (*MEMBER_TABLES, "category", "rule", "share", "merchant", "price")


def _security_up() -> None:
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
        END $$""")
    # NULLIF: after a SET LOCAL ends, a custom setting reads back as '' rather than NULL.
    op.execute("""
        CREATE FUNCTION tijori_current_member() RETURNS bigint LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('tijori.member_id', true), '')::bigint $$""")
    op.execute("""
        CREATE FUNCTION tijori_current_household() RETURNS bigint LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('tijori.household_id', true), '')::bigint $$""")
    # Auth needs email -> member before any member context exists; this is the only bypass.
    op.execute("""
        CREATE FUNCTION auth_member_by_email(p_email text)
        RETURNS TABLE(member_id bigint, household_id bigint)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT m.id, m.household_id FROM member m WHERE lower(m.email) = lower(p_email) $$""")
    op.execute("REVOKE ALL ON FUNCTION auth_member_by_email(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth_member_by_email(text) TO {APP_ROLE}")

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(APP_DML_TABLES)} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON household TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE (name, classify_config) ON member TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")

    for t in (*MEMBER_TABLES, "category", "rule", "share", "member", "household"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    # FORCE also binds a non-superuser table owner. household/member stay admin-managed.
    for t in (*MEMBER_TABLES, "category", "rule", "share"):
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")

    for t in MEMBER_TABLES:
        op.execute(f"""
            CREATE POLICY {t}_own ON {t}
            USING (member_id = tijori_current_member())
            WITH CHECK (member_id = tijori_current_member())""")
    for t, account_col in SHAREABLE.items():
        by_account = f" OR s.scope_json->'account_ids' @> to_jsonb({t}.{account_col})" if account_col else ""
        op.execute(f"""
            CREATE POLICY {t}_shared_read ON {t} FOR SELECT
            USING (EXISTS (
              SELECT 1 FROM share s
              WHERE s.grantor_member_id = {t}.member_id
                AND s.grantee_member_id = tijori_current_member()
                AND (s.scope_json @> '{{"all": true}}'::jsonb{by_account})))""")

    # Household-shared rows have member_id NULL; personal rows belong to one member.
    for t in ("category", "rule"):
        op.execute(f"""
            CREATE POLICY {t}_household ON {t}
            USING (household_id = tijori_current_household()
                   AND (member_id IS NULL OR member_id = tijori_current_member()))
            WITH CHECK (household_id = tijori_current_household()
                        AND (member_id IS NULL OR member_id = tijori_current_member()))""")
    op.execute("""
        CREATE POLICY share_visible ON share FOR SELECT
        USING (tijori_current_member() IN (grantor_member_id, grantee_member_id))""")
    op.execute("""
        CREATE POLICY share_grantor_writes ON share
        USING (grantor_member_id = tijori_current_member())
        WITH CHECK (grantor_member_id = tijori_current_member())""")
    op.execute("""
        CREATE POLICY member_same_household ON member FOR SELECT
        USING (household_id = tijori_current_household())""")
    op.execute("""
        CREATE POLICY member_update_self ON member FOR UPDATE
        USING (id = tijori_current_member()) WITH CHECK (id = tijori_current_member())""")
    op.execute("""
        CREATE POLICY household_own ON household FOR SELECT
        USING (id = tijori_current_household())""")


def _security_down() -> None:
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute("DROP FUNCTION IF EXISTS auth_member_by_email(text)")
    # Policies reference these; dropping the tables below removes the policies first.
    for t in (*MEMBER_TABLES, "category", "rule", "share", "member", "household"):
        op.execute(f"DROP POLICY IF EXISTS {t}_own ON {t}")
        op.execute(f"DROP POLICY IF EXISTS {t}_shared_read ON {t}")
        op.execute(f"DROP POLICY IF EXISTS {t}_household ON {t}")
    for p, t in (("share_visible", "share"), ("share_grantor_writes", "share"),
                 ("member_same_household", "member"), ("member_update_self", "member"),
                 ("household_own", "household")):
        op.execute(f"DROP POLICY IF EXISTS {p} ON {t}")
    op.execute("DROP FUNCTION IF EXISTS tijori_current_member()")
    op.execute("DROP FUNCTION IF EXISTS tijori_current_household()")
