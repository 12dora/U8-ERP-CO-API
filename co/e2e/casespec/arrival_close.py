"""到货单关闭 / 打开用例（vouchers/close，type=arrival），全程自建自删。

arrival-close：采购订单 2 个 → 审核 → 参照订单生成到货单 2 个 → 未审核时关闭 409 → 审核 → 关闭预演（dry_run，回滚模式，
  状态不变）→ 整单关闭：响应 closed、closed_by 与表头 ccloser 一致、各行 closed；表头 ccloser、dclosedate，行 cbcloser、
  dlineclosedate 都有值 → 再关 409 → 整单打开：响应与表头、各行关闭人和关闭日期全部清空。清理按建的倒序（弃审删到货单、
  订单；中途失败时先打开到货单）。挑单同采购入库参照到货单（pu_settle_manual_sql.pick_sql），挑不到记 N/A。
arrival-close-lines：两行采购订单 → 审核 → 参照两行生成到货单 → 审核 → 只关第一行（line_ids）：响应和回读都是第一行关闭、
  第二行未关、表头未关 → 第一行再关 409 → 只打开第一行：两行、表头都未关闭。
关闭请求没有成功（如 504）但回读已关闭时也记下打开的清理，免得清理被已关闭挡住。
步骤 kind 为 "custom"，sql_name 是操作名（ARRIVAL_CLOSE_OPS 并入 ops_run）。各类账套都跑。
"""

from __future__ import annotations

from co.e2e.casespec import arrival_close_sql as q
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _Run
from co.e2e.casespec.pu_arrival_in import _seed
from co.e2e.casespec.pu_settle_manual import _gen_body, _made_verified, _order, _Seed

CASE_NAME = "arrival-close"
LINES_CASE = "arrival-close-lines"
_KIND = "arrival"
_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_CLOSE = "/v1/vouchers/close"
_CREATE = "/v1/vouchers/create"
_MEMO = "U8CO-E2E-ARR-CLOSE"


def _expect(ok: bool, message: str) -> None:
    if not ok:
        raise _Fail(message)


def _close_body(arr: int, action: str, dry: bool = False) -> dict:
    body: dict = {"type": _KIND, "id": arr, "action": action}
    if dry:
        body["dry_run"] = True
    return body


def _refused(run: _Run, body: dict, what: str) -> None:
    status, payload = run.send(_CLOSE, body)
    got = f"{status} {payload.get('code')} {payload.get('message')}"
    _expect(status == 409 and payload.get("code") == "state_mismatch", f"{what}应 409 state_mismatch，实际 {got}"[:240])


def _state(run: _Run, arr: int) -> list[str]:
    parts = run.pick(q.close_state_sql(arr), q.STATE_COLS)
    if parts is None:
        raise _Fail(f"回读不到到货单 {arr} 的关闭状态")
    return parts


def _want_open(run: _Run, arr: int, when: str) -> None:
    closer, dated, rows, shut, shut_dated = _state(run, arr)
    got = "|".join((closer, dated, rows, shut, shut_dated))
    _expect(closer == "" and dated == "0" and rows != "0" and shut == "0" and shut_dated == "0",
            f"{when}到货单应未关闭：关闭人|关闭日期|行数|关闭行|有关闭日期的行 = {got}")


def _want_lines(payload: dict, shut: bool, what: str) -> None:
    lines = payload.get("lines")
    ok = isinstance(lines, list) and len(lines) > 0 and all(item.get("closed") is shut for item in lines)
    _expect(ok, f"{what}：响应的 lines 应全部 closed={shut}，实际 {lines}"[:240])


def _dry(run: _Run, arr: int) -> None:
    payload = run.ok(_CLOSE, _close_body(arr, "close", True), "关闭到货单预演")
    shape = (payload.get("dry_run"), payload.get("mode"))
    _expect(shape == (True, "rollback"), f"关闭预演应 dry_run=true、mode=rollback，实际 {shape}")
    _want_open(run, arr, "预演后")


