"""Split a bank narration into payee, UPI handle, masked account and remark.

Handles SBI (`UPI/DR/<rrn>/<name>/<bank>/<vpa>/<remark>`, `NEFT*ifsc*utr*name`, `IMPS/<rrn>/...`)
and HDFC (`UPI-<name>-<vpa>-<ifsc>-<rrn>-<remark>`, `NEFT DR-ifsc-name-...`, `IMPS-<rrn>-name-...`).
"""

import re
from dataclasses import dataclass

# SBI keeps only the first 10 characters of a UPI handle; comparing on that prefix lets one
# payee match across banks.
VPA_KEY_LEN = 10

_SBI_UPI = re.compile(r"UPI/(DR|CR)/(\d{6,})/(.*)$", re.I)
_HDFC_UPI = re.compile(r"^(?:REV-)?UPI-(.*)$", re.I)
_VPA_HEAD = re.compile(r"^([A-Za-z0-9._\-]+@[A-Za-z0-9]+)(.*)$")
_SBI_NEFT = re.compile(r"NEFT\*([A-Z0-9]+)\*([A-Z0-9 ]+)\*([^*]+)(?:\*(.*))?$", re.I)
_HDFC_NEFT = re.compile(r"^NEFT (?:DR|CR)-([A-Z0-9]+)-([^-]+)-?(.*)$", re.I)
_SBI_IMPS = re.compile(r"IMPS/(\d{6,})/([^/]*)/?(.*)$", re.I)
_HDFC_IMPS = re.compile(r"^IMPS-(\d{6,})-([^-]+)-(.*)$", re.I)
_NACH = re.compile(r"\bNACH\d*\s+(.+)$|\bACH [CD]-\s*([^-]+)", re.I)
_MASKED = re.compile(r"X{2,}(\d{3,6})\b", re.I)
_CASH = re.compile(r"SELF\s*-?\s*CHQ|CASH\s*WDL|ATM\s*WDL|\bATW\b|\bNFS\b|CASH WITHDRAWAL", re.I)


@dataclass(frozen=True, slots=True)
class Narration:
    raw: str
    channel: str  # upi | neft | imps | rtgs | nach | cash | interest | other
    payee: str | None = None
    vpa: str | None = None
    masked_account: str | None = None
    remark: str | None = None
    rrn: str | None = None

    @property
    def vpa_key(self) -> str | None:
        return self.vpa[:VPA_KEY_LEN] if self.vpa else None

    @property
    def vpa_handle(self) -> str | None:
        """Local part before '@' (the whole value when a bank truncated the domain away)."""
        return self.vpa.split("@", 1)[0] if self.vpa else None


def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip(" -/*")
    return s or None


def _masked(text: str) -> str | None:
    m = _MASKED.search(text)
    return m.group(1) if m else None


def parse_narration(raw: str) -> Narration:
    text = re.sub(r"\s+", " ", raw).strip()

    if m := _SBI_UPI.search(text):
        fields = m.group(3).split("/")
        name = _clean(fields[0])
        vpa = _clean(fields[2]).lower() if len(fields) > 2 and _clean(fields[2]) else None
        return Narration(
            raw=text,
            channel="upi",
            payee=name,
            vpa=vpa,
            masked_account=_masked(name or ""),
            remark=_clean("/".join(fields[3:])) if len(fields) > 3 else None,
            rrn=m.group(2),
        )

    if m := _HDFC_UPI.match(text):
        rest = m.group(1)
        if "@" in rest and "-" in rest:
            name, after = rest.split("-", 1)
            if v := _VPA_HEAD.match(after):
                tail = [p for p in v.group(2).split("-") if p]
                rrn = next((p for p in tail if p.isdigit() and len(p) >= 9), None)
                remark = tail[-1] if tail and not tail[-1].isdigit() else None
                payee = _clean(name)
                return Narration(
                    raw=text, channel="upi", payee=None if payee and payee.isdigit() else payee,
                    vpa=v.group(1).lower(), remark=_clean(remark), rrn=rrn,
                )
        fields = rest.split("-")
        head = _clean(fields[0])
        masked = _masked(head or "")
        rrn = next((p for p in fields if p.isdigit() and len(p) >= 9), None)
        return Narration(
            raw=text, channel="upi", payee=None if masked else head, masked_account=masked, rrn=rrn,
            remark=_clean(fields[-1]) if len(fields) > 1 else None,
        )

    if m := _SBI_NEFT.search(text):
        return Narration(raw=text, channel="neft", payee=_clean(m.group(3)), remark=_clean(m.group(4)))
    if m := _HDFC_NEFT.match(text):
        return Narration(raw=text, channel="neft", payee=_clean(m.group(2)), remark=_clean(m.group(3)),
                         masked_account=_masked(text))

    if m := _SBI_IMPS.search(text):
        # Middle field is "<bank>-<XXnnn>-<name>"; the name is the last dash-part.
        parts = [p for p in (x.strip() for x in m.group(2).split("-")) if p]
        return Narration(
            raw=text, channel="imps", payee=_clean(parts[-1]) if parts else None,
            masked_account=_masked(m.group(2)), remark=_clean(m.group(3)), rrn=m.group(1),
        )
    if m := _HDFC_IMPS.match(text):
        return Narration(raw=text, channel="imps", payee=_clean(m.group(2)), masked_account=_masked(m.group(3)),
                         remark=_clean(m.group(3).split("-")[-1]), rrn=m.group(1))

    if "RTGS" in text.upper():
        return Narration(raw=text, channel="rtgs")
    if m := _NACH.search(text):
        return Narration(raw=text, channel="nach", payee=_clean(m.group(1) or m.group(2)))
    if _CASH.search(text):
        return Narration(raw=text, channel="cash")
    if re.search(r"\bINTEREST\b", text, re.I):
        return Narration(raw=text, channel="interest")
    return Narration(raw=text, channel="other", masked_account=_masked(text))
