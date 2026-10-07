"""merchant_order, merchant_order_item: Blinkit and Zomato orders as the line items behind a txn.

An order points at the debit it was paid with (txn_id), or at a txn built from the receipt on the account that paid
it when no debit did (a meal card the bank never sees); match_state says which. txn.sources gains 'order' for those receipt txns.
Member-owned under RLS like every member table.

Expand-only (docs/deploy.md): the sources check is widened, never narrowed.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SOURCES = ("statement", "alert", "sms", "upload", "expected", "import")
SOURCES = (*OLD_SOURCES, "order")
MATCH_STATES = ("unmatched", "matched", "ambiguous", "assigned", "cancelled")


def _own(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO tijori_app")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY {table}_own ON {table}
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")


def upgrade() -> None:
    op.drop_constraint("ck_txn_known_sources", "txn")
    op.create_check_constraint(
        op.f("ck_txn_known_sources"), "txn", f"sources <@ ARRAY[{', '.join(repr(s) for s in SOURCES)}]::text[]"
    )
    op.create_table(
        "merchant_order",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("order_no", sa.String(64), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("payment", sa.String(80), nullable=True),
        sa.Column("store", sa.String(160), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("address_label", sa.String(40), nullable=True),
        sa.Column("item_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("charges", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("bill_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("txn_id", sa.BigInteger(), nullable=True),
        sa.Column("match_state", sa.String(12), server_default="unmatched", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("bill_total >= 0", name=op.f("ck_merchant_order_bill_non_negative")),
        sa.CheckConstraint(f"match_state IN ({', '.join(repr(s) for s in MATCH_STATES)})",
                           name=op.f("ck_merchant_order_match_state")),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_merchant_order_member_id_member"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["txn_id"], ["txn.id"], name=op.f("fk_merchant_order_txn_id_txn"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_order")),
        sa.UniqueConstraint("member_id", "source", "order_no", name=op.f("uq_merchant_order_member_id_source_order_no")),
    )
    op.create_index(op.f("ix_merchant_order_member_id"), "merchant_order", ["member_id"])
    op.create_index(op.f("ix_merchant_order_member_placed"), "merchant_order", ["member_id", "placed_at"])
    op.create_index(op.f("ix_merchant_order_txn_id"), "merchant_order", ["txn_id"])
    _own("merchant_order")
    op.create_table(
        "merchant_order_item",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(60), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("line_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_merchant_order_item_member_id_member"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["merchant_order.id"], name=op.f("fk_merchant_order_item_order_id_merchant_order"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_order_item")),
    )
    op.create_index(op.f("ix_merchant_order_item_order_id"), "merchant_order_item", ["order_id"])
    op.create_index(op.f("ix_merchant_order_item_member_id"), "merchant_order_item", ["member_id"])
    _own("merchant_order_item")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")


def downgrade() -> None:
    for table in ("merchant_order_item", "merchant_order"):
        op.execute(f"DROP POLICY IF EXISTS {table}_own ON {table}")
        op.drop_table(table)
    op.execute("DELETE FROM txn WHERE sources @> ARRAY['order']::text[]")
    op.drop_constraint("ck_txn_known_sources", "txn")
    op.create_check_constraint(
        op.f("ck_txn_known_sources"), "txn", f"sources <@ ARRAY[{', '.join(repr(s) for s in OLD_SOURCES)}]::text[]"
    )
