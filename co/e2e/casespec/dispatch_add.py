"""发货单修改新增行（vouchers/update 的 op=add，参照本单来源销售订单的行）。

三条用例都自建自删：挑一个不管货位的仓库里可用量 >= 4 的可销售简单存货，新建 2 行销售订单（各 2 个）并审核，
参照第 1 行生成发货单 2 个（不审核，修改只改未审核的发货单）。挑不到数据记 N/A。

dispatch-update-add-line：修改时新增订单第 2 行 1 个 → 200、lines=2 → vouchers/load 2 行 → 发货单上第 2 行数量 1、仓库对、
  金额对得上 → 订单行累计发货 第 1 行 +2、第 2 行 +1 → 删除发货单 → 两行都回到原值。
dispatch-update-add-over-remain：新增订单第 2 行 3 个（只剩 2）→ 409 / 400，发货单仍 1 行，订单第 2 行累计发货不变。
dispatch-update-add-other-so：另建一张同客户的 1 行订单，把它的行加到发货单上 → 400，发货单仍 1 行，那一行累计发货不变。
清理按建的倒序（stock_plus._guard）：删发货单 → 弃审、删订单；清理失败记 FAIL。
步骤 kind 为 "custom"，sql_name 是操作名（DISPATCH_ADD_OPS 并入 ops_run）。
桥的 VoucherSave 状态 1 带 editprop=A 行未实测：第一次跑失败时先看审计里的「回写核对」记录。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _SALE_TAGS, _dec, _Fail, _guard, _Run
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.sale_out_trim_sql import disp_lines_sql, so_lines_sql
from co.e2e.casespec.dispatch_add_sql import added_sql, fh_sql, pick_sql

_GEN = "/v1/vouchers/generate"
_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_UPDATE = "/v1/vouchers/update"
_LOAD = "/v1/vouchers/load"
_MEMO = "CO桥回归"
_ORDER_QTY = (2, 2)
_SHIP = 2
_ADD = 1


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _order(run: _Run, parts: list[str], qtys: tuple[int, ...]) -> tuple[int, list[str]]:
    cus, st, dep, person, inv = (safe_lot(part) for part in parts[:5])
    head = {
        "cCusCode": cus, "cSTCode": st, "cDepCode": dep, "cPersonCode": person, "cBusType": "普通销售",
        "cexch_name": "人民币", "iExchRate": 1, "iTaxRate": 13, "dDate": run.env.today, "cMemo": _MEMO,
    }
    pre = (date.fromisoformat(run.env.today) + timedelta(days=3)).isoformat()
    lines = [{"cInvCode": inv, "iQuantity": qty, "iTaxUnitPrice": 1, "iTaxRate": 13, "dPreDate": pre} for qty in qtys]
    so = run.made(_CREATE, {"type": "sale_order", "head": head, "lines": lines}, f"新建 {len(qtys)} 行销售订单")
    run.drop("sale_order", so)
    run.ok(_VERIFY, {"type": "sale_order", "id": so, "action": "verify"}, "审核销售订单")
    run.unverify("sale_order", so)
    ids = [text.strip() for text in run.cells(so_lines_sql(str(so)))]
    if len(ids) != len(qtys):
        raise _Fail(f"销售订单应有 {len(qtys)} 行，实际 {len(ids)} 行")
    return so, ids


def _ship(run: _Run, so: int, line: str, wh: str) -> int:
    body = {
        "type": "dispatch", "id": so, "head": {"cWhCode": wh, "dDate": run.env.today},
        "lines": [{"source_line_id": int(line), "quantity": _SHIP, "cWhCode": wh}],
    }
    dl = run.made(_GEN, body, "参照订单第 1 行生成发货单")
    run.drop("dispatch", dl)
    return dl


def _fh(run: _Run, so: int) -> dict[str, Decimal]:
    found: dict[str, Decimal] = {}
    for row in run.cells(fh_sql(str(so))):
        parts = row.split("|")
        if len(parts) == 2:
            found[parts[0]] = _dec(parts[1])
    return found


def _rows(run: _Run, dl: int) -> list[list[str]]:
    return [row.split("|") for row in run.cells(disp_lines_sql(str(dl)))]


def _add(run: _Run, dl: int, line: str, qty: int, wh: str) -> tuple[int, dict]:
    row = {"op": "add", "source_line_id": int(line), "iQuantity": qty, "cWhCode": wh, "cMemo": _MEMO}
    return run.send(_UPDATE, {"type": "dispatch", "id": dl, "lines": [row]})


def _setup(run: _Run) -> tuple[list[str], int, list[str], int] | None:
    """挑数据、建 2 行订单、参照第 1 行发货 2 个。挑不到返回 None。"""
    parts = run.pick(pick_sql(), 6)
    if parts is None:
        return None
    so, ids = _order(run, parts, _ORDER_QTY)
    return parts, so, ids, _ship(run, so, ids[0], safe_lot(parts[5]))


# ---- 新增一行 ----


def _added_problem(run: _Run, dl: int, line: str, wh: str) -> str:
    rows = _rows(run, dl)
    if len(rows) != 2:
        return f"新增后发货单应有 2 行，实际 {len(rows)} 行"
    cell = run.cell(added_sql(str(dl), line))
    want = f"{_ADD}.0000|{wh}|1"
    if cell.upper() != want.upper():
        return f"新增行（订单行 {line}）应为 数量|仓库|金额对上 = {want}，实际 {cell or '无'}"
    return ""


def _add_body(run: _Run) -> tuple[str, str]:
    setup = _setup(run)
    if setup is None:
        return "N/A", "账套挑不到不管货位的仓库里可用量 >= 4 的可销售简单存货"
    parts, so, ids, dl = setup
    wh = safe_lot(parts[5])
    base = _fh(run, so)
    status, payload = _add(run, dl, ids[1], _ADD, wh)
    if status != 200:
        raise _Fail(f"新增行应 200，实际 {_brief(status, payload)}")
    if payload.get("lines") != 2:
        raise _Fail(f"响应 lines 应为 2，实际 {payload.get('lines')}")
    loaded = run.ok(_LOAD, {"type": "dispatch", "id": dl}, "读取发货单")
    if len(loaded.get("lines") or []) != 2:
        raise _Fail(f"vouchers/load 应有 2 行，实际 {len(loaded.get('lines') or [])} 行")
    problem = _added_problem(run, dl, ids[1], wh)
    if problem:
        raise _Fail(problem)
    now = _fh(run, so)
    want = {ids[0]: base.get(ids[0], Decimal(0)), ids[1]: base.get(ids[1], Decimal(0)) + _ADD}
    if now != want:
        raise _Fail(f"新增后订单行累计发货应为 {want}，实际 {now}")
    run.settle(1)
    after = _fh(run, so)
    back = {ids[0]: want[ids[0]] - _SHIP, ids[1]: base.get(ids[1], Decimal(0))}
    if after != back:
        raise _Fail(f"删除发货单后订单行累计发货应回到 {back}，实际 {after}")
    return "PASS", (
        f"订单 {so} 两行，发货单 {dl} 参照第 1 行 {_SHIP} 个后新增第 2 行 {_ADD} 个：2 行、金额对上，"
        f"累计发货 {want[ids[0]]} / {want[ids[1]]}；删除后回到 {back[ids[0]]} / {back[ids[1]]}"
    )


# ---- 拒绝：超过可发数量、别的订单 ----


def _unchanged(run: _Run, dl: int, so: int, base: dict[str, Decimal]) -> str:
    rows = _rows(run, dl)
    if len(rows) != 1:
        return f"被拒后发货单应仍 1 行，实际 {len(rows)} 行"
    now = _fh(run, so)
    if now != base:
        return f"被拒后订单行累计发货应不变 {base}，实际 {now}"
    return ""


def _over_body(run: _Run) -> tuple[str, str]:
    setup = _setup(run)
    if setup is None:
        return "N/A", "账套挑不到不管货位的仓库里可用量 >= 4 的可销售简单存货"
    parts, so, ids, dl = setup
    base = _fh(run, so)
    status, payload = _add(run, dl, ids[1], _ORDER_QTY[1] + 1, safe_lot(parts[5]))
    if status not in (400, 409):
        raise _Fail(f"超过可发数量应 409 / 400，实际 {_brief(status, payload)}")
    problem = _unchanged(run, dl, so, base)
    if problem:
        raise _Fail(problem)
    return "PASS", f"发货单 {dl} 新增订单行 {ids[1]} {_ORDER_QTY[1] + 1} 个（只剩 {_ORDER_QTY[1]}）：{_brief(status, payload)}"


def _other_body(run: _Run) -> tuple[str, str]:
    setup = _setup(run)
    if setup is None:
        return "N/A", "账套挑不到不管货位的仓库里可用量 >= 4 的可销售简单存货"
    parts, so, _ids, dl = setup
    other, other_ids = _order(run, parts, (_ORDER_QTY[0],))
    base, other_base = _fh(run, so), _fh(run, other)
    status, payload = _add(run, dl, other_ids[0], _ADD, safe_lot(parts[5]))
    if status != 400:
        raise _Fail(f"别的订单的行应 400，实际 {_brief(status, payload)}")
    problem = _unchanged(run, dl, so, base)
    if not problem and _fh(run, other) != other_base:
        problem = f"另一张订单的累计发货应不变 {other_base}"
    if problem:
        raise _Fail(problem)
    return "PASS", f"发货单 {dl}（订单 {so}）新增订单 {other} 的行：{_brief(status, payload)}"


# ---- 接线 ----


def dispatch_add_line(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _add_body)


def dispatch_add_over(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _over_body)


def dispatch_add_other(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _other_body)


DISPATCH_ADD_OPS = {
    "dispatch_add_line": dispatch_add_line,
    "dispatch_add_over": dispatch_add_over,
    "dispatch_add_other": dispatch_add_other,
}
_ROUTES = (_CREATE, _VERIFY, _GEN, _UPDATE, _LOAD, _DELETE)


def _case(name: str, title: str, op: str) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=_SALE_TAGS, routes=_ROUTES)


def dispatch_add_cases() -> list[CaseSpec]:
    # 同 sale-out-bin-split：自建订单、发货单（发货单占可用量，标签带 stock）。
    return [
        _case("dispatch-update-add-line",
              "发货单修改新增参照来源订单的行：2 行、金额对上，订单行累计发货随之增加，删除后还原", "dispatch_add_line"),
        _case("dispatch-update-add-over-remain",
              "发货单修改新增行超过订单行可发数量：409 / 400，发货单和累计发货不变", "dispatch_add_over"),
        _case("dispatch-update-add-other-so",
              "发货单修改新增别的销售订单的行：400，发货单和累计发货不变", "dispatch_add_other"),
    ]
