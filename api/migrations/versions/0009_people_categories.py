"""People categories count both ways.

- category.credit_bucket: the bucket a credit takes when it differs from `bucket`. Family, Friends and
  Social circle get "income": money sent counts as spend, money received as income.
- Friends and Social circle join the defaults for every household, next to Family.
- Their existing txns are re-bucketed by direction, except ones a link, a split or a
  not-in-statement flag has taken out of the totals.

Expand-only (docs/deploy.md).

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUCKETS = ("everyday", "oneoff", "card", "invest", "income", "excluded")
PEOPLE = {
    "Family": "Money to and from family: sent counts as spend, received as income.",
    "Friends": "Money to and from friends: sent counts as spend, received as income.",
    "Social circle": "People you know who aren't friends or family: sent counts as spend, received as income.",
}


def upgrade() -> None:
    op.add_column("category", sa.Column("credit_bucket", sa.String(24), nullable=True))
    op.create_check_constraint(op.f("ck_category_category_credit_bucket"), "category",
                               "credit_bucket IN (" + ", ".join(f"'{b}'" for b in BUCKETS) + ")")
    conn = op.get_bind()
    for name, description in PEOPLE.items():
        conn.execute(sa.text("""
            INSERT INTO category (household_id, member_id, name, description, kind, bucket, credit_bucket, sort_order)
            SELECT f.household_id, NULL, :name, :description, 'spend', 'oneoff', 'income', f.sort_order
            FROM category f WHERE f.name = 'Family' AND f.member_id IS NULL
            ON CONFLICT DO NOTHING"""), {"name": name, "description": description})
        conn.execute(sa.text("""
            UPDATE category SET kind = 'spend', bucket = 'oneoff', credit_bucket = 'income', description = :description
            WHERE name = :name AND member_id IS NULL"""), {"name": name, "description": description})
    conn.execute(sa.text("""
        UPDATE txn t SET bucket = CASE WHEN t.direction = 'credit' THEN 'income' ELSE 'oneoff' END,
                         kind = CASE WHEN t.direction = 'credit' THEN 'income' ELSE 'spend' END
        FROM category c
        WHERE c.id = t.category_id AND c.member_id IS NULL AND c.name IN ('Family', 'Friends', 'Social circle')
          AND t.status IS DISTINCT FROM 'flagged'
          AND NOT EXISTS (SELECT 1 FROM txn p WHERE p.split_of = t.id)
          AND NOT EXISTS (SELECT 1 FROM txn_link l WHERE t.id IN (l.a_txn_id, l.b_txn_id))"""))


def downgrade() -> None:
    op.drop_constraint(op.f("ck_category_category_credit_bucket"), "category", type_="check")
    op.drop_column("category", "credit_bucket")
