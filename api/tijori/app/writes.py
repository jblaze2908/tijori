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
    ComponentIn,
    ComponentOut,
    RulePatch,
    RuleState,
    TxnNotes,
    TxnPatch,
    UnfileIn,
    UnfileOut,
    FileInboxIn,
    FileInboxOut,
    RemarkIn,
    RemarkOut,
    SettingsIn,
    SettingsOut,
)
from tijori.services import members, networth, txns
from tijori.services.common import today_ist
from tijori.services.networth import COMPONENT_KEYS
from tijori.services.errors import NotFound

router = APIRouter(prefix="/api", dependencies=[Depends(require_json)])


@router.post("/transactions/{txn_id}/category", response_model=CategorizeOut)
def categorize(db: MemberDep, txn_id: Annotated[int, Path(ge=1)], body: CategorizeIn) -> dict:
    return txns.set_category(db.session, db.ctx, db.actor, txn_id, category_id=body.category_id,
                             category=body.category, scope=body.scope)


@router.patch("/transactions/{txn_id}", response_model=TxnNotes)
def patch_txn(db: MemberDep, txn_id: Annotated[int, Path(ge=1)], body: TxnPatch) -> dict:
    fields = body.model_fields_set
    if not fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [{"loc": ["body"], "msg": "nothing to update"}])
    return txns.update_txn(db.session, db.ctx, db.actor, txn_id, notes=body.notes, tags=body.tags, fields=fields)


@router.post("/inbox/undo", response_model=UnfileOut)
def undo_filing(db: MemberDep, body: UnfileIn) -> dict:
    return txns.unfile(db.session, db.ctx, db.actor, body.txn_ids, body.rule_id)


@router.patch("/rules/{rule_id}", response_model=RuleState)
def patch_rule(db: MemberDep, rule_id: Annotated[str, Path(pattern=r"^rule:\d{1,18}$")], body: RulePatch) -> dict:
    return txns.set_rule_enabled(db.session, db.ctx, db.actor, int(rule_id[5:]), body.enabled)


_AMOUNT = re.compile(r"^\d{1,12}(\.\d{1,2})?$")


@router.put("/networth/components/{key}", response_model=ComponentOut)
def put_component(db: MemberDep, key: str, body: ComponentIn) -> dict:
    if key not in COMPONENT_KEYS:
        raise NotFound("unknown component")
    raw = str(body.amount)
    if not _AMOUNT.match(raw):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            [{"loc": ["body", "amount"], "msg": "must be an amount ≥ 0 with up to 2 decimals"}])
    as_of = body.as_of or today_ist()
    if as_of > today_ist():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [{"loc": ["body", "as_of"], "msg": "must not be in the future"}])
    return networth.set_component(db.session, db.ctx, db.actor, key, Decimal(raw), as_of)


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
