"""Net worth by account: every place money sits is an account, valued from its own evidence. New kinds (retirement,
gold, property, loan, other asset or liability), a subtype preset, the member's overrides (liquid, asset class, in
net worth), and account_valuation for dated values set by hand or printed by a statement.

Values stored under the old fixed component keys (component_value, and the snapshot table's monthly components) move
onto accounts: a bank key onto that bank's account, the rest onto an account of the matching kind, made if missing.
Holdings get the account they sit in. Expand-only (docs/deploy.md): component_value and snapshot stay for the
previous release.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = ("bank", "card", "wallet", "deposit", "holding", "cash")
KINDS = (*OLD_KINDS, "retirement", "gold", "property", "loan", "other_asset", "other_liability")
# The old fixed component keys: (kind, subtype, institution and name of an account made when none fits). The two bank
# keys were the institutions the old model knew by name; they map onto the member's own account at that bank.
OLD_KEYS = {
    "sbi": ("bank", None, "SBI", None), "hdfc": ("bank", None, "HDFC", None),
    "fd": ("deposit", "fd", "Deposits", "Fixed deposits"), "ppf": ("retirement", "ppf", "PPF", None),
    "epf": ("retirement", "epf", "EPF", None), "gold": ("gold", None, "Gold", None),
    "other": ("other_asset", None, "Other", None), "stocks": ("holding", "stocks", "CDSL", "CDSL Stocks & ETFs"),
    "mf": ("holding", "mf", "CDSL", "CDSL Mutual funds"),
}


def _kinds_check(kinds: tuple[str, ...], valid: bool = True) -> None:
    op.execute("ALTER TABLE account DROP CONSTRAINT IF EXISTS ck_account_account_kind")
    op.execute(f"ALTER TABLE account ADD CONSTRAINT ck_account_account_kind CHECK (kind IN ({', '.join(repr(k) for k in kinds)}))"
               + ("" if valid else " NOT VALID"))


def upgrade() -> None:
    _kinds_check(KINDS)
    op.add_column("account", sa.Column("subtype", sa.String(24), nullable=True))
    op.add_column("account", sa.Column("liquid", sa.Boolean(), nullable=True))
    op.add_column("account", sa.Column("asset_class", sa.String(16), nullable=True))
    op.add_column("account", sa.Column("in_net_worth", sa.Boolean(), server_default=sa.text("true"), nullable=False))

    op.create_table(
        "account_valuation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.Enum("manual", "statement", "imported", name="valuation_source", native_enum=False,
                                    create_constraint=True, length=24), server_default="manual", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], name=op.f("fk_account_valuation_member_id_member"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], name=op.f("fk_account_valuation_account_id_account"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_valuation")),
        sa.UniqueConstraint("account_id", "as_of", name=op.f("uq_account_valuation_account_id_as_of")),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_account_valuation_amount_non_negative")),
    )
    op.create_index(op.f("ix_account_valuation_member_id"), "account_valuation", ["member_id"])
    op.create_index(op.f("ix_account_valuation_account_id"), "account_valuation", ["account_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON account_valuation TO tijori_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tijori_app")
    op.execute("ALTER TABLE account_valuation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE account_valuation FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY account_valuation_own ON account_valuation
        USING (member_id = tijori_current_member()) WITH CHECK (member_id = tijori_current_member())""")

    _move_values(op.get_bind())


def _move_values(conn: sa.Connection) -> None:
    """Runs as the owner (a superuser), so RLS doesn't hide rows. Every member's old values, onto accounts."""
    members = conn.execute(sa.text("""
        SELECT member_id FROM component_value UNION SELECT member_id FROM snapshot
        UNION SELECT member_id FROM holding""")).scalars().all()
    for member_id in members:
        made: dict[str, int] = {}

        def account_for(key: str) -> int:
            if key in made:
                return made[key]
            kind, subtype, institution, name = OLD_KEYS[key]
            if kind == "bank":
                found = conn.execute(sa.text("""SELECT id FROM account WHERE member_id = :m AND kind = 'bank'
                    AND upper(institution) = :i ORDER BY id LIMIT 1"""), {"m": member_id, "i": institution}).scalar()
            else:
                ids = conn.execute(sa.text("""SELECT id FROM account WHERE member_id = :m AND kind = :k
                    AND subtype IS NOT DISTINCT FROM :st ORDER BY id"""), {"m": member_id, "k": kind, "st": subtype}).scalars().all()
                found = ids[0] if len(ids) == 1 else None
            if found is None:
                found = conn.execute(sa.text("""INSERT INTO account (member_id, kind, institution, name, subtype)
                    VALUES (:m, :k, :i, :n, :st) RETURNING id"""),
                                     {"m": member_id, "k": kind, "i": institution, "n": name, "st": subtype}).scalar()
            made[key] = found
            return found

        for key, as_of, amount, source in conn.execute(sa.text(
                "SELECT key, as_of, amount, source FROM component_value WHERE member_id = :m ORDER BY as_of"),
                {"m": member_id}).all():
            conn.execute(sa.text("""INSERT INTO account_valuation (member_id, account_id, as_of, amount, source)
                VALUES (:m, :a, :d, :v, :s) ON CONFLICT DO NOTHING"""),
                         {"m": member_id, "a": account_for(key), "d": as_of, "v": amount,
                          "s": "statement" if source == "statement" else "manual"})
        for day, components in conn.execute(sa.text(
                "SELECT date, components_json FROM snapshot WHERE member_id = :m ORDER BY date"), {"m": member_id}).all():
            for key, value in (components or {}).items():
                # A zero under a key with no account of its own (the sheet's blank "Other") makes no account.
                if key in OLD_KEYS and value is not None and (float(value) > 0 or (float(value) == 0 and key in made)):
                    conn.execute(sa.text("""INSERT INTO account_valuation (member_id, account_id, as_of, amount, source)
                        VALUES (:m, :a, :d, :v, 'imported') ON CONFLICT DO NOTHING"""),
                                 {"m": member_id, "a": account_for(key), "d": day, "v": value})
        # Holdings sit in an account: a CAS's demat lines (an INE ISIN or an ETF) and its funds; a CAMS statement's funds.
        for source, equity in conn.execute(sa.text("""
                SELECT DISTINCT source, (left(coalesce(isin, ''), 3) = 'INE' OR name ~* '\\mETF\\M|BEES') AS equity
                FROM holding WHERE member_id = :m AND account_id IS NULL"""), {"m": member_id}).all():
            if source == "cdsl_cas":
                account_id = account_for("stocks" if equity else "mf")
            else:
                key = f"{source}:mf"
                if key not in made:
                    made[key] = conn.execute(sa.text("""INSERT INTO account (member_id, kind, institution, name, subtype)
                        VALUES (:m, 'holding', :i, :n, 'mf') RETURNING id"""),
                                             {"m": member_id, "i": source.upper(), "n": f"{source.upper()} Mutual funds"}).scalar()
                account_id = made[key]
            conn.execute(sa.text("""UPDATE holding SET account_id = :a WHERE member_id = :m AND account_id IS NULL
                AND source = :s AND (left(coalesce(isin, ''), 3) = 'INE' OR name ~* '\\mETF\\M|BEES') = :e"""),
                         {"a": account_id, "m": member_id, "s": source, "e": equity})


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS account_valuation_own ON account_valuation")
    op.drop_table("account_valuation")
    op.drop_column("account", "in_net_worth")
    op.drop_column("account", "asset_class")
    op.drop_column("account", "liquid")
    op.drop_column("account", "subtype")
    _kinds_check(OLD_KINDS, valid=False)  # accounts of the new kinds are kept, unchecked
