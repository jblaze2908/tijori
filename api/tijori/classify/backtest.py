"""classify_backtest: replay history in time order, learning only from the past.

Rolling mode: month M is classified with payee memory built from labels of months < M, as if
the member confirmed every earlier month. Fixed mode (`train_until`): memory comes only from
months <= train_until and the later months are scored, matching the 2026-09-26 manual baseline.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from tijori.classify.engine import Classifier, Decision, Identity, Rule, TxnInput
from tijori.classify.kinds import MemberProfile
from tijori.classify.memory import MIN_AGREEING, PayeeMemory, memory_key


@dataclass(frozen=True, slots=True)
class LabeledTxn:
    txn: TxnInput
    label: str  # taxonomy category the member confirmed

    @property
    def month(self) -> str:
        return self.txn.occurred_at.strftime("%Y-%m")


@dataclass(slots=True)
class Tally:
    n: int = 0
    filed: int = 0
    correct: int = 0
    inbox: int = 0
    debits: int = 0
    debit_inbox: int = 0
    by_source: Counter[str] = field(default_factory=Counter)
    correct_by_source: Counter[str] = field(default_factory=Counter)
    inbox_reasons: Counter[str] = field(default_factory=Counter)

    def add(self, item: LabeledTxn, d: Decision) -> None:
        self.n += 1
        is_debit = item.txn.direction == "debit"
        self.debits += is_debit
        if d.filed:
            src = d.classified_by or "?"
            self.filed += 1
            self.by_source[src] += 1
            if d.category == item.label:
                self.correct += 1
                self.correct_by_source[src] += 1
        else:
            self.inbox += 1
            self.debit_inbox += is_debit
            self.inbox_reasons[d.inbox_reason or "?"] += 1

    @property
    def auto_rate(self) -> float:
        return self.filed / self.n if self.n else 0.0

    @property
    def precision(self) -> float:
        return self.correct / self.filed if self.filed else 0.0


@dataclass(slots=True)
class BaselineStats:
    """Comparable to the manual 2026-09-26 count: test-period debits whose payee was never seen
    in training, and how many of those the brand dictionary covers."""

    test_debits: int = 0
    unseen_payee: int = 0
    unseen_with_brand: int = 0


@dataclass(slots=True)
class BacktestReport:
    mode: str
    months: dict[str, Tally]
    overall: Tally
    misses: list[tuple[str, LabeledTxn, Decision]]
    baseline: BaselineStats | None = None


def run_backtest(
    items: Sequence[LabeledTxn],
    profile: MemberProfile,
    *,
    train_until: str | None = None,
    member_rules: Iterable[Rule] = (),
    household_rules: Iterable[Rule] = (),
    min_agreeing: int = MIN_AGREEING,
) -> BacktestReport:
    ordered = sorted(items, key=lambda i: (i.txn.occurred_at, i.txn.account_id or 0))
    idents: list[Identity] = [Classifier.identify(i.txn) for i in ordered]  # memory-independent
    by_month: dict[str, list[int]] = defaultdict(list)
    for idx, item in enumerate(ordered):
        by_month[item.month].append(idx)
    member_rules, household_rules = tuple(member_rules), tuple(household_rules)

    months: dict[str, Tally] = {}
    overall = Tally()
    misses: list[tuple[str, LabeledTxn, Decision]] = []
    for month in sorted(by_month):
        if train_until and month <= train_until:
            continue
        cutoff = train_until or month
        seen = (i for i, it in enumerate(ordered) if (it.month <= cutoff if train_until else it.month < cutoff))
        memory = PayeeMemory.from_pairs(
            ((memory_key(ordered[i].txn.direction, idents[i].payee_key), ordered[i].label) for i in seen),
            min_agreeing,
        )
        clf = Classifier(profile, member_rules, household_rules, memory)
        idx = by_month[month]
        decisions = clf.classify_batch([ordered[i].txn for i in idx])
        tally = months.setdefault(month, Tally())
        for i, d in zip(idx, decisions):
            tally.add(ordered[i], d)
            overall.add(ordered[i], d)
            if d.filed and d.category != ordered[i].label:
                misses.append((month, ordered[i], d))

    baseline = None
    if train_until:
        baseline = BaselineStats()
        train_merchants = {idents[i].merchant for i, it in enumerate(ordered) if it.month <= train_until}
        for i, it in enumerate(ordered):
            if it.month > train_until and it.txn.direction == "debit":
                baseline.test_debits += 1
                if idents[i].merchant not in train_merchants:
                    baseline.unseen_payee += 1
                    baseline.unseen_with_brand += idents[i].brand is not None
    mode = f"fixed (train <= {train_until})" if train_until else "rolling"
    return BacktestReport(mode, months, overall, misses, baseline)


def format_report(report: BacktestReport, show_misses: int = 0) -> str:
    lines = [f"classify_backtest — mode: {report.mode}", ""]
    hdr = f"{'month':<8} {'txns':>5} {'filed':>6} {'auto%':>6} {'prec%':>6} {'inbox':>6} {'debit inbox':>12}"
    lines += [hdr, "-" * len(hdr)]

    def row(label: str, t: Tally) -> str:
        return (f"{label:<8} {t.n:>5} {t.filed:>6} {100 * t.auto_rate:>6.1f} {100 * t.precision:>6.1f} "
                f"{t.inbox:>6} {t.debit_inbox:>12}")

    for month, t in report.months.items():
        lines.append(row(month, t))
    lines.append("-" * len(hdr))
    lines.append(row("overall", report.overall))
    n_months = len(report.months) or 1
    lines.append(f"inbox items per month (mean): {report.overall.inbox / n_months:.1f}")
    lines.append("")
    lines.append("filed by source (filed / correct):")
    for src, n in report.overall.by_source.most_common():
        lines.append(f"  {src:<13} {n:>4} / {report.overall.correct_by_source[src]:>4}")
    lines.append("inbox reasons: " + ", ".join(f"{k}={v}" for k, v in report.overall.inbox_reasons.most_common()))
    if report.baseline:
        b = report.baseline
        lines.append("")
        lines.append(f"baseline check: {b.unseen_payee} of {b.test_debits} test debits have a payee unseen in "
                     f"training; the brand dictionary covers {b.unseen_with_brand} of them")
    if show_misses:
        lines.append("")
        lines.append(f"first {show_misses} misfiles (label → decision):")
        for month, item, d in report.misses[:show_misses]:
            lines.append(f"  {month} {item.txn.direction:<6} {item.txn.amount:>10} {d.merchant[:28]:<28} "
                         f"{item.label} → {d.category} [{d.rule_id}]")
    return "\n".join(lines)
