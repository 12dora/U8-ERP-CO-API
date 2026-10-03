"""POST /v1/co/intercompany/generate_buyer：卖方明细按对照换成买方存货，参照买方的公司间采购订单生单。

生单本身走 co_service.run_call 的 vouchers/generate（授权、写入策略、预演、幂等键、审计字段都与直接调用相同），
审计动作再改回本路由。订单从 reports/order_execution（only_open）里挑。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import ValidationError

from u8co_api.co_bridge import to_api_error
from u8co_api.co_ic_core import allow_accs, call_acc, ic_map_of, paged, remember_ic
from u8co_api.co_ic_greedy import number
from u8co_api.co_ic_map import IcGroup
from u8co_api.co_idem import HEADER, lookup_fields
from u8co_api.co_models_edit import CoGenerateIn
from u8co_api.co_models_ic import IcGenerateIn
from u8co_api.co_service import bridge_of, bridge_payload, refuse_read_only, run_call
from u8co_api.errors import ApiError, bad_request, conflict, timeout_error

ORDER_EXEC = "/v1/reports/order_execution"
GENERATE = "/v1/vouchers/generate"
GENERATE_ACTION = "co:vouchers/generate"
IDEM_GET = "/v1/idempotency/get"
PO_PAGE, PO_PAGES = 1000, 10
# 采购入库看累计入库数，到货单看累计到货数。
_DONE_KEY = {"purchase_in": "in_qty", "arrival": "arrived_qty"}


@dataclass
class _PoLine:
    po_id: int
    po_code: str | None
    line_id: int
    inv_code: str
    open_qty: Decimal


@dataclass
class _Need:
    """买方一个存货要的数量，以及对应的卖方编码。"""

    qty: Decimal = Decimal(0)
    seller_codes: list[str] = field(default_factory=list)


def run_generate(request, caller, action: str, _bridge_path: str, body: IcGenerateIn) -> dict:
    login = body.login
    request.state.ic_caller = caller
    remember_ic(request, action, [login], body.audit_ref())
    group = ic_map_of(request).group_of({login.acc, body.seller_acc})
    allow_accs(request, [login.acc, body.seller_acc], caller)
    # 买方是只读账套时，查订单、查幂等记录之前就拒绝（卖方只读取，不受限）。
    refuse_read_only(request, action, login.acc)
    _check_key(request, body)
    replay = _replayed(request, caller, body)
    if replay is not None:
        request.state.audit_action = f"{action}#{body.audit_ref()}:replay"
        return replay
    vendor = group.vendor_code(login.acc, body.seller_acc)
    if vendor is None:
        error = conflict(f"公司间对照里没有账套 {body.seller_acc} 在账套 {login.acc} 的供应商编码（as_vendor）", "ic_party_unmapped")
        error.hint = "管理员在 U8CO_IC_MAP_FILE 里补上往来单位编码"
        raise error
    needs = _needs(group, body)
    po_lines = _open_lines(request, body, vendor)
    po_id, allocated = _choose(po_lines, needs, body.po_id)
    plan = _plan(body, vendor, po_lines, (po_id, allocated), needs)
    generate = _generate_body(body, po_id, allocated)
    try:
        result = run_call(request, caller, GENERATE_ACTION, GENERATE, generate)
    finally:
        # run_call 把审计记成 vouchers/generate；改回本路由，后缀记卖方、买方和订单。
        request.state.audit_action = f"{action}#{body.audit_ref()}:po{po_id}"
    out = dict(result)
    out["plan"] = plan
    return out


def _check_key(request, body: IcGenerateIn) -> None:
    keys = request.headers.getlist(HEADER)
    if not body.dry_run and not keys:
        error = bad_request("正式生成必须带 Idempotency-Key", "idempotency_required", field="dry_run")
        error.hint = "先用 dry_run=true 预演，正式生成时带上 Idempotency-Key 头"
        raise error


def _replayed(request, caller, body: IcGenerateIn) -> dict | None:
    """正式生成带幂等键时，先查买方桥上 vouchers/generate 的同键记录（同一调用方、操作员）。
    已有记录就不再挑单：第一次之后订单余量已变，重新挑单会换订单或数量，桥会判为内容不同。
    还在执行（in_flight）时照常往下走：第一次尚未提交，挑出的订单相同，桥等待同一结果。"""
    keys = request.headers.getlist(HEADER)
    if body.dry_run or len(keys) != 1:
        return None
    payload = bridge_payload(body.login)
    payload.update(lookup_fields(caller, "/v1/co/vouchers/generate", keys[0]))
    found = bridge_of(request, body.login.acc).call(IDEM_GET, payload)
    if not found.get("found") or found.get("state") == "in_flight":
        return None
    status, stored = found.get("status"), found.get("response")
    if isinstance(status, int) and 200 <= status < 300 and isinstance(stored, dict):
        _check_replay(body, stored)
    request.state.outcome = "idempotent_replay"
    if isinstance(status, int) and isinstance(stored, dict):
        if 200 <= status < 300:
            # 计划只在第一次请求里有；重放不再挑单，响应里没有 plan。
            return dict(stored)
        raise to_api_error(status, stored)
    raise timeout_error("同一幂等键的第一次生成结果未知，请按业务内容核对买方单据", "outcome_unknown")


def _check_replay(body: IcGenerateIn, stored: dict) -> None:
    """同键的第一次结果要与本次请求一致：单据类型相同、参照采购订单生单，给了 po_id 时订单相同；否则 409。"""
    same = stored.get("type") == body.type and stored.get("source_type", "purchase_order") == "purchase_order"
    if same and body.po_id is not None and "source_id" in stored:
        same = stored.get("source_id") == body.po_id
    if not same:
        error = conflict("同一幂等键的第一次请求内容不同（单据类型或采购订单不一致）", "idempotency_mismatch")
        error.detail = {"type": stored.get("type"), "source_type": stored.get("source_type"), "po_id": stored.get("source_id")}
        error.hint = "换一个新的 Idempotency-Key，或按第一次的内容重试"
        raise error


def _needs(group: IcGroup, body: IcGenerateIn) -> dict[str, _Need]:
    needs: dict[str, _Need] = defaultdict(_Need)
    missing: list[str] = []
    for line in body.lines:
        entry = group.inventory_of(body.seller_acc, line.inv_code)
        code = entry.codes.get(body.login.acc) if entry is not None else None
        if code is None:
            missing.append(line.inv_code)
            continue
        need = needs[code]
        need.qty += Decimal(str(line.quantity))
        if line.inv_code not in need.seller_codes:
            need.seller_codes.append(line.inv_code)
    if missing:
        error = conflict("这些存货在公司间对照里没有买方账套的编码", "ic_inventory_unmapped")
        error.detail = {"codes": sorted(set(missing))}
        raise error
    return dict(needs)


def _open_lines(request, body: IcGenerateIn, vendor: str) -> list[_PoLine]:
    query: dict = {"type": "purchase_order", "only_open": True}
    if body.po_id is not None:
        query["ids"] = [body.po_id]
    else:
        query["partner"] = vendor
    rows = paged(request, body.login, ORDER_EXEC, query, page_size=PO_PAGE, max_pages=PO_PAGES)
    done_key = _DONE_KEY[body.type]
    found: list[_PoLine] = []
    for row in rows:
        line = _po_line(row, vendor, done_key)
        if line is not None:
            found.append(line)
    return found


def _po_line(row: dict, vendor: str, done_key: str) -> _PoLine | None:
    if row.get("partner") != vendor or row.get("closed") in (True, 1):
        return None
    po_id, line_id, inv = row.get("id"), row.get("line_id"), row.get("inv_code")
    qty = number(row.get("qty"))
    if not isinstance(po_id, int) or not isinstance(line_id, int) or not isinstance(inv, str) or qty is None:
        return None
    left = qty - (number(row.get(done_key)) or Decimal(0))
    if left <= 0:
        return None
    code = row.get("code")
    return _PoLine(po_id, code if isinstance(code, str) else None, line_id, inv, left)


def _choose(lines: list[_PoLine], needs: dict[str, _Need], po_id: int | None) -> tuple[int, list[tuple[int, Decimal]]]:
    """挑一张能覆盖全部需要的订单（给了 po_id 只看它），按订单行顺序分配数量。"""
    by_po: dict[int, list[_PoLine]] = defaultdict(list)
    for line in sorted(lines, key=lambda item: (item.po_id, item.line_id)):
        by_po[line.po_id].append(line)
    for candidate in sorted(by_po):
        if po_id is not None and candidate != po_id:
            continue
        allocated = _allocate(by_po[candidate], needs)
        if allocated is not None:
            return candidate, allocated
    raise _no_po(po_id)


def _allocate(lines: list[_PoLine], needs: dict[str, _Need]) -> list[tuple[int, Decimal]] | None:
    allocated: list[tuple[int, Decimal]] = []
    for code, need in needs.items():
        left = need.qty
        for line in lines:
            if left <= 0:
                break
            if line.inv_code != code:
                continue
            take = min(left, line.open_qty)
            allocated.append((line.line_id, take))
            left -= take
        if left > 0:
            return None
    return allocated


def _no_po(po_id: int | None) -> ApiError:
    if po_id is not None:
        error = conflict(f"采购订单 {po_id} 不是卖方公司的未执行完订单，或剩余数量不够", "ic_no_open_po")
    else:
        error = conflict("没有能覆盖这些存货和数量的公司间采购订单", "ic_no_open_po")
    error.hint = "在买方账套补一张公司间采购订单，或指定 po_id"
    return error


def _plan(body: IcGenerateIn, vendor: str, lines: list[_PoLine], choice: tuple, needs: dict[str, _Need]) -> dict:
    po_id, allocated = choice
    by_line = {line.line_id: line for line in lines if line.po_id == po_id}
    code = next((line.po_code for line in by_line.values()), None)
    rows = []
    for line_id, qty in allocated:
        inv = by_line[line_id].inv_code
        rows.append(
            {
                "buyer_inv_code": inv,
                "seller_inv_codes": needs[inv].seller_codes,
                "quantity": format(qty.normalize(), "f"),
                "source_line_id": line_id,
            }
        )
    return {
        "buyer_acc": body.login.acc,
        "seller_acc": body.seller_acc,
        "vendor": vendor,
        "po_id": po_id,
        "po_code": code,
        "lines": rows,
    }


def _generate_body(body: IcGenerateIn, po_id: int, allocated: list[tuple[int, Decimal]]) -> CoGenerateIn:
    head = dict(body.head or {})
    if body.date is not None and not any(key.lower() == "ddate" for key in head):
        head["dDate"] = body.date
    data = body.login.model_dump(exclude_none=True)
    data.update(
        type=body.type,
        source_type="purchase_order",
        id=po_id,
        lines=[{"source_line_id": line_id, "quantity": _qty(qty)} for line_id, qty in allocated],
        dry_run=body.dry_run,
    )
    if head:
        data["head"] = head
    try:
        return CoGenerateIn.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        raise bad_request("生成的请求无效：" + str(first.get("msg", "")), field="head") from exc


def _qty(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)
