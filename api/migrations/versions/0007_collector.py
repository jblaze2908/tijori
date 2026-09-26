"""IMAP collector and split transactions.

- mail_source: UID watermark (uid_validity, last_uid) and last poll outcome.
- raw_message: which mailbox and UID it came from; parse_status gains `needs_password` and `ignored`.
- collector_members(): the members with a working mailbox, for the worker that has no member context
  yet. SECURITY DEFINER like auth_member_by_email; returns ids only.
- txn.split_of: a part of a split txn points at the original.
- component_value.source: "statement" when a statement printed the balance (SBI PPF, HDFC FDs).

Expand-only (docs/deploy.md): nullable columns, a widened check and a new function, so the previous
release keeps working on rollback.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("pending", "parsed", "failed", "parser_needed")
NEW = (*OLD, "needs_password", "ignored")


def _check(values: tuple[str, ...]) -> str:
    return "parse_status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    for name, typ in (("uid_validity", sa.BigInteger()), ("last_uid", sa.BigInteger()),
                      ("last_poll_at", sa.DateTime(timezone=True)), ("last_poll_error", sa.String(40))):
        op.add_column("mail_source", sa.Column(name, typ, nullable=True))
    op.add_column("raw_message", sa.Column("mail_source_id", sa.BigInteger(), nullable=True))
    op.add_column("raw_message", sa.Column("mail_uid", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_raw_message_mail_source_id_mail_source"), "raw_message", "mail_source",
                          ["mail_source_id"], ["id"], ondelete="SET NULL")
    op.drop_constraint(op.f("ck_raw_message_parse_status"), "raw_message", type_="check")
    op.create_check_constraint(op.f("ck_raw_message_parse_status"), "raw_message", _check(NEW))

    op.add_column("component_value", sa.Column("source", sa.String(16), nullable=True))
    op.add_column("txn", sa.Column("split_of", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_txn_split_of_txn"), "txn", "txn", ["split_of"], ["id"], ondelete="CASCADE")
    op.create_index(op.f("ix_txn_split_of"), "txn", ["split_of"])

    op.execute("""
        CREATE FUNCTION collector_members()
        RETURNS TABLE(member_id bigint, household_id bigint)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT DISTINCT m.id, m.household_id FROM member m JOIN mail_source s ON s.member_id = m.id
           WHERE s.status = 'ok' $$""")
    op.execute("REVOKE ALL ON FUNCTION collector_members() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION collector_members() TO tijori_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS collector_members()")
    op.drop_index(op.f("ix_txn_split_of"), table_name="txn")
    op.drop_constraint(op.f("fk_txn_split_of_txn"), "txn", type_="foreignkey")
    op.drop_column("txn", "split_of")
    op.drop_column("component_value", "source")
    op.drop_constraint(op.f("ck_raw_message_parse_status"), "raw_message", type_="check")
    op.create_check_constraint(op.f("ck_raw_message_parse_status"), "raw_message", _check(OLD))
    op.drop_constraint(op.f("fk_raw_message_mail_source_id_mail_source"), "raw_message", type_="foreignkey")
    op.drop_column("raw_message", "mail_uid")
    op.drop_column("raw_message", "mail_source_id")
    for name in ("last_poll_error", "last_poll_at", "last_uid", "uid_validity"):
        op.drop_column("mail_source", name)
