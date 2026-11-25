"""无来源销售出库单（sale_out，cSource=库存）新增。

步骤 kind 为 "custom"，sql_name 是操作名（SALE_OUT_NOSRC_OPS 并入 ops_run）。先读账套选项（gate_sql，口径同桥
StockSaleOut.RefuseOption）：销售管理已启用（SA 启用日期非空）或库存选项「销售出库单由销售系统生成」（ST.bSAcreat）为真时，
断言 409 state_mismatch、消息说明原因、销售出库单最大主键不变（没有建单）；否则走成功路径。e2e 从不改账套选项。

sale-out-nosrc：挑非货位仓里有可用量的普通存货（不分批、不管保质期）和客户、部门、收发类别、销售类型（优先最近一张销售出库单上的）→
  新建 1 行数量 1 → 读取 → 回读表头 cSource=库存、cBusType=普通销售、VT_ID=87、表体没有发货单行（iDLsID 为空）→ 审核 → 弃审 →
  删除 → 读取 404。中途失败时尽力弃审、删除。挑不到数据记 N/A。
dry-sale-out-nosrc：同样的正文带 dry_run 新建：放开时 200 预演（rollback）、docs 里有新单据、之后最大主键不变；禁止时同样 409。
"""

from __future__ import annotations

from co.e2e.casespec import sale_out_nosrc_sql as q
from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

_CREATE = "/v1/vouchers/create"
_LOAD = "/v1/vouchers/load"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_KIND = "sale_out"
MEMO = "U8CO-E2E-SO-NOSRC"
_REASONS = ("销售管理", "销售系统生成")
# 选项禁止时正文里的档案编码不会被查（选项闸门在档案预检之前），挑不到数据时用占位值。
_DUMMY = ["01", "A001", "C001", "D01", "", ""]


def _send(env: Env, route: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)


def _cells(env: Env, spec: CaseSpec, query: str) -> list[str]:
    cell = first_cell(run_guarded(env, query, spec.name))
    return cell.split("|") if cell else []


def _on(text: str) -> bool:
    return text.strip().upper() in ("1", "TRUE")


def _forbidden(env: Env, spec: CaseSpec) -> bool:
    gate = _cells(env, spec, q.gate_sql()) + ["", ""]
    return bool(gate[0].strip()) or _on(gate[1])


def _max_id(env: Env, spec: CaseSpec) -> int:
    cell = first_cell(run_guarded(env, q.max_id_sql(), spec.name))
    return int(cell) if cell.isdigit() else -1


def _made_id(payload: dict) -> int:
    raw = payload.get("id")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _body(cells: list[str], today: str) -> dict:
    head = {"cwhcode": cells[0], "ccuscode": cells[2], "cdepcode": cells[3], "ddate": today, "cmemo": MEMO}
    if cells[4]:
        head["crdcode"] = cells[4]
    if cells[5]:
        head["cstcode"] = cells[5]
    return {"type": _KIND, "head": head, "lines": [{"cinvcode": cells[1], "iquantity": 1}]}


def _refused(env: Env, spec: CaseSpec, body: dict) -> tuple[str, str]:
    before = _max_id(env, spec)
    status, payload = _send(env, _CREATE, body)
    made = _made_id(payload) if status == 200 else 0
    if made:
        _drop(env, made)
        return "FAIL", f"账套已启用销售管理（或由销售系统生成）时仍新建了单据 {made}（已删除）"
    if status != 409 or payload.get("code") != "state_mismatch":
        return "FAIL", "期望 409 state_mismatch，得到 " + _brief(status, payload)
    message = str(payload.get("message") or "")
    if not any(word in message for word in _REASONS):
        return "FAIL", "409 消息没有说明原因：" + message[:120]
    if _max_id(env, spec) != before:
        return "FAIL", "409 之后销售出库单最大主键变了"
    return "PASS", "409（" + message[:60] + "）"


def _drop(env: Env, made: int) -> str:
    status, payload = _send(env, _DELETE, {"type": _KIND, "id": made})
    if status == 409 and "已审核" in str(payload.get("message") or ""):
        _send(env, _VERIFY, {"type": _KIND, "id": made, "action": "unverify"})
        status, payload = _send(env, _DELETE, {"type": _KIND, "id": made})
    if status != 200 or payload.get("deleted") is not True:
        return "删除 " + _brief(status, payload)
    status, _payload = _send(env, _LOAD, {"type": _KIND, "id": made})
    return "" if status == 404 else "删除后仍能读取"


