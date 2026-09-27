"""Loans: money lent or borrowed, tracked per person.

- loan: one row per loan (lent | borrowed), member-owned under RLS like every member table.
- txn.loan_id: the loan a txn belongs to. Those txns are filed under the Loans category (bucket
  excluded), so they never count as spend or income.
- The Loans category joins the defaults for every household.

Expand-only (docs/deploy.md).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loan",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(24), nullable=False),
        sa.Column("counterparty", sa.String(120), nullable=False),
        sa.Column("payee_key", sa.String(80), nullable=True),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("opening_amount", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), server_default="open", nullable=False),
        sa.Column("closed_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("direction IN ('lent', 'borrowed')", name=op.f("ck_loan_loan_direction")),
        sa.CheckConstraint("status IN ('open', 'settled', 'written_off')", name=op.f("ck_loan_loan_status")),
        sa.CheckConstraint("opening_amount >= 0", name=op.f("ck_loan_opening_non_negative")),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_loan_member_id_member"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_loan")),
    )
    op.create_index(op.f("ix_loan_member_id"), "loan", ["member_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON loan TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE loan ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE loan FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY loan_own ON loan
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")
    op.add_column("txn", sa.Column("loan_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_txn_loan_id_loan"), "txn", "loan", ["loan_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_txn_loan_id"), "txn", ["loan_id"])
    op.execute("""
        INSERT INTO category (household_id, member_id, name, description, kind, bucket, sort_order)
        SELECT p.household_id, NULL, 'Loans',
               'Money lent or borrowed, and its repayments; tracked per loan, never spend or income.',
               'transfer', 'excluded', p.sort_order
        FROM category p WHERE p.name = 'Pass-through' AND p.member_id IS NULL
        ON CONFLICT DO NOTHING""")


def downgrade() -> None:
    op.drop_index(op.f("ix_txn_loan_id"), "txn")
    op.drop_constraint(op.f("fk_txn_loan_id_loan"), "txn", type_="foreignkey")
    op.drop_column("txn", "loan_id")
    op.execute("DROP POLICY IF EXISTS loan_own ON loan")
    op.drop_index(op.f("ix_loan_member_id"), "loan")
    op.drop_table("loan")
