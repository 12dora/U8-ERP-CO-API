"""期初余额报表与附件列表的只读用例。不改库。

步骤 kind 为 "custom"，sql_name 是操作名（OPEN_RPT_OPS 并入 ops_run）。
- 期初余额：库存（按仓库、存货、批号）、应收 / 应付（按往来单位、科目）、总账（按科目带试算平衡，另按客户辅助项）。
  核对公共字段（start_date、posted、opening_year）、行数不超过 limit、余额方向与借贷差一致、翻页不重叠；
  总账取启用年度时末级科目期初试算必须平衡。
- 凭证附件：从凭证列表挑一张，列附件（GL_AccAttachs 为空时空列表也算通过），附单据数回显为整数。
- 单据附件：挑一张销售订单列附件；不存在的 id 必须 404 not_found。
挑不到数据记 N/A。
"""

from __future__ import annotations

import re
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.readonly import _ask
from co.e2e.casespec.steps import auth_fields, invoke

LIMIT = 5
_OPEN = "/v1/reports/opening_balance"
_GL_ATT = "/v1/gl/vouchers/attachments/list"
_ATT = "/v1/vouchers/attachments/list"
_CENT = Decimal("0.01")
_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _dec(value: object) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("items 不是对象列表")
    return rows


def _common_problem(payload: dict, module: str) -> str:
    start = payload.get("start_date")
    if payload.get("module") != module or type(payload.get("posted")) is not bool:
        return "module 回显或 posted 不对"
    if start is not None and not _YMD.match(str(start)):
        return f"start_date 格式不对：{start}"
    if type(payload.get("opening_year")) is not int:
        return "opening_year 不是整数"
    return "" if len(_items(payload)) <= LIMIT else "行数超过 limit"


def _balance_problem(row: dict, debit: str, credit: str) -> str:
    net = _dec(row.get(debit)) - _dec(row.get(credit))
    shown = _dec(row.get("balance_debit")) - _dec(row.get("balance_credit"))
    want = "借" if net > 0 else ("贷" if net < 0 else "平")
    if abs(net - shown) > _CENT or row.get("balance_dir") != want:
        return f"{row.get('partner_code')} 余额方向或金额与借贷差不一致"
    return ""


def _second_page(env: Env, body: dict, payload: dict) -> str:
    rows = _items(payload)
    if not payload.get("next") or not rows:
        return ""
    more = _items(_ask(env, _OPEN, dict(body, after=payload["next"])))
    if more and more[0] == rows[-1]:
        return "第二页与第一页重叠"
    return ""


