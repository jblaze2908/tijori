"""A "Don't remember" category for payments you can't place.

Counts like the people categories (sent as spend, received as income), so an unknown payment still
moves the totals. Joins the defaults for every household, next to Cash. Payee memory skips it (ingest).

Expand-only (docs/deploy.md).

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(sa.text("""
        INSERT INTO category (household_id, member_id, name, description, kind, bucket, credit_bucket, sort_order)
        SELECT c.household_id, NULL, 'Don''t remember',
               'Payments you can''t place: sent counts as spend, received as income.', 'spend', 'oneoff', 'income',
               c.sort_order
        FROM category c WHERE c.name = 'Cash' AND c.member_id IS NULL
        ON CONFLICT DO NOTHING"""))


def downgrade() -> None:
    pass  # txns filed under it keep it; older code just lists one more category
