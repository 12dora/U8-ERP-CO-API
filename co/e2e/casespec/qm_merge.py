"""产品检验单合并检验（vouchers/generate type=qm_product_check + source_ids）。

qm04-merge-create-delete：挑两条同存货、同辅计量单位的生产订单行（qm_merge_sql.pick_sql）→ 各生成一张产品报检单（1 个，
  U8 按选项自动审核；未自动审核记 N/A）→ 只给一条来源 400 → 两条报检行合并检验（检验员、检验方案取挑单）→ 回读：
  BMERGECHECKFLAG=1、合并明细 2 行且都指向这两条报检行、明细合计 = 表头 = 2，两条报检行累计检验各 1、BFLAG=1 →
  vouchers/load 带 2 条 merge_sources → 同样两条再合并 409（已生成过检验单）→ 删除检验单 → 表头、检验项目、
  合并明细都不在，两条报检行累计检验回 0、BFLAG 回 0 → 删除两张报检单（清理）。

模板必输的自定义项照 qm_tpl 补占位；检验单扩展自定义项取最近一张产品检验单的值（同 qm02-create-delete）。
清理按建的倒序（_guard），清理失败记 FAIL。步骤 kind 为 "custom"，sql_name 是操作名（QM_MERGE_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.qm_write_sql import NO_DEFINE, qmp_def_sql
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.qm_merge_sql import gone_sql, line_sql, merged_sql, pick_sql, sources_sql
from co.e2e.casespec.qm_tpl import merge_head, required_defines, stuck_text

_GEN = "/v1/vouchers/generate"
_LOAD = "/v1/vouchers/load"
_DELETE = "/v1/vouchers/delete"
_INSPECT = "qm_product_inspect"
_CHECK = "qm_product_check"


class _Pick:
    def __init__(self, parts: list[str]) -> None:
        self.mo_a, self.line_a, self.mo_b, self.line_b = (safe_id(part) for part in parts[:4])
        self.project = safe_lot(parts[4])
        self.person = safe_lot(parts[5])


def _fill(run: _Run, vt: int) -> dict[str, str]:
    fill, stuck = required_defines(run.env, run.spec, vt)
    if stuck:
        raise _NotApplicable(stuck_text(vt, stuck))
    return fill


def _inspect(run: _Run, mo: str, line: str, fill: dict[str, str]) -> str:
    body = {"type": _INSPECT, "id": int(mo), "source_type": "production_order",
            "lines": [{"source_line_id": int(line), "quantity": 1}]}
    if fill:
        body["head"] = merge_head({}, fill)
    payload = run.ok(_GEN, body, "参照生产订单生成产品报检单")
    doc = payload.get("id")
    if type(doc) is not int or doc <= 0:
        raise _Fail("产品报检单响应没有新单据 id")
    run.drop(_INSPECT, doc)
    if not (payload.get("state") or {}).get("verified"):
        raise _NotApplicable("产品报检单没有按选项自动审核（bProInspectAutoVerify），不能参照生成检验单")
    return run.cell(line_sql(str(doc)))


def _check_head(run: _Run, pick: _Pick, fill: dict[str, str]) -> dict:
    head: dict = {"cCheckPersonCode": pick.person, "project_code": pick.project}
    values = run.cell(qmp_def_sql()).split("|")
    for number, value in zip(("11", "12", "13"), values, strict=False):
        if value and value != NO_DEFINE:
            head["chDefine" + number] = safe_lot(value)
    return merge_head(head, fill)


def _merge_body(head: dict, lines: list[str]) -> dict:
    return {"type": _CHECK, "source_ids": [int(line) for line in lines], "head": head}


def _refused(run: _Run, body: dict, status: int, what: str) -> None:
    got, payload = run.send(_GEN, body)
    if got == 200:
        doc = payload.get("id")
        if type(doc) is int and doc > 0:
            run.drop(_CHECK, doc)
        raise _Fail(what + "没有被拒绝")
    if got != status:
        raise _Fail(f"{what}应 {status}，实际 {got} {payload.get('message')}"[:240])


def _load_problem(run: _Run, check: int) -> str:
    payload = run.ok(_LOAD, {"type": _CHECK, "id": check}, "读取合并检验的产品检验单")
    sources = payload.get("merge_sources")
    if not isinstance(sources, list) or len(sources) != 2:
        return f"检验单 {check} 的 merge_sources 应为 2 项，实际 {sources!r}"[:200]
    return ""


def _case_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(pick_sql(), 6)
    if parts is None:
        return "N/A", "挑不到两条同存货、同辅计量单位、要检验且有剩余的已审核生产订单行（另需检验方案和检验员）"
    pick = _Pick(parts)
    fill_inspect, fill_check = _fill(run, 352), _fill(run, 354)
    lines = [_inspect(run, pick.mo_a, pick.line_a, fill_inspect), _inspect(run, pick.mo_b, pick.line_b, fill_inspect)]
    head = _check_head(run, pick, fill_check)
    _refused(run, _merge_body(head, lines[:1]), 400, "只给一条来源的合并检验")
    check = run.made(_GEN, _merge_body(head, lines), "两条产品报检行合并检验")
    run.drop(_CHECK, check)
    merged = run.cell(merged_sql(str(check), lines[0], lines[1]))
    made = run.cell(sources_sql(lines[0], lines[1]))
    problem = _load_problem(run, check)
    _refused(run, _merge_body(head, lines), 409, "已检验的报检行再合并检验")
    run.settle(1)
    gone = run.cell(gone_sql(str(check)))
    after = run.cell(sources_sql(lines[0], lines[1]))
    problem = (
        ("" if merged == "1|2|2.0000|2.0000|2" else f"合并检验单回读应为 1|2|2.0000|2.0000|2，实际 {merged or '空'}")
        or ("" if made == "1.0000|1|1.0000|1" else f"生成后两条报检行应为 1.0000|1|1.0000|1，实际 {made or '空'}")
        or problem
        or ("" if gone == "0" else f"删除后检验单 {check} 仍剩 {gone} 行")
        or ("" if after == "0.0000|0|0.0000|0" else f"删除后两条报检行应回到 0.0000|0|0.0000|0，实际 {after or '空'}")
    )
    if problem:
        raise _Fail(problem)
    return "PASS", f"报检行 {lines[0]}、{lines[1]} → 合并检验单 {check}：合并明细与来源回写正确，删除后全部回退"


def qm04_merge_create_delete(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _case_body)


QM_MERGE_OPS = {"qm04_merge_create_delete": qm04_merge_create_delete}


def qm_merge_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="qm04_merge_create_delete", id_from="none")
    title = "产品检验单合并检验：两条报检行合成一张检验单，合并明细与来源回写，只给一条 400、重复合并 409，删除后来源回退"
    tags = frozenset(("new:qm_product_inspect", "new:qm_product_check", "w:production_order", "stock"))
    spec = CaseSpec("qm04-merge-create-delete", title, (), (step,), True, tags=tags, routes=(_GEN, _LOAD, _DELETE))
    return [spec]
