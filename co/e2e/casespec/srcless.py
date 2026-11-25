"""无来源新增与产成品入库参照生产订单。

步骤 kind 为 "custom"，sql_name 是操作名（SRCLESS_OPS 并入 ops_run）。
无来源发货单、先开票销售发票、无来源到货单、无来源采购退货单、无来源材料出库单：挑单 SQL 同时读出账套选项。选项禁止时（测试账套
SA.bMustSO_ptxs、PU.bPTHavePO、ST.ballowAddnewVouch「领料必有订单」打开）断言 409 state_mismatch、消息带选项名；
选项放开时自动走成功路径：新建 → 读取 → 删除 → 读取 404（先开票另核 U8 生成的发货单，删除发票后应一并没有）。
无来源退货单（红字发货单）不受 SA.bMustSO_ptxs 限制，同样新建、读取、删除（挑单同发货单）；读取的形态须是红字、类型 05、
1 行数量 −1、不挂发货行和订单行。
无来源采购退货单（红字到货单）不受 PU.bPTHavePO 限制：不论选项开关都应新建成功，随后读取、删除。e2e 从不改账套选项。挑不到主数据记 N/A。无来源到货单另带挑出的末级部门和采购类型（U8 要部门，账套可能没有默认采购类型）。
产成品入库参照生产订单：挑不需检验、剩余 >= 2 的已审核订单行入 1 个，核对合格入库数量 +1，删除后回到原值；
需要检验的行（QcFlag=1 且 ST.bQuality）断言 409。挑不到记 N/A。
材料出库、产成品入库的表头带测试数据里的收发类别（stock.material_out_rd / stock.product_in_rd，桥没有缺省值），没配置记 SKIP。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec import srcless_sql as picks
from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD
from co.e2e.testdata import value as td

_CREATE = "/v1/vouchers/create"
_GEN = "/v1/vouchers/generate"
_LOAD = "/v1/vouchers/load"
_DELETE = "/v1/vouchers/delete"
MEMO = "U8CO-E2E-NOSRC"
# 类型 → （挑单 SQL，选项名，选项为真时禁止）。ballowAddnewVouch 的选项名是「领料必有订单」，同样为真时禁止（实测）。
_KINDS = {
    "dispatch": (picks.sale_pick_sql, "bMustSO_ptxs", True),
    "sale_invoice": (picks.sale_pick_sql, "bMustSO_ptxs", True),
    "sale_return": (picks.sale_pick_sql, "", None),
    "arrival": (picks.arrival_pick_sql, "bPTHavePO", True),
    # 红字不受选项限制（forbid_when 为 None：永不走拒绝分支）。
    "purchase_return": (picks.arrival_pick_sql, "", None),
    "material_out": (picks.material_pick_sql, "ballowAddnewVouch", True),
}


def _send(env: Env, route: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)


def _pick(env: Env, spec: CaseSpec, query: str) -> list[str]:
    cell = first_cell(run_guarded(env, query, spec.name))
    return cell.split("|") if cell else []


def _on(text: str) -> bool:
    return text.strip().upper() in ("1", "TRUE")


def _made_id(payload: dict) -> int:
    raw = payload.get("id")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0


def _sale_body(kind: str, cells: list[str], today: str) -> dict:
    head = {"ccuscode": cells[1], "cstcode": cells[2], "ddate": today, "cmemo": MEMO}
    if len(cells) > 6 and cells[6]:
        head["cdepcode"] = cells[6]
    line: dict = {"cwhcode": cells[3], "cinvcode": cells[4], "iquantity": 1}
    if Decimal(cells[5] or "0") > 0:
        line["itaxunitprice"] = float(cells[5])
    return {"type": kind, "head": head, "lines": [line]}


def _body(kind: str, cells: list[str], today: str) -> dict:
    if kind in ("dispatch", "sale_invoice", "sale_return"):
        return _sale_body(kind, cells, today)
    if kind in ("arrival", "purchase_return"):
        line = {"cinvcode": cells[2], "iquantity": 1, "ioritaxcost": 1}
        if cells[3]:
            line["cwhcode"] = cells[3]
        # U8 无来源到货单要部门；采购类型显式给（账套可能没设默认）。
        head = {"cvencode": cells[1], "cdepcode": cells[4], "cptcode": cells[5], "ddate": today, "cmemo": MEMO}
        return {"type": kind, "head": head, "lines": [line]}
    # 材料出库：收发类别取测试数据（桥没有缺省值）。
    head = {"cwhcode": cells[1], "crdcode": td(MATERIAL_OUT_RD), "ddate": today, "cmemo": MEMO}
    if len(cells) > 3 and cells[3]:
        head["cdepcode"] = cells[3]
    return {"type": kind, "head": head, "lines": [{"cinvcode": cells[2], "iquantity": 1}]}


def _refused(env: Env, kind: str, reply: tuple[int, dict], option: str) -> tuple[str, str]:
    status, payload = reply
    made = _made_id(payload) if status == 200 else 0
    if made:
        _send(env, _DELETE, {"type": kind, "id": made})
        return "FAIL", f"选项 {option} 禁止时仍新建了单据 {made}（已删除）"
    if status != 409 or payload.get("code") != "state_mismatch":
        return "FAIL", f"期望 409 state_mismatch，得到 {status} {payload.get('code')} {payload.get('message')}"[:240]
    if option not in str(payload.get("message") or ""):
        return "FAIL", "消息不含选项名 " + option
    return "PASS", f"409（{option} 禁止无来源）"


def _made_dispatch(env: Env, spec: CaseSpec, invoice: int) -> int:
    cell = first_cell(run_guarded(env, picks.made_dispatch_sql(invoice), spec.name))
    return int(cell) if cell.isdigit() else 0


def _drop(env: Env, kind: str, made: int) -> str:
    status, payload = _send(env, _DELETE, {"type": kind, "id": made})
    if status != 200 or payload.get("deleted") is not True:
        return f"删除 {status} {payload.get('code')} {payload.get('message')}"[:200]
    status, _payload = _send(env, _LOAD, {"type": kind, "id": made})
    return "" if status == 404 else "删除后仍能读取"


def _invoice_problem(env: Env, spec: CaseSpec, made: int, payload: dict) -> str:
    # 先开票：U8 生成的发货单应指回本发票；响应的 dispatch_id 有值时必须与之一致。
    # 本用例不复核，保存后（复核前）就要读到 SBVID 指回本发票的发货单，即 U8 在保存时生成。
    dl = _made_dispatch(env, spec, made)
    if not dl:
        return "保存后（复核前）没有 SBVID 指回本发票的发货单"
    given = payload.get("dispatch_id")
    if given is not None and given != dl:
        return f"dispatch_id {given} 与 SBVID 回读 {dl} 不一致"
    return ""


def _leftover(env: Env, spec: CaseSpec, made: int) -> str:
    # 先开票：删除发票后 U8 生成的发货单还在（桥应已回滚并 409，这里兜底）就删掉它再报。
    left = _made_dispatch(env, spec, made)
    if not left:
        return ""
    _send(env, _DELETE, {"type": "dispatch", "id": left})
    return f"删除发票后 U8 生成的发货单 {left} 仍在（已尝试删除）"


def _cell(row: dict, key: str) -> str:
    """vouchers/load 的值是字符串字典，键大小写按 U8；空值省略。"""
    return next((str(value).strip() for name, value in row.items() if name.lower() == key), "")


def _return_shape(body: dict) -> str:
    """无来源退货单读取：表头 bReturnFlag=1、cVouchType=05；1 行、数量 −1、iCorID 和 iSOsID 为空或 0。"""
    head = body.get("head") if isinstance(body.get("head"), dict) else {}
    lines = [row for row in body.get("lines") or [] if isinstance(row, dict)]
    if _cell(head, "breturnflag").upper() not in ("1", "TRUE") or _cell(head, "cvouchtype") != "05":
        return f"读取的表头不是红字退货单（bReturnFlag {_cell(head, 'breturnflag') or '空'}，类型 {_cell(head, 'cvouchtype') or '空'}）"
    if len(lines) != 1:
        return f"读取的行数 {len(lines)}，期望 1"
    try:
        qty = Decimal(_cell(lines[0], "iquantity") or "0")
    except InvalidOperation:
        qty = Decimal(0)
    if qty != -1:
        return f"读取的数量 {_cell(lines[0], 'iquantity') or '空'}，期望 −1"
    linked = [key for key in ("icorid", "isosid") if _cell(lines[0], key) not in ("", "0")]
    return f"无来源退货单的行挂了来源（{'、'.join(linked)}）" if linked else ""


def _happy(env: Env, spec: CaseSpec, kind: str, reply: tuple[int, dict]) -> tuple[str, str]:
    status, payload = reply
    made = _made_id(payload) if status == 200 else 0
    if not made:
        return "FAIL", f"期望 200，得到 {status} {payload.get('code')} {payload.get('message')}"[:240]
    loaded = 0
    try:
        problem = _invoice_problem(env, spec, made, payload) if kind == "sale_invoice" else ""
        loaded, body = _send(env, _LOAD, {"type": kind, "id": made})
    finally:
        gone = _drop(env, kind, made)
        left = _leftover(env, spec, made) if kind == "sale_invoice" else ""
    if loaded != 200:
        problem = problem or f"新建后读取 {loaded}"
    elif kind == "sale_return":
        problem = problem or _return_shape(body)
    problem = problem or gone or left
    return ("FAIL", problem) if problem else ("PASS", f"新建 {made} 并删除")


def srcless_create(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    kind = dict(step.fields)["type"]
    query, option, forbid_when = _KINDS[kind]
    cells = _pick(env, spec, query())
    if not cells:
        return "N/A", "测试账套挑不到可用的主数据"
    reply = _send(env, _CREATE, _body(kind, cells, env.today))
    if forbid_when is not None and _on(cells[0]) == forbid_when:
        return _refused(env, kind, reply, option)
    return _happy(env, spec, kind, reply)


def _mo_qty(env: Env, spec: CaseSpec, mod_id: int) -> Decimal:
    cell = first_cell(run_guarded(env, picks.mo_qty_sql(mod_id), spec.name))
    try:
        return Decimal(cell)
    except InvalidOperation as exc:
        raise ValueError("合格入库数量回读 " + (cell or "空")) from exc


def _mo_body(cells: list[str], today: str) -> dict:
    head = {"cwhcode": cells[2], "crdcode": td(PRODUCT_IN_RD), "ddate": today}
    lines = [{"source_line_id": int(cells[1]), "quantity": 1}]
    return {"type": "product_in", "id": int(cells[0]), "source_type": "production_order", "head": head, "lines": lines}


def product_in_mo(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    cells = _pick(env, spec, picks.mo_pick_sql(False))
    if not cells:
        return "N/A", "本账套没有不需检验、剩余 >= 2 的已审核生产订单行"
    mod_id = int(cells[1])
    before = _mo_qty(env, spec, mod_id)
    status, payload = _send(env, _GEN, _mo_body(cells, env.today))
    made = _made_id(payload) if status == 200 else 0
    if not made:
        return "FAIL", f"生单 {status} {payload.get('code')} {payload.get('message')}"[:240]
    try:
        after = _mo_qty(env, spec, mod_id)
    finally:
        gone = _drop(env, "product_in", made)
    back = _mo_qty(env, spec, mod_id)
    if after != before + 1:
        return "FAIL", f"合格入库数量 {before} → {after}，期望 +1"
    if gone or back != before:
        return "FAIL", gone or f"删除后合格入库数量 {back}，期望 {before}"
    return "PASS", f"订单行 {mod_id} 入 1 并删除"


def product_in_mo_inspect(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    cells = _pick(env, spec, picks.mo_pick_sql(True))
    if not cells:
        return "N/A", "本账套没有需要检验的已审核生产订单行（或 ST.bQuality 关闭）"
    status, payload = _send(env, _GEN, _mo_body(cells, env.today))
    made = _made_id(payload) if status == 200 else 0
    if made:
        _drop(env, "product_in", made)
        return "FAIL", f"需要检验的订单行仍生成了入库单 {made}（已删除）"
    if status != 409 or "需要检验" not in str(payload.get("message") or ""):
        return "FAIL", f"期望 409「需要检验」，得到 {status} {payload.get('code')} {payload.get('message')}"[:240]
    return "PASS", "409 需要检验"


SRCLESS_OPS = {
    "srcless_create": srcless_create,
    "product_in_mo": product_in_mo,
    "product_in_mo_inspect": product_in_mo_inspect,
}

# 用例名落在 coverage.py 已有的族里（dispatch-、sale-、arrival-、material-out-、product-in-）。
_CREATES = (
    ("dispatch", "dispatch-nosrc", "无来源发货单：选项禁止时 409，放开时新建后删除",
     ("opt:bMustSO_ptxs", "new:dispatch", "new:sale_return", "w:dispatch")),
    ("sale_invoice", "sale-invoice-advance", "先开票销售发票：选项禁止时 409，放开时新建（U8 生成发货单）后删除",
     ("opt:bMustSO_ptxs", "new:sale_invoice", "new:dispatch", "new:sale_return", "w:sale_invoice", "w:dispatch")),
    ("sale_return", "sale-return-create-free", "无来源退货单（红字发货单）：新建后读取、删除",
     ("new:dispatch", "new:sale_return", "w:sale_return")),
    ("arrival", "arrival-nosrc", "无来源到货单：选项禁止时 409，放开时新建后删除",
     ("opt:bPTHavePO", "new:arrival", "new:purchase_return", "w:arrival")),
    ("purchase_return", "purchase-return-create-free", "无来源采购退货单（红字到货单）：新建后读取、删除",
     ("new:arrival", "new:purchase_return", "w:purchase_return")),
    ("material_out", "material-out-nosrc", "无来源材料出库单：选项禁止时 409，放开时新建后删除",
     ("opt:ballowAddnewVouch", "new:material_out", "w:material_out", "stock")),
)


# 正文要带测试数据的类型：缺键时用例记 SKIP。
_DATA = {"material_out": (MATERIAL_OUT_RD,)}


def _create_case(kind: str, name: str, title: str, tags: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", _CREATE, (("type", kind),), sql_name="srcless_create", id_from="none")
    routes = (_CREATE, _LOAD, _DELETE)
    data = _DATA.get(kind, ())
    return CaseSpec(name, title, (), (step,), True, optional=True, data=data, tags=frozenset(tags), routes=routes)


def srcless_cases() -> list[CaseSpec]:
    rows = [_create_case(*item) for item in _CREATES]
    mo_routes = (_GEN, _LOAD, _DELETE)
    mo_step = Step("custom", _GEN, sql_name="product_in_mo", id_from="none")
    rows.append(
        CaseSpec(
            "product-in-from-mo", "产成品入库参照生产订单：合格入库数量 +1，删除后回退", (), (mo_step,), True,
            optional=True, data=(PRODUCT_IN_RD,),
            tags=frozenset(("new:product_in", "w:product_in", "w:production_order", "stock")),
            routes=mo_routes,
        )
    )
    inspect = Step("custom", _GEN, sql_name="product_in_mo_inspect", id_from="none")
    rows.append(
        CaseSpec(
            "product-in-mo-inspect", "产成品入库参照需要检验的生产订单行被拒绝", (), (inspect,), True,
            optional=True, data=(PRODUCT_IN_RD,), tags=frozenset(("opt:bQuality", "probe")), routes=(_GEN,),
        )
    )
    return rows
