"""Mutations (docs/api.md). JSON bodies only, audit-logged, inside one member-scoped transaction."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from tijori.app.deps import MemberDep, require_json
from tijori.app.schemas import (
    LoanAttachOut,
    LoanDetachOut,
    LoanIn,
    LoanItem,
    LoanPatch,
    LoanTxnsIn,
    LoanWriteOffIn,
    CategorizeIn,
    CategorizeOut,
    ComponentIn,
    ComponentOut,
    BudgetIn,
    BudgetOut,
    McpTokenIn,
    OrdersAssignIn,
    OrdersAssignOut,
    OrdersIn,
    OrdersOut,
    McpTokenOut,
    NotifyTestOut,
    PayeeDismissIn,
    PayeeDismissOut,
    PayeeRenameIn,
    PayeeRenameOut,
    PayeeResetIn,
    PayeeResetOut,
    LinkIn,
    LinkResult,
    RecurringDecision,
    RecurringIn,
    SplitIn,
    SplitOut,
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
from tijori.services import aliases, budgets, loans, mcp_tokens, members, networth, notify, orders, recurring, txn_edit, txns
from tijori.services.common import today_ist
from tijori.services.networth import COMPONENT_KEYS, MANUAL_KEYS
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
    if key not in MANUAL_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            [{"loc": ["path", "key"], "msg": "this comes from statements and daily prices, so it can't be set by hand"}])
    raw = str(body.amount)
    if not _AMOUNT.match(raw):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            [{"loc": ["body", "amount"], "msg": "must be an amount ≥ 0 with up to 2 decimals"}])
    as_of = body.as_of or today_ist()
    if as_of > today_ist():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [{"loc": ["body", "as_of"], "msg": "must not be in the future"}])
    return networth.set_component(db.session, db.ctx, db.actor, key, Decimal(raw), as_of)


# :path because a payee key may contain "/" (keys built from narration text).
@router.post("/mcp/tokens", response_model=McpTokenOut)
def create_mcp_token(db: MemberDep, body: McpTokenIn) -> dict:
    return mcp_tokens.create(db.session, db.ctx, db.actor, body.name.strip())


@router.post("/mcp/tokens/{token_id}/revoke", response_model=McpTokenOut)
def revoke_mcp_token(db: MemberDep, token_id: Annotated[int, Path(ge=1)]) -> dict:
    mcp_tokens.revoke(db.session, db.ctx, db.actor, token_id)
    return next(t for t in mcp_tokens.list_tokens(db.session, db.ctx.member_id) if t["id"] == token_id)


@router.post("/notify/test", response_model=NotifyTestOut)
def notify_test(request: Request, db: MemberDep) -> dict:
    cfg = members.get_settings(db.session, db.ctx.member_id)
    if not cfg["notify_topic"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "set a notification topic first")
    return {"sent": notify.send(request.app.state.settings, cfg["notify_topic"], "Tijori", "Test notification: pushes reach this device.")}


@router.post("/loans", response_model=LoanItem)
def create_loan(db: MemberDep, body: LoanIn) -> dict:
    return loans.create(db.session, db.ctx, db.actor, direction=body.direction, counterparty=body.counterparty,
                        started_on=body.started_on, opening_amount=Decimal(body.opening_amount), note=body.note,
                        txn_ids=body.txn_ids)


@router.patch("/loans/{loan_id}", response_model=LoanItem)
def patch_loan(db: MemberDep, loan_id: Annotated[int, Path(ge=1)], body: LoanPatch) -> dict:
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if "opening_amount" in fields:
        fields["opening_amount"] = Decimal(fields["opening_amount"] or "0")
    return loans.update_loan(db.session, db.ctx, db.actor, loan_id, fields)


@router.post("/loans/{loan_id}/txns", response_model=LoanAttachOut)
def attach_loan_txns(db: MemberDep, loan_id: Annotated[int, Path(ge=1)], body: LoanTxnsIn) -> dict:
    return loans.attach(db.session, db.ctx, db.actor, loan_id, body.txn_ids)


@router.post("/loans/{loan_id}/txns/remove", response_model=LoanDetachOut)
def detach_loan_txns(db: MemberDep, loan_id: Annotated[int, Path(ge=1)], body: LoanTxnsIn) -> dict:
    return loans.detach(db.session, db.ctx, db.actor, loan_id, body.txn_ids)


@router.post("/loans/{loan_id}/settle", response_model=LoanItem)
def settle_loan(db: MemberDep, loan_id: Annotated[int, Path(ge=1)]) -> dict:
    return loans.close(db.session, db.ctx, db.actor, loan_id, "settled")


@router.post("/loans/{loan_id}/reopen", response_model=LoanItem)
def reopen_loan(db: MemberDep, loan_id: Annotated[int, Path(ge=1)]) -> dict:
    return loans.close(db.session, db.ctx, db.actor, loan_id, "open")


@router.post("/loans/{loan_id}/write-off", response_model=LoanItem)
def write_off_loan(db: MemberDep, loan_id: Annotated[int, Path(ge=1)], body: LoanWriteOffIn) -> dict:
    return loans.close(db.session, db.ctx, db.actor, loan_id, "written_off", body.category_id)


@router.put("/budgets/{category_id}", response_model=BudgetOut)
def put_budget(db: MemberDep, category_id: Annotated[int, Path(ge=1)], body: BudgetIn) -> dict:
    amount = Decimal(body.amount) if body.amount is not None else None
    return budgets.set_budget(db.session, db.ctx, db.actor, category_id, amount, body.rollover)


@router.post("/transactions/{txn_id}/split", response_model=SplitOut)
def split_txn(db: MemberDep, txn_id: Annotated[int, Path(ge=1)], body: SplitIn) -> dict:
    return txn_edit.split(db.session, db.ctx, db.actor, txn_id, [p.model_dump() for p in body.parts])


@router.post("/transactions/{txn_id}/unsplit", response_model=SplitOut)
def unsplit_txn(db: MemberDep, txn_id: Annotated[int, Path(ge=1)]) -> dict:
    return txn_edit.unsplit(db.session, db.ctx, db.actor, txn_id)


@router.post("/transactions/{txn_id}/links", response_model=LinkResult)
def link_txn(db: MemberDep, txn_id: Annotated[int, Path(ge=1)], body: LinkIn) -> dict:
    if body.kind == "card_payment":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "card payments are matched automatically")
    return txn_edit.link(db.session, db.ctx, db.actor, txn_id, body.txn_id, body.kind)


@router.post("/transactions/{txn_id}/links/remove", response_model=LinkResult)
def unlink_txn(db: MemberDep, txn_id: Annotated[int, Path(ge=1)], body: LinkIn) -> dict:
    return txn_edit.unlink(db.session, db.ctx, db.actor, txn_id, body.txn_id, body.kind)


@router.put("/recurring/{payee_key:path}", response_model=RecurringDecision)
def put_recurring(db: MemberDep, payee_key: Annotated[str, Path(min_length=1, max_length=120)],
                  body: RecurringIn) -> dict:
    amount = Decimal(body.amount_expected) if body.amount_expected is not None else None
    return recurring.decide(db.session, db.ctx, db.actor, payee_key, body.decision, body.cadence, amount,
                            body.kind, body.ended)


@router.post("/payee-aliases", response_model=PayeeRenameOut)
def rename_payees(db: MemberDep, body: PayeeRenameIn) -> dict:
    return aliases.rename(db.session, db.ctx, db.actor, body.name, body.payee_keys)


@router.post("/payee-aliases/reset", response_model=PayeeResetOut)
def reset_payees(db: MemberDep, body: PayeeResetIn) -> dict:
    return aliases.reset(db.session, db.ctx, db.actor, body.payee_keys)


@router.post("/payee-aliases/dismiss", response_model=PayeeDismissOut)
def dismiss_alias_suggestion(db: MemberDep, body: PayeeDismissIn) -> dict:
    return aliases.dismiss(db.session, db.ctx, db.actor, body.payee_key, body.name)


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
    extra = {k: v for k, v in (("raw_retention_days", body.raw_retention_days), ("notify_topic", body.notify_topic),
                               ("notify_enabled", body.notify_enabled)) if v is not None}
    if "notify_topic" in body.model_fields_set and body.notify_topic is None:  # explicit null clears the topic
        extra |= {"notify_topic": None, "notify_enabled": False}
    return members.update_settings(db.session, db.ctx, db.actor, month_start_day=body.month_start_day,
                                   local_shop_cap=cap, extra=extra)


@router.patch("/networth/snapshots/{day}", response_model=RemarkOut)
def patch_remark(db: MemberDep, day: date, body: RemarkIn) -> dict:
    out = networth.update_remark(db.session, db.ctx, db.actor, day, body.remark or None)
    if out is None:
        raise NotFound("no snapshot on that date")
    return out


@router.post("/orders", response_model=OrdersOut)
def record_orders(db: MemberDep, body: OrdersIn) -> dict:
    return orders.record(db.session, db.ctx, db.actor, [o.model_dump() for o in body.orders])


@router.post("/orders/assign", response_model=OrdersAssignOut)
def assign_orders(db: MemberDep, body: OrdersAssignIn) -> dict:
    return orders.assign(db.session, db.ctx, db.actor, [(o.source, o.order_no) for o in body.orders], body.account_id)
