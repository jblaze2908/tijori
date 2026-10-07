"""merchant_order.category and merchant_order_item.category: an agent's label for the order and for each item.

Free text set by whoever records the order (Zomato: the whole order is eating out; Blinkit: per item, e.g. "Snacks &
biscuits"). It never changes a txn's category or totals; it groups and filters items.

Expand-only (docs/deploy.md).

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("merchant_order", sa.Column("category", sa.String(60), nullable=True))
    op.add_column("merchant_order_item", sa.Column("category", sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column("merchant_order_item", "category")
    op.drop_column("merchant_order", "category")