def opening_stock(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    body = {"module": "stock", "limit": LIMIT}
    payload = _ask(env, _OPEN, body)
    problem = _common_problem(payload, "stock") or _second_page(env, body, payload)
    rows = _items(payload)
    if not problem and any(row.get("lines", 0) < 1 or not row.get("wh_code") for row in rows):
        problem = "有行缺仓库或 lines 小于 1"
    if problem:
        return "FAIL", problem
    if not rows:
        return "N/A", "本账套没有库存期初"
    return "PASS", f"{len(rows)} 行，posted={payload.get('posted')}"


def opening_arap(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    counts = []
    for side in ("ar", "ap"):
        body = {"module": "arap", "side": side, "limit": LIMIT}
        payload = _ask(env, _OPEN, body)
        problem = _common_problem(payload, "arap") or _second_page(env, body, payload)
        if not problem and payload.get("side") != side:
            problem = "side 回显不对"
        for row in _items(payload):
            problem = problem or _balance_problem(row, "debit", "credit")
        if problem:
            return "FAIL", f"{side}：{problem}"
        counts.append(len(_items(payload)))
    if not any(counts):
        return "N/A", "本账套没有应收应付期初"
    return "PASS", "应收、应付 " + "/".join(str(n) for n in counts) + " 行"


def _trial_problem(payload: dict) -> str:
    trial = payload.get("trial")
    if not isinstance(trial, dict):
        return "第一页没有 trial"
    if trial.get("balanced") is not True or abs(_dec(trial.get("debit")) - _dec(trial.get("credit"))) > _CENT:
        return f"期初试算不平：借 {trial.get('debit')} 贷 {trial.get('credit')}"
    return ""


def opening_gl(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    first = _ask(env, _OPEN, {"module": "gl", "limit": LIMIT})
    problem = _common_problem(first, "gl")
    start = first.get("start_date")
    if problem or start is None:
        return ("FAIL", problem) if problem else ("N/A", "总账没有启用日期")
    year = int(str(start)[:4])
    payload = _ask(env, _OPEN, {"module": "gl", "fiscal_year": year, "leaf_only": True, "limit": LIMIT})
    problem = _common_problem(payload, "gl") or _trial_problem(payload)
    if not problem and payload.get("period") != int(str(start)[5:7]):
        problem = f"启用年度的期初期间应为启用月份，实际 {payload.get('period')}"
    aux = _ask(env, _OPEN, {"module": "gl", "fiscal_year": year, "dim": "customer", "limit": LIMIT})
    problem = problem or _common_problem(aux, "gl")
    if not problem and (aux.get("dim") != "customer" or aux.get("trial") is not None):
        problem = "辅助项期初的 dim 回显或 trial 不对"
    if problem:
        return "FAIL", problem
    return "PASS", f"{year} 年第 {payload.get('period')} 期，试算借 {payload['trial'].get('debit')}"


def gl_attachments(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    period = int(env.today[5:7])
    listed = _items(_ask(env, "/v1/gl/vouchers/list", {"period_from": 1, "period_to": period, "limit": 1}))
    if not listed:
        return "N/A", "测试账套本年没有凭证"
    key = {name: listed[0].get(name) for name in ("period", "sign", "no")}
    payload = _ask(env, _GL_ATT, key)
    voucher = payload.get("voucher")
    if not isinstance(voucher, dict) or type(voucher.get("attachments")) is not int:
        return "FAIL", "voucher.attachments 不是整数"
    if any(voucher.get(name) != value for name, value in key.items()):
        return "FAIL", "凭证键回显不对"
    rows = _items(payload)
    if any(type(row.get("id")) is not int for row in rows) or type(payload.get("truncated")) is not bool:
        return "FAIL", "附件行缺 id 或 truncated 不是布尔"
    return "PASS", f"{key['period']}-{key['sign']}-{key['no']} 附件 {len(rows)} 个，附单据数 {voucher['attachments']}"


def voucher_attachments(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    listed = _items(_ask(env, "/v1/vouchers/list", {"type": "sale_order", "keys_only": True, "limit": 1}))
    if not listed:
        return "N/A", "本账套没有销售订单"
    doc = int(listed[0]["id"])
    payload = _ask(env, _ATT, {"type": "sale_order", "id": doc})
    rows = _items(payload)
    if payload.get("id") != doc or any(row.get("stored") not in ("database", "file_server") for row in rows):
        return "FAIL", "id 回显或 stored 不对"
    fields = auth_fields(env)
    fields.update({"type": "sale_order", "id": 2147483647})
    status, missing = invoke(env.client, _ATT, fields)
    if status != 404 or missing.get("code") != "not_found":
        return "FAIL", f"不存在的单据应 404 not_found，实际 {status} {missing.get('code')}"
    return "PASS", f"销售订单 {doc} 附件 {len(rows)} 个"


OPEN_RPT_OPS = {
    "opening_stock": opening_stock,
    "opening_arap": opening_arap,
    "opening_gl": opening_gl,
    "gl_attachments": gl_attachments,
    "voucher_attachments": voucher_attachments,
}

# （用例名, 操作, 标题, 路由, 标签）。报表类带 wave:2 与对应的 rpt:<域>；附件列表只读。
_CASES = (
    ("report-opening-stock", "opening_stock", "报表：库存期初（公共字段、翻页）", (_OPEN,),
     frozenset({"wave:2", "rpt:stock"})),
    ("report-opening-arap", "opening_arap", "报表：应收应付期初（余额方向与借贷差一致）", (_OPEN,),
     frozenset({"wave:2", "rpt:arap"})),
    ("report-opening-gl", "opening_gl", "报表：总账期初余额（启用年度试算平衡、辅助项）", (_OPEN,),
     frozenset({"wave:2", "rpt:gl"})),
    ("gl-attachments", "gl_attachments", "总账凭证附件列表", ("/v1/gl/vouchers/list", _GL_ATT),
     frozenset({"wave:2", "rpt:gl"})),
    ("list-attachments-sale-order", "voucher_attachments", "单据附件列表（销售订单；不存在的 id 404）",
     ("/v1/vouchers/list", _ATT), frozenset({"list:sale_order"})),
)


def open_rpt_cases() -> list[CaseSpec]:
    rows = []
    for name, op, title, routes, tags in _CASES:
        step = Step("custom", sql_name=op, id_from="none")
        rows.append(CaseSpec(name, title, (), (step,), True, tags=tags, routes=routes))
    return rows