def _closing(run: _Run, arr: int, body: dict, what: str) -> dict:
    """发关闭请求并记下「整单打开」的清理（中途失败时清理先打开，否则弃审、删除都会被已关闭挡住）。
    请求没成功但回读已有关闭（如提交后回读失败的 504）也记下清理，再 FAIL。"""
    status, payload = run.send(_CLOSE, body)
    if status != 200:
        closer, _dated, _rows, shut, _shut_dated = _state(run, arr)
        if closer or shut != "0":
            run.undo.append((_CLOSE, _close_body(arr, "open"), "打开到货单"))
        raise _Fail(f"{what}：{status} {payload.get('code')} {payload.get('message')}"[:240])
    run.undo.append((_CLOSE, _close_body(arr, "open"), "打开到货单"))
    return payload


def _close(run: _Run, arr: int) -> str:
    payload = _closing(run, arr, _close_body(arr, "close"), "整单关闭到货单")
    closer = str(payload.get("closed_by") or "")
    _expect(payload.get("closed") is True and closer != "", f"关闭后响应 closed / closed_by 不符：{payload.get('closed')} / {closer}")
    _expect(str(payload.get("closed_at") or "") != "", "关闭后响应没有 closed_at")
    _want_lines(payload, True, "关闭后")
    head, dated, rows, shut, shut_dated = _state(run, arr)
    got = "|".join((head, dated, rows, shut, shut_dated))
    _expect(head == closer and dated == "1" and rows != "0" and shut == rows and shut_dated == rows,
            f"关闭后回读：关闭人|关闭日期|行数|关闭行|有关闭日期的行 = {got}，关闭人应为 {closer}")
    return closer


def _open(run: _Run, arr: int) -> None:
    payload = run.ok(_CLOSE, _close_body(arr, "open"), "整单打开到货单")
    run.undo.pop()  # 已打开：撤掉关闭时记下的「打开到货单」清理
    _expect(payload.get("closed") is False and not payload.get("closed_by"),
            f"打开后响应 closed / closed_by 不符：{payload.get('closed')} / {payload.get('closed_by')}")
    _want_lines(payload, False, "打开后")
    _want_open(run, arr, "打开后")


def _body(run: _Run) -> tuple[str, str]:
    seed = _seed(run)
    po, po_line = _order(run, seed, 2)
    arr = run.made(_GEN, _gen_body(_KIND, po, po_line, 2, {"cWhCode": seed.wh}), "参照采购订单生成到货单")
    run.drop(_KIND, arr)
    _refused(run, _close_body(arr, "close"), "未审核的到货单关闭")
    run.ok(_VERIFY, {"type": _KIND, "id": arr, "action": "verify"}, "审核到货单")
    run.unverify(_KIND, arr)
    _want_open(run, arr, "审核后")
    _dry(run, arr)
    closer = _close(run, arr)
    _refused(run, _close_body(arr, "close"), "已关闭的到货单再关")
    _open(run, arr)
    return "PASS", f"到货单 {arr}：未审核关闭 409、预演不落库、整单关闭（关闭人 {closer}）、再关 409、整单打开，回读一致"


# ---- arrival-close-lines：按行关闭、打开 ----


def _two(run: _Run, query: str, what: str) -> tuple[int, int]:
    parts = run.pick(query, 3)
    if parts is None or parts[0] != "2" or not parts[1].isdigit() or not parts[2].isdigit() or parts[1] == parts[2]:
        raise _Fail(f"{what}应为两行，实际 {parts}")
    return int(parts[1]), int(parts[2])


def _order2(run: _Run, seed: _Seed) -> tuple[int, int, int]:
    """两行本币普通采购订单（各 1 个，无税单价 1、税率 13）→ 审核；返回（订单 id, 第一行 ID, 第二行 ID）。"""
    head = {"cVenCode": seed.ven, "cDepCode": seed.dep, "cPTCode": seed.ptype, "cBusType": "普通采购",
            "cexch_name": "人民币", "nflat": 1, "iTaxRate": 13, "dPODate": run.env.today, "cMemo": _MEMO}
    line = {"cInvCode": seed.inv, "iQuantity": 1, "iUnitPrice": 1, "iPerTaxRate": 13}
    body = {"type": "purchase_order", "head": head, "lines": [line, dict(line)]}
    po = _made_verified(run, _CREATE, body, "新建两行采购订单")
    return (po, *_two(run, q.po_lines_sql(po), "采购订单行"))