def _head_problem(env: Env, spec: CaseSpec, made: int) -> str:
    row = _cells(env, spec, q.head_sql(str(made)))
    if len(row) < 5:
        return "回读不到新单据的表头"
    want = ["库存", "普通销售", "87", "0", "1"]
    if row[:5] != want:
        return "表头 来源|业务类型|VT_ID|发货单行数|行数 = " + "|".join(row[:5]) + "，期望 " + "|".join(want)
    return ""


def _cycle(env: Env, spec: CaseSpec, made: int) -> str:
    status, payload = _send(env, _LOAD, {"type": _KIND, "id": made})
    if status != 200:
        return "新建后读取 " + _brief(status, payload)
    problem = _head_problem(env, spec, made)
    if problem:
        return problem
    for action in ("verify", "unverify"):
        status, payload = _send(env, _VERIFY, {"type": _KIND, "id": made, "action": action})
        if status != 200:
            return action + " " + _brief(status, payload)
    return ""


def sale_out_nosrc(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    forbid = _forbidden(env, spec)
    cells = _cells(env, spec, q.pick_sql())
    if forbid:
        return _refused(env, spec, _body(cells if len(cells) >= 6 else _DUMMY, env.today))
    if len(cells) < 6:
        return "N/A", "挑不到非货位仓里有可用量的普通存货，或没有客户、末级部门"
    status, payload = _send(env, _CREATE, _body(cells, env.today))
    made = _made_id(payload) if status == 200 else 0
    if not made:
        return "FAIL", "新建 " + _brief(status, payload)
    try:
        problem = _cycle(env, spec, made)
    finally:
        gone = _drop(env, made)
    problem = problem or gone
    return ("FAIL", problem) if problem else ("PASS", f"新建 {made}（仓库 {cells[0]}、存货 {cells[1]}）、审核、弃审、删除")


def _dry_problem(env: Env, spec: CaseSpec, payload: dict, before: int) -> str:
    if payload.get("dry_run") is not True or payload.get("mode") != "rollback":
        return f"期望 rollback 预演，得到 dry_run={payload.get('dry_run')} mode={payload.get('mode')}"
    docs = [doc for doc in payload.get("docs") or [] if isinstance(doc, dict) and doc.get("type") == _KIND]
    if not any(doc.get("state") == "exists" for doc in docs):
        return "预演的 docs 里没有新建的销售出库单"
    head = docs[0].get("head") or {}
    if str(head.get("csource") or "") != "库存":
        return "预演映像的 csource 不是库存：" + str(head.get("csource"))
    if _max_id(env, spec) != before:
        return "预演之后销售出库单最大主键变了（真写了）"
    return ""


def dry_sale_out_nosrc(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    forbid = _forbidden(env, spec)
    cells = _cells(env, spec, q.pick_sql())
    if forbid:
        body = _body(cells if len(cells) >= 6 else _DUMMY, env.today)
        return _refused(env, spec, dict(body, dry_run=True))
    if len(cells) < 6:
        return "N/A", "挑不到非货位仓里有可用量的普通存货，或没有客户、末级部门"
    before = _max_id(env, spec)
    status, payload = _send(env, _CREATE, dict(_body(cells, env.today), dry_run=True))
    if status != 200:
        return "FAIL", "预演新建 " + _brief(status, payload)
    problem = _dry_problem(env, spec, payload, before)
    return ("FAIL", problem) if problem else ("PASS", "预演新建已回滚，没有留下单据")


SALE_OUT_NOSRC_OPS = {
    "sale_out_nosrc": sale_out_nosrc,
    "dry_sale_out_nosrc": dry_sale_out_nosrc,
}

_TAGS = frozenset(("opt:bSAcreat", "new:sale_out", "w:sale_out", "stock"))


def sale_out_nosrc_cases() -> list[CaseSpec]:
    real = Step("custom", _CREATE, (("type", _KIND),), sql_name="sale_out_nosrc", id_from="none")
    dry = Step("custom", _CREATE, (("type", _KIND),), sql_name="dry_sale_out_nosrc", id_from="none")
    return [
        CaseSpec(
            "sale-out-nosrc",
            "无来源销售出库单：销售管理已启用时 409，未启用时新建（来源库存）、审核、弃审、删除",
            (), (real,), True, optional=True, tags=_TAGS, routes=(_CREATE, _LOAD, _VERIFY, _DELETE),
        ),
        CaseSpec(
            "dry-sale-out-nosrc",
            "预演：无来源销售出库单新建，回滚后没有新单据（销售管理已启用时 409）",
            (), (dry,), True, optional=True, tags=_TAGS, routes=(_CREATE,),
        ),
    ]
