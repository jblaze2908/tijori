"""Read-only IMAP connection test (stdlib imaplib over TLS). Results are fixed error codes: server
banners, exception text and the password never leave this module."""

import imaplib
import logging
import ipaddress
import socket
import ssl
from dataclasses import dataclass

PRESETS: dict[str, tuple[str, int]] = {
    "gmail": ("imap.gmail.com", 993),
    "outlook": ("outlook.office365.com", 993),
    "yahoo": ("imap.mail.yahoo.com", 993),
}
TIMEOUT_S = 10


@dataclass(frozen=True, slots=True)
class ImapResult:
    ok: bool
    message_count: int | None
    error_code: str | None


def quote_mailbox(label: str) -> str:
    """IMAP quoted string; callers only pass printable ASCII without CR/LF."""
    return '"' + label.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _public_only(host: str, port: int) -> None:
    """Refuse private, loopback and link-local targets: a member-supplied host must not let the
    server probe its own network. Costs one DNS lookup per test (the endpoint is rate-limited)."""
    for *_, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(sockaddr[0]).is_global:
            raise PermissionError("host_not_public")


# Gmail's LOGIN refusals say why; the reply never echoes the password, only the reason.
_LOGIN_REASONS: tuple[tuple[str, str], ...] = (
    ("application-specific password required", "app_password_required"),
    ("not enabled for imap", "imap_disabled"),
    ("web login required", "web_login_required"),
    ("webalert", "web_login_required"),
    ("too many", "rate_limited"),
    ("invalid credentials", "auth_failed"),
    ("authenticationfailed", "auth_failed"),
)
log = logging.getLogger("tijori.imap")


def _login_error_code(reply: str) -> str:
    low = reply.lower()
    code = next((c for needle, c in _LOGIN_REASONS if needle in low), "auth_failed")
    log.info("imap login refused: %s", code)
    return code


def normalize_password(host: str, password: str) -> str:
    """Gmail shows App Passwords as 'abcd efgh ijkl mnop' but they never contain spaces; a pasted
    copy with the spaces fails LOGIN."""
    return "".join(password.split()) if host == PRESETS["gmail"][0] else password


def check(host: str, port: int, username: str, password: str, label: str) -> ImapResult:
    password = normalize_password(host, password)
    try:
        _public_only(host, port)
        with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_S) as conn:
            try:
                conn.login(username, password)
            except imaplib.IMAP4.error as exc:
                return ImapResult(False, None, _login_error_code(str(exc)))
            typ, data = conn.select(quote_mailbox(label), readonly=True)  # EXAMINE: never marks mail read
            if typ != "OK":
                return ImapResult(False, None, "mailbox_not_found")
            try:
                return ImapResult(True, int(data[0]), None)
            except (TypeError, ValueError, IndexError):
                return ImapResult(True, None, None)
    except PermissionError:
        return ImapResult(False, None, "host_not_public")
    except socket.gaierror:
        return ImapResult(False, None, "dns_failed")
    except ssl.SSLError:
        return ImapResult(False, None, "tls_failed")
    except (TimeoutError, socket.timeout):
        return ImapResult(False, None, "timeout")
    except ConnectionRefusedError:
        return ImapResult(False, None, "connect_refused")
    except imaplib.IMAP4.error:
        return ImapResult(False, None, "protocol_error")
    except OSError:
        return ImapResult(False, None, "connect_failed")
