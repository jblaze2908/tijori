"""Alert rules that read a bank's mail without a database."""

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from tijori.parsers.alerts import AlertParser, is_informational
from tijori.parsers.base import Message

SENDER = "alerts@hdfcbank.bank.in"
INITIATED = ("Dear Customer, Thank you for banking with HDFC Bank. You have successfully initiated RTGS transaction of "
             "Rs. 500000 from your HDFC Bank A/c XX0001 for a transfer to payee Example Traders using HDFC Bank Online "
             "Banking. Not you? Call the number on your card.")
COMPLETED = ("Dear Customer, Greetings from HDFC Bank! Important Note: Your RTGS transfer has been completed "
             "successfully. We know timely updates matter, and we are happy to confirm this for you. Transaction "
             "Details: Amount: INR 5,00,000.00 Credited to beneficiary A/c ending: XX0002 Date & Time: 27-10-2026 at "
             "10:10:51 Reference Number: HDFCR52026102700000001 Thank you for banking with us.")


class NetBankingTransfer(unittest.TestCase):
    """Incident: an HDFC RTGS made over net banking never appeared; no alert rule read the transfer mails."""

    def test_initiated_is_a_debit_dated_by_arrival_in_ist(self) -> None:
        # 20:00 UTC on the 26th is 01:30 on the 27th in India; the mail itself prints no date.
        msg = Message(text=INITIATED, sender=SENDER, received_at=datetime(2026, 10, 26, 20, 0, tzinfo=UTC))
        self.assertTrue(AlertParser().match(msg))
        (obs,) = AlertParser().parse(msg)
        self.assertEqual(obs.direction, "debit")
        self.assertEqual(obs.amount, Decimal("500000"))
        self.assertEqual(obs.occurred_at, date(2026, 10, 27))
        self.assertEqual(obs.payload["mask"], "0001")
        self.assertIn("RTGS", obs.narration)
        self.assertIn("Example Traders", obs.narration)

    def test_without_an_arrival_time_there_is_no_date_and_no_sighting(self) -> None:
        self.assertEqual(AlertParser().parse(Message(text=INITIATED, sender=SENDER)), [])

    def test_completed_is_recognised_but_not_a_transaction(self) -> None:
        msg = Message(text=COMPLETED, sender=SENDER, received_at=datetime(2026, 10, 27, 4, 41, tzinfo=UTC))
        self.assertFalse(AlertParser().match(msg))
        self.assertTrue(is_informational(msg))
        self.assertFalse(is_informational(Message(text=INITIATED, sender=SENDER)))


if __name__ == "__main__":
    unittest.main()
