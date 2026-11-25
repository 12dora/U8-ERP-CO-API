"""参照合并检验的产品检验单（QM04，BMERGECHECKFLAG=1）生成产成品入库。

product-in-merged-check：挑一张可入库的合并检验产品检验单里的一个来源（qm_merged_in_sql.merged_pick_sql）→
  vouchers/load 带 merge_sources、含该来源（剩余 >= 1、订单行对得上）→ 用别的检验单的来源 AUTOID 生单被拒（400 / 409）→
  只对这一个来源生成产成品入库 1 个（不碰另一个来源，免得订单行满额被 U8 关闭）→ 回读表体（1 行、imergecheckautoid
  是来源 AUTOID、iMPoIds / cmocode 是该来源的订单行、iCheckIdBaks 是检验单、表头 cMPoCode 为空）、来源累计入库数、
  检验单累计入库数、订单行合格入库量各 +1 → 审核 → 弃审 → 删除 → 单据不在、三处都回到生成前。

挑不到数据记 N/A（只有启用了合并检验的账套才有）；清理按建的倒序（_guard），清理失败记 FAIL。
步骤 kind 为 "custom"，sql_name 是操作名（QM_MERGED_OPS 并入 ops_run）。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus import _dec, _Fail, _guard, _Run
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.qm_merged_in_sql import gone_sql, levels_sql, line_sql, merged_pick_sql
from co.e2e.testdata import PRODUCT_IN_RD
from co.e2e.testdata import value as td

_GEN = "/v1/vouchers/generate"
_LOAD = "/v1/vouchers/load"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_MEMO = "CO桥回归"
_LABELS = ("来源累计入库数", "检验单累计入库数", "订单行合格入库量")


class _Pick:
    def __init__(self, parts: list[str]) -> None:
        self.check, self.autoid, self.modid = (safe_id(part) for part in parts[:3])
        self.mocode = safe_lot(parts[3])
        self.wh = safe_lot(parts[4])
        self.other = safe_id(parts[5])


def _levels(run: _Run, pick: _Pick) -> tuple[Decimal, ...]:
    parts = run.cell(levels_sql(pick.check, pick.autoid, pick.modid)).split("|")
    if len(parts) != 3:
        raise _Fail("回读累计数不是 3 段：" + "|".join(parts))
    return tuple(_dec(part) for part in parts)


def _level_problem(label: str, got: tuple[Decimal, ...], base: tuple[Decimal, ...], delta: int) -> str:
    for name, now, was in zip(_LABELS, got, base, strict=True):
        if now != was + delta:
            return f"{label}{name}应为 {was + delta}，实际 {now}"
    return ""


def _load_problem(run: _Run, pick: _Pick) -> str:
    payload = run.ok(_LOAD, {"type": "qm_product_check", "id": int(pick.check)}, "读取合并检验的产品检验单")
    sources = payload.get("merge_sources")
    if not isinstance(sources, list) or not sources:
        return f"检验单 {pick.check} 读取结果没有 merge_sources"
    mine = [item for item in sources if isinstance(item, dict) and item.get("source_line_id") == int(pick.autoid)]
    if len(mine) != 1:
        return f"merge_sources 里没有来源 {pick.autoid}"
    item = mine[0]
    if item.get("mo_detail_id") != int(pick.modid) or float(item.get("remaining") or 0) < 1 or item.get("done"):
        return f"merge_sources 来源 {pick.autoid} 不符：{item}"[:200]
    return ""


def _body(run: _Run, pick: _Pick, autoid: str) -> dict:
    head = {"cwhcode": pick.wh, "crdcode": td(PRODUCT_IN_RD), "ddate": run.env.today, "cmemo": _MEMO}
    line = {"source_line_id": int(autoid), "quantity": 1}
    return {"type": "product_in", "id": int(pick.check), "source_type": "qm_product_check", "head": head, "lines": [line]}


def _refuse_other(run: _Run, pick: _Pick) -> None:
    status, payload = run.send(_GEN, _body(run, pick, pick.other))
    if status == 200:
        doc = int(payload.get("id") or 0)
        if doc:
            run.drop("product_in", doc)
        raise _Fail(f"用别的检验单的来源 {pick.other} 生单没有被拒绝")
    if status not in (400, 409):
        raise _Fail(f"用别的检验单的来源生单应 400 / 409，实际 {status} {payload.get('message')}"[:240])


def _line_problem(got: str, pick: _Pick) -> str:
    want = f"1|{pick.autoid}|{pick.modid}|{pick.mocode}|{pick.check}|"
    if got != want:
        return f"入库单应为 1 行、来源 {pick.autoid}、订单行 {pick.modid}、表头无订单号（{want}），实际 {got or '空'}"
    return ""


def _case_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(merged_pick_sql(), 6)
    if parts is None:
        return "N/A", "测试账套挑不到可入库的合并检验产品检验单（来源剩余 >= 1、订单行已审核且入库 1 个后不满额）"
    pick = _Pick(parts)
    loaded = _load_problem(run, pick)
    if loaded:
        raise _Fail(loaded)
    base = _levels(run, pick)
    _refuse_other(run, pick)
    doc = run.made(_GEN, _body(run, pick, pick.autoid), "参照合并检验的产品检验单生成产成品入库")
    run.drop("product_in", doc)
    line = run.cell(line_sql(str(doc)))
    made = _levels(run, pick)
    run.ok(_VERIFY, {"type": "product_in", "id": doc, "action": "verify"}, "审核产成品入库")
    run.unverify("product_in", doc)
    # 先弃审、删除（记下的两个清理动作），再判回读，断言失败也不会把单据留在库里。
    run.settle(2)
    gone = run.cell(gone_sql(str(doc)))
    problem = (
        _line_problem(line, pick)
        or _level_problem("生成后", made, base, 1)
        or ("" if gone == "0" else f"删除后入库单 {doc} 仍在")
        or _level_problem("删除后", _levels(run, pick), base, 0)
    )
    if problem:
        raise _Fail(problem)
    return "PASS", f"检验单 {pick.check} 来源 {pick.autoid} → 产成品入库 {doc}：按来源回写三处累计数，审核弃审删除后全部回退"


def product_in_merged_check(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _case_body)


QM_MERGED_OPS = {"product_in_merged_check": product_in_merged_check}


def qm_merged_cases() -> list[CaseSpec]:
    # 动现存量（stock）；回写检验单、订单行（w:）。收发类别取测试数据，缺键时用例记 SKIP。
    step = Step("custom", sql_name="product_in_merged_check", id_from="none")
    title = "参照合并检验的产品检验单按来源生成产成品入库：imergecheckautoid、来源 / 检验单 / 订单行累计数，删除后回退"
    tags = frozenset(("new:product_in", "w:product_in", "w:qm_product_check", "w:production_order", "stock"))
    spec = CaseSpec("product-in-merged-check", title, (), (step,), True, tags=tags,
                    routes=(_LOAD, _GEN, _VERIFY, _DELETE))
    return [replace(spec, data=(PRODUCT_IN_RD,))]
