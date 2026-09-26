"""Daily mutual fund NAVs from AMFI (PLAN §7.7), kept only for ISINs someone holds.

One HTTPS GET of portal.amfiindia.com's NAVAll.txt (≈1.5 MB, every scheme's latest NAV) per day, run
by the collector; skipped when the newest stored AMFI price is from today or yesterday (NAVs publish
after market close). Rows are `code;ISIN growth;ISIN reinvest;name;NAV;DD-Mon-YYYY`.
"""

import logging
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from tijori.db import MemberContext, member_session
from tijori.models import Holding, Price

log = logging.getLogger("tijori.prices")
AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
TIMEOUT_S = 30


def _held(engine: Engine, members: list[MemberContext]) -> set[str]:
    isins: set[str] = set()
    for ctx in members:
        with member_session(engine, ctx) as s:
            isins |= {i for i in s.scalars(select(Holding.isin).where(Holding.member_id == ctx.member_id,
                                                                      Holding.isin.is_not(None)).distinct())}
    return isins


def parse_navall(text: str, wanted: set[str]) -> list[tuple[str, date, Decimal]]:
    out = []
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 6:
            continue
        try:
            nav, day = Decimal(parts[4]), datetime.strptime(parts[5].strip(), "%d-%b-%Y").date()
        except (InvalidOperation, ValueError):
            continue
        for isin in (parts[1].strip(), parts[2].strip()):
            if isin in wanted:
                out.append((isin, day, nav))
    return out


def refresh_navs(engine: Engine, members: list[MemberContext], today: date | None = None) -> int:
    wanted = _held(engine, members)
    if not wanted or not members:
        return 0
    today = today or date.today()
    with member_session(engine, members[0]) as s:
        newest = s.scalar(select(func.max(Price.date)).where(Price.source == "amfi"))
    if newest and newest >= today - timedelta(days=1):
        return 0
    req = urllib.request.Request(AMFI_URL, headers={"User-Agent": "tijori/1 (personal finance; daily NAV)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (fixed https URL)
        text = r.read(8_000_000).decode("utf-8", errors="replace")
    rows = parse_navall(text, wanted)
    with member_session(engine, members[0]) as s:
        for isin, day, nav in rows:
            s.execute(pg_insert(Price).values(isin_or_symbol=isin, date=day, close=nav, source="amfi").on_conflict_do_nothing())
        s.commit()
    log.info("NAVs stored: %s of %s held schemes", len(rows), len(wanted))
    return len(rows)
