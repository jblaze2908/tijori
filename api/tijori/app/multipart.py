"""Bounded multipart/form-data reading with the stdlib email parser (no python-multipart)."""

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from fastapi import HTTPException, Request, status


@dataclass(frozen=True, slots=True)
class Part:
    filename: str | None
    data: bytes


async def read_capped(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"body larger than {cap} bytes")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"body larger than {cap} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_multipart(request: Request, cap: int) -> dict[str, Part]:
    """First occurrence of each field wins; non form-data parts are ignored."""
    ctype = request.headers.get("content-type", "")
    if ctype.split(";", 1)[0].strip().lower() != "multipart/form-data" or "boundary=" not in ctype:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type must be multipart/form-data")
    body = await read_capped(request, cap)
    head = b"MIME-Version: 1.0\r\nContent-Type: " + ctype.encode("latin-1") + b"\r\n\r\n"
    msg = BytesParser(policy=policy.HTTP).parsebytes(head + body)
    if not msg.is_multipart():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed multipart body")
    parts: dict[str, Part] = {}
    for part in msg.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if isinstance(name, str) and name not in parts:
            parts[name] = Part(part.get_filename(), part.get_payload(decode=True) or b"")
    return parts
