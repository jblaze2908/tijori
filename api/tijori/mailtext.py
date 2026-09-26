"""Readable text of an email: the HTML part (banks put the facts there) stripped to lines, else the
plain part. Used by the collector and the raw-message view; never renders markup."""

import html
import re
from email.message import EmailMessage


def body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("html", "plain"))
    if part is None:
        return ""
    try:
        t = part.get_content()
    except (LookupError, UnicodeDecodeError):
        t = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        t = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", t)
        t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = html.unescape(t)
    t = re.sub(r"[ \t ​‌﻿]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()
