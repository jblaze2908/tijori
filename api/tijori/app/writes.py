"""Mutations (docs/api.md). JSON bodies only, audit-logged, inside one member-scoped transaction."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from tijori.app.deps import MemberDep, require_json
from tijori.app.schemas import (
    CategorizeIn,
    CategorizeOut,
    FileInboxIn,
    FileInboxOut,
    RemarkIn,
    RemarkOut,
    SettingsIn,
    SettingsOut,
)
from tijori.services import members, networth, txns
from tijori.services.errors import NotFound

router = APIRouter(prefix="/api", dependencies=[Depends(require_json)])


@router.post("/transactions/{txn_id}/category", response_model=CategorizeOut)
def categorize(db: MemberDep, txn_id: Annotated[int, Path(ge=1)], body: CategorizeIn) -> dict:
    return txns.set_category(db.session, db.ctx, db.actor, txn_id, category_id=body.category_id,
                             category=body.category, scope=body.scope)


# :path because a payee key may contain "/" (keys built from narration text).
@router.post("/inbox/{payee_key:path}/file", response_model=FileInboxOut)
def file_inbox(db: MemberDep, payee_key: Annotated[str, Path(min_length=1, max_length=120)],
               body: FileInboxIn) -> dict:
    remember = body.remember if body.remember is not None else body.scope == "payee"
    return txns.file_inbox(db.session, db.ctx, db.actor, payee_key, category_id=body.category_id,
                           category=body.category, remember=remember, direction=body.direction,
                           txn_ids=body.txn_ids)


_CAP = re.compile(r"^\d{1,6}(\.\d{1,2})?$")
MAX_LOCAL_SHOP_CAP = Decimal("100000")


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(db: MemberDep, body: SettingsIn) -> dict:
    cap = None
    if body.local_shop_cap is not None:
        raw = str(body.local_shop_cap)
        if not _CAP.match(raw) or Decimal(raw) > MAX_LOCAL_SHOP_CAP:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                [{"loc": ["body", "local_shop_cap"], "msg": "must be an amount from 0 to 100000"}])
        cap = Decimal(raw)
    return members.update_settings(db.session, db.ctx, db.actor, month_start_day=body.month_start_day,
                                   local_shop_cap=cap)


@router.patch("/networth/snapshots/{day}", response_model=RemarkOut)
def patch_remark(db: MemberDep, day: date, body: RemarkIn) -> dict:
    out = networth.update_remark(db.session, db.ctx, db.actor, day, body.remark or None)
    if out is None:
        raise NotFound("no snapshot on that date")
    return out