def _line_body(arr: int, action: str, line: int) -> dict:
    return dict(_close_body(arr, action), line_ids=[line])


def _line_flags(payload: dict) -> dict:
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return {}
    return {item.get("line_id"): item.get("closed") for item in lines if isinstance(item, dict)}


def _close_first(run: _Run, arr: int, first: int, second: int) -> None:
    payload = _closing(run, arr, _line_body(arr, "close", first), "关闭到货单第一行")
    flags = _line_flags(payload)
    _expect(payload.get("closed") is False and flags == {first: True, second: False},
            f"只关第一行后响应应 closed=false、只有第一行 closed：{payload.get('closed')} / {flags}"[:240])
    head, one, one_dated, two, two_dated = _line_state(run, arr, first, second)
    got = "|".join((head, one, one_dated, two, two_dated))
    _expect(head == "" and (one, one_dated, two, two_dated) == ("1", "1", "0", "0"),
            f"只关第一行后回读：表头关闭人|第一行关闭|第一行关闭日期|第二行关闭|第二行关闭日期 = {got}")


def _line_state(run: _Run, arr: int, first: int, second: int) -> list[str]:
    parts = run.pick(q.lines_state_sql(arr, first, second), q.LINE_COLS)
    if parts is None:
        raise _Fail(f"回读不到到货单 {arr} 的行关闭状态")
    return parts


def _open_first(run: _Run, arr: int, first: int, second: int) -> None:
    payload = run.ok(_CLOSE, _line_body(arr, "open", first), "打开到货单第一行")
    run.undo.pop()  # 已打开：撤掉关闭时记下的「打开到货单」清理
    flags = _line_flags(payload)
    _expect(payload.get("closed") is False and flags == {first: False, second: False},
            f"打开第一行后响应应全部未关闭：{payload.get('closed')} / {flags}"[:240])
    _want_open(run, arr, "打开第一行后")


def _lines_body(run: _Run) -> tuple[str, str]:
    seed = _seed(run)
    po, po_one, po_two = _order2(run, seed)
    lines = [{"source_line_id": po_one, "quantity": 1}, {"source_line_id": po_two, "quantity": 1}]
    gen = {"type": _KIND, "id": po, "head": {"cWhCode": seed.wh}, "lines": lines}
    arr = _made_verified(run, _GEN, gen, "参照两行采购订单生成到货单")
    first, second = _two(run, q.arrival_lines_sql(arr), "到货单行")
    _close_first(run, arr, first, second)
    _refused(run, _line_body(arr, "close", first), "已关闭的行再关")
    _open_first(run, arr, first, second)
    return "PASS", f"到货单 {arr}：只关第一行（第二行、表头未关）、该行再关 409、打开该行，回读一致"


def arrival_close(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _body)


def arrival_close_lines(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _lines_body)


ARRIVAL_CLOSE_OPS = {"arrival_close": arrival_close, "arrival_close_lines": arrival_close_lines}


def arrival_close_cases() -> list[CaseSpec]:
    # 建采购订单、到货单（与采购退货单共用编号）；不动现存量。
    tags = frozenset(("new:purchase_order", "w:purchase_order", "new:arrival", "w:arrival", "new:purchase_return"))
    title = "到货单整单关闭、打开：未审核 409、预演、已关闭再关 409，表头与行的关闭人和关闭日期回读"
    step = Step("custom", sql_name="arrival_close", id_from="none")
    routes = (_GEN, _VERIFY, _DELETE, _CLOSE)
    lines_title = "到货单按行关闭、打开：两行到货单只关第一行，第二行和表头不受影响，该行再关 409，打开后全部未关闭"
    lines_step = Step("custom", sql_name="arrival_close_lines", id_from="none")
    return [
        CaseSpec(CASE_NAME, title, (), (step,), True, optional=True, tags=tags, routes=routes),
        CaseSpec(LINES_CASE, lines_title, (), (lines_step,), True, optional=True, tags=tags, routes=(_CREATE, *routes)),
    ]

