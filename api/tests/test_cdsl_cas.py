"""CAS lines land in the component the statement's totals put them in, without a database."""

import unittest
from decimal import Decimal

from tijori.parsers.base import Message
from tijori.parsers.cdsl_cas import CdslCasParser

# Synthetic layout: an ETF's name row reads as its fund house; "ETF" is only on the wrapped line below.
CAS = """CDSL Consolidated Account Statement
Your Total Portfolio Value as on 31-10-2026
ISIN              Security
                  EXAMPLE LIMITED
INE000X01011      EQUITY SHARES          50.000        --        --        --        50.000      250.00      12,500.00
                  EXAMPLE LIFE INDIA AM
INF000X01011      LTD#EXAMPLE MF-EXAMPLE           100.000        --        --        --       100.000      250.00      25,000.00
                  INDIA ETF NIFTY 50 BEES
Total Portfolio Value      37,500.00
"""


class EtfNamedByItsFundHouse(unittest.TestCase):
    """Incident: a CAS counted a demat ETF in stocks while its holding was filed under mutual funds, because the
    account was picked from the name above the row, which named only the fund house."""

    def test_the_etf_line_and_the_totals_agree_on_stocks(self) -> None:
        cas = CdslCasParser().parse(Message(text=CAS))
        self.assertEqual({h.isin: h.bucket for h in cas.lines}, {"INE000X01011": "stocks", "INF000X01011": "stocks"})
        self.assertEqual(dict(cas.components), {"stocks": Decimal("37500.00"), "mf": Decimal("0")})


if __name__ == "__main__":
    unittest.main()
