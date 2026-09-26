"""POST /api/uploads: one bank statement, stored raw, then parse → resolve → classify →
reconcile synchronously. The statement password is used once and never kept."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from tijori.app.auth import Identity
from tijori.app.deps import AuthDep, require_upload_header, run_as_member
from tijori.app.multipart import read_multipart
from tijori.blobs import store_blob
from tijori.db import MemberContext
from tijori.legacy import load_sheet_text
from tijori.parsers import Message, ParseError, route
from tijori.pdf import PdfError, PdfUnavailable, is_pdf, pdf_to_text
from tijori.services import ingest, networth
from tijori.services import secrets as vault

router = APIRouter(prefix="/api", dependencies=[Depends(require_upload_header)])

MAX_PASSWORD = 256
MAX_SHEET_BYTES = 2 * 1024 * 1024


async def _pdf_text(request: Request, identity: Identity, data: bytes, password: str | None) -> str:
    """With no password given, a locked PDF is retried with the member's saved statement passwords
    (most recently saved first). Each attempt is a local qpdf run; nothing is logged."""
    try:
        return await run_in_threadpool(pdf_to_text, data, password)
    except PdfError:
        box = request.app.state.secret_box
        if password is not None or box is None:
            raise
    saved = await run_in_threadpool(run_as_member, request.app.state.engine, identity,
                                    lambda s, ctx, _a: vault.get_many(s, ctx, box, "statement_password:"))
    for candidate in saved:
        try:
            return await run_in_threadpool(pdf_to_text, data, candidate)
        except PdfError:
            continue
    raise PdfError("could not read the PDF; if it is password-protected, send the password or save it for the account")


def _parse(msg: Message) -> tuple[Any, str | None]:
    """(statement, None), (None, reason) on a layout error, or (None, None) when no parser matches."""
    parser = route(msg)
    if parser is None or not hasattr(parser, "parse_statement"):
        return None, None
    try:
        return parser.parse_statement(msg), None  # type: ignore[attr-defined]
    except ParseError as exc:
        return None, str(exc)


@router.post("/uploads", status_code=201)
async def upload(request: Request, identity: AuthDep) -> JSONResponse:
    settings = request.app.state.settings
    parts = await read_multipart(request, settings.max_upload_bytes + 64 * 1024)
    upload = parts.get("file")
    if upload is None or not upload.data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            [{"loc": ["body", "file"], "msg": "required (statement PDF or pdftotext text)"}])
    if len(upload.data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
    password = None
    if "password" in parts:
        try:
            password = parts["password"].data.decode("utf-8") or None
        except UnicodeDecodeError:
            password = None
        if password is not None and len(password) > MAX_PASSWORD:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                [{"loc": ["body", "password"], "msg": "too long"}])
    filename = (upload.filename or "upload")[:200]
    data = upload.data

    if is_pdf(data):
        try:
            text = await _pdf_text(request, identity, data, password)
        except PdfUnavailable as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PDF support is not installed") from exc
        except PdfError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                                "expected a PDF or UTF-8 text from pdftotext -layout") from exc
    del password  # never stored, never logged
    statement, parse_error = await run_in_threadpool(_parse, Message(text=text, filename=filename))

    def work(s: Session, ctx: MemberContext, actor: str) -> tuple[int, dict[str, Any]]:
        digest, blob_ref = store_blob(settings.blob_dir, ctx.member_id, data)
        dup = ingest.find_duplicate(s, ctx.member_id, digest)
        if dup is not None:
            payload: dict[str, Any] = {"duplicate": True, **dup}
            if statement is not None:
                payload["reconciliation"] = ingest.reconciliation_out(statement)
            return 200, payload
        if statement is None:
            parse_status = "failed" if parse_error else "parser_needed"
            msg, _ = ingest.record_raw(s, ctx, filename=filename, sha256=digest, blob_ref=blob_ref,
                                       parse_status=parse_status)
            return 422, {"detail": parse_error or "no parser recognises this document",
                         "raw_message_id": msg.id, "parse_status": parse_status}
        return 201, ingest.ingest_statement(s, ctx, actor, statement, filename=filename,
                                            sha256=digest, blob_ref=blob_ref)

    code, payload = await run_in_threadpool(run_as_member, request.app.state.engine, identity, work)
    return JSONResponse(jsonable_encoder(payload), status_code=code)


@router.post("/networth/import")
async def import_sheet(request: Request, identity: AuthDep) -> JSONResponse:
    """The net-worth Google Sheet as CSV: idempotent upsert by month."""
    parts = await read_multipart(request, MAX_SHEET_BYTES)
    sheet = parts.get("sheet")
    if sheet is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            [{"loc": ["body", "sheet"], "msg": "required (CSV file)"}])
    try:
        rows = load_sheet_text(sheet.data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, KeyError, StopIteration) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "not a net-worth sheet export (needs a 'Month' header row)") from exc
    n = await run_in_threadpool(run_as_member, request.app.state.engine, identity,
                                lambda s, ctx, _actor: networth.upsert_sheet(s, ctx.member_id, rows))
    return JSONResponse({"snapshots_upserted": n})
