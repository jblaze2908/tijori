"""Daily prices, kept only for ISINs someone holds: mutual fund NAVs from AMFI, and share and ETF
closes from NSE's end-of-day file (the bhavcopy).

One HTTPS GET of portal.amfiindia.com's NAVAll.txt (≈1.5 MB, every scheme's latest NAV) per day, run
by the collector; skipped when the newest stored AMFI price is from today or yesterday (NAVs publish
after market close), and attempted at most every 6 hours. Rows are
`code;ISIN growth;ISIN reinvest;name;[plan;option;]NAV;DD-Mon-YYYY`: NAV and date are read from the end.
"""

import csv
import io
import logging
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from tijori.db import MemberContext, member_session
from tijori.models import Holding, Price

log = logging.getLogger("tijori.prices")
AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
NSE_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
NSE_READY_IST = (18, 30)  # the day's file is out by then
TIMEOUT_S = 30


def _held(engine: Engine, members: list[MemberContext]) -> set[str]:
    isins: set[str] = set()
    for ctx in members:
        with member_session(engine, ctx) as s:
            isins |= {i for i in s.scalars(select(Holding.isin).where(Holding.member_id == ctx.member_id,
                                                                      Holding.isin.is_not(None)).distinct())}
    return isins


def parse_navall(text: str, wanted: set[str]) -> list[tuple[str, date, Decimal, str]]:
    out = []
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 6:
            continue
        try:
            nav, day = Decimal(parts[-2]), datetime.strptime(parts[-1].strip(), "%d-%b-%Y").date()
        except (InvalidOperation, ValueError):
            continue
        name = " - ".join(p.strip() for p in parts[3:-2] if p.strip())
        for isin in (parts[1].strip(), parts[2].strip()):
            if isin in wanted:
                out.append((isin, day, nav, name))
    return out


_ATTEMPT_S = 6 * 3600
_last_attempt = [-float(_ATTEMPT_S)]


def refresh_navs(engine: Engine, members: list[MemberContext], today: date | None = None) -> int:
    if time.monotonic() - _last_attempt[0] < _ATTEMPT_S:
        return 0
    _last_attempt[0] = time.monotonic()
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
        for isin, day, nav, _ in rows:
            s.execute(pg_insert(Price).values(isin_or_symbol=isin, date=day, close=nav, source="amfi").on_conflict_do_nothing())
        s.commit()
    # Statements wrap scheme names across lines; AMFI's full name replaces the fragment.
    names = {isin: name for isin, _, _, name in rows if name}
    for ctx in members:
        with member_session(engine, ctx) as s:
            for isin, name in names.items():
                s.execute(update(Holding).where(Holding.member_id == ctx.member_id, Holding.isin == isin)
                          .values(name=name[:160]))
            s.commit()
    log.info("NAVs stored: %s of %s held schemes", len(rows), len(wanted))
    return len(rows)


def parse_bhavcopy(text: str, wanted: set[str]) -> list[tuple[str, date, Decimal]]:
    """One close per held ISIN; the EQ series wins when a security trades in several."""
    out: dict[str, tuple[date, Decimal, str]] = {}
    for r in csv.DictReader(io.StringIO(text)):
        isin = (r.get("ISIN") or "").strip()
        if isin not in wanted or (isin in out and out[isin][2] == "EQ"):
            continue
        try:
            out[isin] = (date.fromisoformat(r["TradDt"].strip()), Decimal(r["ClsPric"]), (r.get("SctySrs") or "").strip())
        except (KeyError, ValueError, InvalidOperation):
            continue
    return [(isin, day, close) for isin, (day, close, _) in out.items()]


_last_nse = [-float(_ATTEMPT_S)]


def refresh_closes(engine: Engine, members: list[MemberContext], now: datetime | None = None) -> int:
    """NSE's end-of-day file for the newest trading day not stored yet: one HTTPS GET (≈200 KB zipped), run by
    the collector at most every 6 hours. A weekend or holiday has no file (404), so up to 6 earlier days are
    tried. The request carries only the date."""
    if time.monotonic() - _last_nse[0] < _ATTEMPT_S:
        return 0
    _last_nse[0] = time.monotonic()
    wanted = _held(engine, members)
    if not wanted or not members:
        return 0
    ist = (now or datetime.now(UTC)) + timedelta(hours=5, minutes=30)
    day = ist.date() if (ist.hour, ist.minute) >= NSE_READY_IST else ist.date() - timedelta(days=1)
    with member_session(engine, members[0]) as s:
        newest = s.scalar(select(func.max(Price.date)).where(Price.source == "nse"))
    for _ in range(7):
        if newest and day <= newest:
            return 0
        req = urllib.request.Request(NSE_URL.format(day=day), headers={"User-Agent": "tijori/1.0 (personal finance; daily closing prices)"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (fixed https URL)
                data = r.read(20_000_000)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # no trading that day
                day -= timedelta(days=1)
                continue
            raise
        z = zipfile.ZipFile(io.BytesIO(data))
        rows = parse_bhavcopy(z.read(z.namelist()[0]).decode("utf-8", errors="replace"), wanted)
        with member_session(engine, members[0]) as s:
            for isin, d, close in rows:
                s.execute(pg_insert(Price).values(isin_or_symbol=isin, date=d, close=close, source="nse").on_conflict_do_nothing())
            s.commit()
        log.info("NSE closes stored for %s: %s of %s held ISINs", day, len(rows), len(wanted))
        return len(rows)
    return 0
