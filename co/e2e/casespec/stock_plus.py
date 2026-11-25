"""库存补全用例：销售出库生单带批号 / 货位 / 拆行，外币采购订单生成采购入库，无来源红字采购入库（退库）。

步骤 kind 为 "custom"，sql_name 是操作名（STOCKPLUS_OPS 并入 ops_run）。每个操作自己挑数据、自己建来源单据，
按建的倒序清理（删出库 → 弃审删发货单 → 弃审删订单）；清理失败记 FAIL。挑不到数据记 N/A。
挑单与回读 SQL 在 stock_plus_sql。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.assertutil import sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus_sql import (
    batch_sql,
    bin_split_sql,
    disp_line_sql,
    fx_in_sql,
    fx_po_sql,
    fx_template_sql,
    havepo_sql,
    out_binned_sql,
    out_qty_sql,
    out_rows_sql,
    plain_line_sql,
    po_line_sql,
    red_in_sql,
    red_pin_sql,
    safe_lot,
    so_line_sql,
)
from co.e2e.casespec.stock_binned_sql import lot_prop_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

_GEN = "/v1/vouchers/generate"
_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_LOAD = "/v1/vouchers/load"
_MEMO = "CO桥回归"
_FX_RATE = 7.1
_EPS = Decimal("0.000001")
_CENT = Decimal("0.01")


class _Fail(Exception):
    pass


class _NotApplicable(Exception):
    """账套条件不满足（例如 U8 不许给本币供应商建外币订单），记 N/A。"""


class _Run:
    """一次操作：发请求、跑回读、记下清理动作（后进先出）。"""

    def __init__(self, env: Env, spec: CaseSpec) -> None:
        self.env = env
        self.spec = spec
        self.undo: list[tuple[str, dict, str]] = []

    def send(self, route: str, extra: dict) -> tuple[int, dict]:
        fields = auth_fields(self.env)
        fields.update(extra)
        return invoke(self.env.client, route, fields)

    def ok(self, route: str, extra: dict, what: str) -> dict:
        status, payload = self.send(route, extra)
        if status != 200:
            raise _Fail(f"{what}：{status} {payload.get('code')} {payload.get('message')}"[:240])
        return payload

    def made(self, route: str, extra: dict, what: str) -> int:
        doc = _doc_id(self.ok(route, extra, what))
        if doc == 0:
            raise _Fail(what + "：响应没有新单据 id")
        return doc

    def cells(self, query: str) -> list[str]:
        return sql_cells(run_guarded(self.env, query, self.spec.name))

    def cell(self, query: str) -> str:
        cells = self.cells(query)
        return cells[0].strip() if cells else ""

    def pick(self, query: str, count: int) -> list[str] | None:
        cell = self.cell(query)
        parts = cell.split("|") if cell else []
        return parts if len(parts) == count else None

    def drop(self, kind: str, doc: int) -> None:
        self.undo.append((_DELETE, {"type": kind, "id": doc}, "删除" + kind))

    def unverify(self, kind: str, doc: int) -> None:
        self.undo.append((_VERIFY, {"type": kind, "id": doc, "action": "unverify"}, "弃审" + kind))

    def settle(self, count: int) -> None:
        """立刻执行最近记下的 count 个清理动作，失败就 FAIL。"""
        for _ in range(count):
            route, extra, what = self.undo.pop()
            self.ok(route, extra, what)

    def cleanup(self) -> str:
        problems = []
        while self.undo:
            route, extra, what = self.undo.pop()
            status, payload = self.send(route, extra)
            if status != 200:
                ref = next((extra[k] for k in ("id", "note_no", "cancel_no", "pz_id") if extra.get(k) is not None), "")
                problems.append(f"{what} {ref}：{status} {payload.get('message')}")
        return "；".join(problems)[:240]


def _doc_id(payload: dict) -> int:
    raw = payload.get("id")
    return raw if type(raw) is int and raw > 0 else 0


def _dec(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise _Fail("回读不是数：" + (text or "空")) from exc


def _guard(env: Env, spec: CaseSpec, body: Callable[[_Run], tuple[str, str]]) -> tuple[str, str]:
    run = _Run(env, spec)
    try:
        result = body(run)
    except _NotApplicable as exc:
        result = ("N/A", str(exc)[:240])
    except (_Fail, KeyError, RuntimeError, ValueError) as exc:
        result = ("FAIL", str(exc)[:240])
    left = run.cleanup()
    if not left:
        return result
    head = result[1] + "；" if result[0] == "FAIL" else ""
    return "FAIL", (head + "清理失败：" + left)[:240]


# ---- 销售出库：批号、货位、拆行 ----


def _sale_order(run: _Run, parts: list[str], qty: int) -> int:
    cus, st, dep, person, inv = (safe_lot(part) for part in parts[:5])
    head = {
        "cCusCode": cus, "cSTCode": st, "cDepCode": dep, "cPersonCode": person, "cBusType": "普通销售",
        "cexch_name": "人民币", "iExchRate": 1, "iTaxRate": 13, "dDate": run.env.today, "cMemo": _MEMO,
    }
    pre = (date.fromisoformat(run.env.today) + timedelta(days=3)).isoformat()
    line = {"cInvCode": inv, "iQuantity": qty, "iTaxUnitPrice": 1, "iTaxRate": 13, "dPreDate": pre}
    so = run.made(_CREATE, {"type": "sale_order", "head": head, "lines": [line]}, "新建销售订单")
    run.drop("sale_order", so)
    run.ok(_VERIFY, {"type": "sale_order", "id": so, "action": "verify"}, "审核销售订单")
    run.unverify("sale_order", so)
    return so


def _dispatch(run: _Run, so: int, wh: str, qty: int, lot: str) -> tuple[int, str]:
    line = {"source_line_id": int(run.cell(so_line_sql(str(so))) or 0), "quantity": qty, "cWhCode": wh}
    if lot:
        line["cBatch"] = lot
    body = {"type": "dispatch", "id": so, "head": {"cWhCode": wh, "dDate": run.env.today}, "lines": [line]}
    dl = run.made(_GEN, body, "参照订单生成发货单")
    run.drop("dispatch", dl)
    run.ok(_VERIFY, {"type": "dispatch", "id": dl, "action": "verify"}, "审核发货单")
    run.unverify("dispatch", dl)
    return dl, run.cell(disp_line_sql(str(dl)))


def _sale_out(run: _Run, dl: int, line: str, specs: list[tuple[int, str, str]]) -> int:
    lines = []
    for qty, lot, pos in specs:
        row: dict = {"source_line_id": int(line), "quantity": qty}
        if lot:
            row["cbatch"] = lot
        if pos:
            row["cposition"] = pos
        lines.append(row)
    payload = run.ok(_GEN, {"type": "sale_out", "id": dl, "lines": lines}, "生成销售出库单")
    ids = payload.get("ids") or [payload.get("id")]
    for doc in ids:
        if type(doc) is int and doc > 0:
            run.drop("sale_out", doc)
    if len(ids) != 1 or _doc_id(payload) == 0:
        raise _Fail("一个仓库的发货单生成了多张或没有出库单：" + str(ids))
    return _doc_id(payload)


def _out_problem(run: _Run, out: int, line: str, specs: list[tuple[int, str, str]]) -> str:
    got = sorted(run.cells(out_rows_sql(str(out), line)))
    want = sorted(f"{lot}|{pos}|{Decimal(qty):.4f}|{1 if pos else 0}" for qty, lot, pos in specs)
    if [row.lower() for row in got] != [row.lower() for row in want]:
        return f"出库行 {got}，期望 {want}"
    total = sum((qty for qty, _lot, _pos in specs), 0)
    if abs(_dec(run.cell(out_qty_sql(line))) - Decimal(total)) > _EPS:
        return "发货行累计出库数量不等于请求合计"
    return ""


def _sale_chain(
    run: _Run, parts: list[str], lot: str, specs: list[tuple[int, str, str]],
    extra: Callable[[_Run, int, str], str] | None = None,
) -> tuple[str, str]:
    qty = sum(item[0] for item in specs)
    wh = safe_lot(parts[5])
    so = _sale_order(run, parts, qty)
    dl, line = _dispatch(run, so, wh, qty, lot)
    out = _sale_out(run, dl, line, specs)
    problem = _out_problem(run, out, line, specs)
    if not problem and extra is not None:
        problem = extra(run, out, line)
    run.settle(1)
    if problem:
        raise _Fail(problem)
    if abs(_dec(run.cell(out_qty_sql(line)))) > _EPS or run.cell(out_binned_sql(str(out))) != "0":
        raise _Fail("删除出库单后累计出库数量或货位记录没有回退")
    return "PASS", f"出库单 {out}：{len(specs)} 行，删除后回退"


def _bin_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(bin_split_sql(), 9)
    if parts is None:
        return "N/A", "测试账套挑不到货位仓库里有结存的可销售存货"
    first = safe_lot(parts[6])
    if parts[8]:
        specs = [(1, "", first), (1, "", safe_lot(parts[8]))]
    else:
        specs = [(2, "", first)]
    return _sale_chain(run, parts, "", specs)


def _batch_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(batch_sql(), 8)
    if parts is None:
        return "N/A", "测试账套挑不到有两个可用批号的可销售批次存货"
    return _sale_chain(run, parts, safe_lot(parts[6]), [(1, safe_lot(parts[7]), "")], _lot_prop_problem)


def _lot_prop_problem(run: _Run, out: int, line: str) -> str:
    # 换了批号的出库行，批次属性 1..10 与新批号的档案 AA_BatchProperty 一致（档案没有时全空）；
    # 属性 10 是日期，桥按 yyyy-mm-ddThh:mi:ss 写进 DOM，这里核对落库的日期与档案相同。
    rows = run.cells(lot_prop_sql(str(out), line))
    if not rows:
        return "回读不到出库行的批号"
    for row in rows:
        parts = row.split("|")
        if len(parts) != 5:
            return "批次属性回读格式不对：" + row[:80]
        if parts[1] != "0":
            return f"批号 {parts[0]} 的批次属性与档案不一致 {parts[1]} 项（属性 10：行 {parts[2] or '空'}，档案 {parts[3] or '空'}）"
    return ""


def _refuse_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(plain_line_sql(), 2)
    if parts is None:
        return "N/A", "测试账套挑不到可出库的普通发货行"
    tries = (("cbatch", "E2E-LOT", "未启用批次管理"), ("cposition", "E2E-POS", "未启用货位管理"))
    for key, value, text in tries:
        line = {"source_line_id": int(parts[1]), "quantity": 1, key: value}
        status, payload = run.send(_GEN, {"type": "sale_out", "id": int(parts[0]), "lines": [line]})
        if status == 200 and _doc_id(payload):
            run.drop("sale_out", _doc_id(payload))
            raise _Fail(key + " 本该被拒绝却生成了出库单")
        message = str(payload.get("message") or "")
        if status != 400 or text not in message:
            raise _Fail(f"{key}：期望 400「{text}」，实际 {status} {message}"[:240])
    return "PASS", "批号、货位都在生成前 400"


# ---- 采购入库：外币、红字退库 ----


def _fx_order(run: _Run, tpl: list[str]) -> tuple[int, str, str]:
    ven, dep, person, pt, bus, tax, inv, price, mode, cur, wh = tpl
    head = {
        "cVenCode": safe_lot(ven), "cDepCode": safe_lot(dep), "cPTCode": safe_lot(pt), "cBusType": bus,
        "cexch_name": cur, "nflat": _FX_RATE, "iTaxRate": float(_dec(tax)), "dPODate": run.env.today, "cMemo": _MEMO,
    }
    if person:
        head["cPersonCode"] = safe_lot(person)
    line = {"cInvCode": safe_lot(inv), "iQuantity": 1, "iPerTaxRate": float(_dec(tax))}
    line["iTaxPrice" if mode == "T" else "iUnitPrice"] = float(_dec(price))
    status, payload = run.send(_CREATE, {"type": "purchase_order", "head": head, "lines": [line]})
    if status == 409:
        # U8 不许给本币供应商建外币订单等：账套条件不满足，不算桥的失败。
        raise _NotApplicable(f"U8 拒绝新建外币采购订单：{payload.get('message')}"[:240])
    po = _doc_id(payload) if status == 200 else 0
    if po == 0:
        raise _Fail(f"新建外币采购订单：{status} {payload.get('code')} {payload.get('message')}"[:240])
    run.drop("purchase_order", po)
    run.ok(_VERIFY, {"type": "purchase_order", "id": po, "action": "verify"}, "审核采购订单")
    run.unverify("purchase_order", po)
    return po, run.cell(po_line_sql(str(po))), safe_lot(wh)


def _fx_body(run: _Run) -> tuple[str, str]:
    found = run.pick(fx_po_sql(), 3)
    if found is not None:
        po, po_line, wh = int(found[0]), found[1], safe_lot(found[2])
    else:
        tpl = run.pick(fx_template_sql(), 11)
        if tpl is None:
            return "N/A", "本账套没有外币或采购订单模板"
        po, po_line, wh = _fx_order(run, tpl)
    line = {"source_line_id": int(po_line), "quantity": 1}
    body = {"type": "purchase_in", "id": po, "head": {"cWhCode": wh}, "lines": [line]}
    pin = run.made(_GEN, body, "参照外币订单生成采购入库")
    run.drop("purchase_in", pin)
    parts = run.cell(fx_in_sql(str(pin))).split("|")
    if len(parts) != 3:
        raise _Fail("回读采购入库金额失败")
    rate, diff, orisum = (_dec(part) for part in parts)
    if rate == 1 or orisum == 0:
        raise _Fail(f"汇率 {rate}、原币价税合计 {orisum}，期望外币且非零")
    if diff > _CENT:
        raise _Fail(f"本币价税合计与原币 × 汇率相差 {diff}")
    return "PASS", f"汇率 {rate}，本币 = 原币 × 汇率"


def _red_request(parts: list[str], today: str) -> dict:
    wh, ven, dep, inv, ptype = (safe_lot(part) for part in parts)
    head = {"cwhcode": wh, "cvencode": ven, "cdepcode": dep, "cptcode": ptype, "ddate": today, "cmemo": _MEMO, "red": True}
    line = {"cinvcode": inv, "iquantity": 1, "iunitcost": 1, "itaxrate": 13}
    return {"type": "purchase_in", "head": head, "lines": [line]}


def _red_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(red_pin_sql(), 5)
    if parts is None:
        return "N/A", "测试账套挑不到有结存的外购存货"
    # 「普通业务必有订单」只拦蓝字，红字退库在选项打开时同样新建（同 U8 客户端），选项状态只记进结果。
    option = "必有订单打开" if run.cell(havepo_sql()) == "1" else "必有订单关闭"
    status, payload = run.send(_CREATE, _red_request(parts, run.env.today))
    if status == 200 and _doc_id(payload):
        run.drop("purchase_in", _doc_id(payload))
    if status != 200 or not _doc_id(payload):
        raise _Fail(f"新建红字采购入库（{option}）：{status} {payload.get('code')} {payload.get('message')}"[:240])
    pin = _doc_id(payload)
    red, qty, total = (run.cell(red_in_sql(str(pin))).split("|") + ["", "", ""])[:3]
    if red != "1" or _dec(qty) != -1 or _dec(total) >= 0:
        raise _Fail(f"红字标记 {red}、数量 {qty}、价税合计 {total}，期望 1、-1、负数")
    run.settle(1)
    status, _payload = run.send(_LOAD, {"type": "purchase_in", "id": pin})
    if status != 404:
        raise _Fail("删除后仍能读取")
    return "PASS", f"{option}，红字入库单 {pin}：数量 -1，删除成功"


def sop_refuse(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _refuse_body)


def sop_bin(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _bin_body)


def sop_batch(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _batch_body)


def pin_fx(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _fx_body)


def pin_red(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _red_body)


STOCKPLUS_OPS = {"sop_refuse": sop_refuse, "sop_bin": sop_bin, "sop_batch": sop_batch, "pin_fx": pin_fx, "pin_red": pin_red}

_SALE_TAGS = frozenset(
    ("new:sale_order", "w:sale_order", "new:dispatch", "new:sale_return", "w:dispatch", "new:sale_out", "w:sale_out", "stock")
)
_SALE_ROUTES = (_CREATE, _VERIFY, _GEN, _DELETE)


def _case(name: str, title: str, op: str, tags: frozenset[str], routes: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=tags, routes=routes)


def stockplus_cases() -> list[CaseSpec]:
    return [
        _case("sale-out-batch-refuse", "销售出库生单：普通存货填批号、普通仓库填货位在生成前 400", "sop_refuse",
              frozenset(("probe", "w:dispatch", "new:sale_out", "w:sale_out")), (_GEN,)),
        _case("sale-out-bin-split", "销售出库生单按货位拆行：InvPosition 与累计出库对上，删除后回退", "sop_bin",
              _SALE_TAGS, _SALE_ROUTES),
        _case("sale-out-batch-override", "销售出库生单改批号：出库行批号换成请求的，删除后回退", "sop_batch",
              _SALE_TAGS, _SALE_ROUTES),
        _case("purchase-in-fx", "外币采购订单生成采购入库：本币价税合计 = 原币 × 汇率", "pin_fx",
              frozenset(("new:purchase_order", "w:purchase_order", "new:purchase_in", "w:purchase_in", "stock")),
              (_CREATE, _VERIFY, _GEN, _DELETE)),
        _case("purchase-in-red-nosrc", "无来源红字采购入库：必有订单打开与否都新建，数量为负、可删除", "pin_red",
              frozenset(("opt:havepo", "new:purchase_in", "w:purchase_in", "stock")), (_CREATE, _DELETE, _LOAD)),
    ]
