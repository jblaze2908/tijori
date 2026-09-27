"""secret.label: the member's name for a statement password, now that an account can hold any number.

Not sealed: a label says which code it is ("SBI Quick code"), never the code. The `extra` slot existed only
for SBI Quick codes, so SBI accounts' extra passwords are labelled that.

Expand-only (docs/deploy.md).

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("secret", sa.Column("label", sa.String(40), nullable=True))
    # A bind, not a literal: text() would read ":extra" as a parameter.
    op.execute(sa.text("""
        UPDATE secret s SET label = 'SBI Quick code'
        FROM account a
        WHERE s.member_id = a.member_id AND a.institution = 'SBI'
          AND s.name = 'statement_password:account:' || a.id || :suffix""").bindparams(suffix=":extra"))


def downgrade() -> None:
    op.drop_column("secret", "label")
