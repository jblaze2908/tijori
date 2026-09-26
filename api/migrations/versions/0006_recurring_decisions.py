"""Subscriptions and card bills: the member's decisions on recurring series (confirm, dismiss, cancel,
kind), and a card statement's total due and due date.

Expand-only (docs/deploy.md): new nullable columns on existing tables (`recurring` is unused since 0001),
so the previous release keeps working on rollback. RLS and grants on both tables already exist.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("recurring", sa.Column("payee_key", sa.String(120), nullable=True))
    op.add_column("recurring", sa.Column("decision", sa.String(16), nullable=True))
    op.add_column("recurring", sa.Column("kind", sa.String(16), nullable=True))
    op.add_column("recurring", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                                         nullable=False))
    op.create_check_constraint(op.f("ck_recurring_known_decision"), "recurring",
                               "decision IS NULL OR decision IN ('confirmed', 'dismissed')")
    op.create_check_constraint(op.f("ck_recurring_known_kind"), "recurring",
                               "kind IS NULL OR kind IN ('subscription', 'bill', 'invest', 'other')")
    op.create_unique_constraint(op.f("uq_recurring_member_id_payee_key"), "recurring", ["member_id", "payee_key"])
    op.add_column("statement", sa.Column("total_due", sa.Numeric(14, 2), nullable=True))
    op.add_column("statement", sa.Column("due_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("statement", "due_date")
    op.drop_column("statement", "total_due")
    op.drop_constraint(op.f("uq_recurring_member_id_payee_key"), "recurring", type_="unique")
    op.drop_constraint(op.f("ck_recurring_known_kind"), "recurring", type_="check")
    op.drop_constraint(op.f("ck_recurring_known_decision"), "recurring", type_="check")
    for col in ("updated_at", "kind", "decision", "payee_key"):
        op.drop_column("recurring", col)
