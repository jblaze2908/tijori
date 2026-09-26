"""PDF to `pdftotext -layout` text via OS tools only (poppler-utils, qpdf).

The statement password goes to qpdf on stdin: never argv (visible in `ps`), never logs,
never the database. Decrypted copies live only in a private temp dir removed on exit.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT_S = 60


class PdfError(ValueError):
    """The PDF could not be read; the message is safe to show the member."""


class PdfUnavailable(RuntimeError):
    """A required OS tool is missing from this image."""


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def pdf_to_text(data: bytes, password: str | None = None) -> str:
    if shutil.which("pdftotext") is None:
        raise PdfUnavailable("pdftotext is not installed")
    with tempfile.TemporaryDirectory(prefix="tijori-pdf-") as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(data)
        target = src
        if password:
            if shutil.which("qpdf") is None:
                raise PdfUnavailable("qpdf is not installed")
            target = Path(tmp) / "plain.pdf"
            done = subprocess.run(
                ["qpdf", "--password-file=-", "--decrypt", str(src), str(target)],
                input=password.encode(), capture_output=True, timeout=TIMEOUT_S, check=False,
            )
            # 3 = succeeded with warnings.
            if done.returncode not in (0, 3):
                raise PdfError("could not decrypt the PDF; check the password")
        done = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(target), "-"],
            capture_output=True, timeout=TIMEOUT_S, check=False,
        )
        if done.returncode != 0:
            raise PdfError("could not read the PDF; if it is password-protected, send the password")
        return done.stdout.decode("utf-8", errors="replace")
