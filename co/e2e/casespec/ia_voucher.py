"""存货核算制单、取消制单（ia/voucher、ia/voucher/delete，第二级写入）的真实写入。只对桥 config.json 的 testAccounts 里的
账套开放（回归在造数通道的测试账套上跑），其余账套 403 test_account_only（总开关未打开时 feature_disabled），记 N/A。

ia-voucher-post-cycle：本年本月（存货核算未结账、还没有明细账）正式记账（on_uncosted=skip）→ 挑最多 3 张已记账、
  有成本的入库单据（采购入库、其他入库、产成品入库）→ 预演逐张制单：200 validate，计划的每张凭证借贷平衡、回读不变 →
  逐张制单 → 每张凭证：总账行都是制单系统 IA、外部业务类型「存货核算」、借贷平衡、未记账，存货明细账带本号的行数等于
  响应的单据行数 → 经 gl/vouchers/load 读第一张凭证，行数相同 → 再制单 409 → 逐张取消制单 → 总账、存货明细账不再引用
  → 恢复记账，明细账行数、已记账单据行、结账标志回到用例之前。
  另挑一张销售出库单 32（销售成本按销售出库单核算的账套）和一对调拨 / 形态转换（只给其他出库单 09，另一边由桥补齐），
  各自预演、制单、回读（含来源单据表头凭证号；成对的凭证要含 08 和 09 两张单据），随后一起取消制单；没有可制单的记 N/A
  写进说明，环境原因被拒同样记进说明、不影响其他部分。
  本月已结账、已有明细账（恢复记账整月撤销，还原不到用例之前）、没有可记账的单据、没有可制单的入库单据、
  已知的环境原因（接口暂不支持、未启用、已结账、科目没有设置）记 N/A。
失败时按回读还原：取消已生成的凭证，再按 live_close 的做法取消期末处理、恢复记账；还原不了的写进说明并记 FAIL。
改整个账套的存货核算状态，独占且排在登记表最后。步骤 kind 为 "custom"，sql_name 是操作名（IA_VOUCHER_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec import ia_voucher_sql as q
from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.live_close import _ERRORS, POST_ROUTE, _IaRun, _ia_restore, _refused_for, _today
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import tier2_refused

VOUCHER_ROUTE = "/v1/ia/voucher"
DELETE_ROUTE = "/v1/ia/voucher/delete"
LOAD_ROUTE = "/v1/gl/vouchers/load"
_NOT_TEST_NA = "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only）"
# 记 N/A 的已知环境原因（桥的中文 message 片段）：账套设置不支持、未启用、已结账、存货核算科目没有设置。
_ENV = ("接口暂不支持", "启用", "已结账", "没有设置")


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _cells(run: _IaRun, query: str) -> list[str]:
    return sql_cells(run_guarded(run.env, query, run.spec.name))


def _money(value: object) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"金额无法识别：{value}") from exc


# 主流程之外单独制单的单据类型：销售出库单 32、成对业务（只给 09）。
_EXTRA = {"32": "销售出库 32", "09": "调拨 / 形态转换"}


def split_ids(ids: list[dict]) -> tuple[list[dict], list[dict]]:
    """bills_sql 挑出的单据 → （入库单据，32 与成对业务各自单独制单的）。"""
    return [i for i in ids if i["type"] not in _EXTRA], [i for i in ids if i["type"] in _EXTRA]


def coverage(notes: dict[str, str]) -> str:
    return "".join(f"；{label}：{notes.get(t, 'N/A（本月没有可制单的单据）')}" for t, label in _EXTRA.items())


def pair_problem(item: dict, voucher: dict) -> str:
    """成对业务只给了 09：凭证里要有同业务的 08 和 09 两张单据。"""
    if item["type"] != "09":
        return ""
    types = sorted({str(bill.get("type")) for bill in voucher.get("bills") or [] if isinstance(bill, dict)})
    return "" if types == ["08", "09"] else f"成对制单的凭证 {voucher.get('pz_id')} 单据类型 {types}，应为 08 和 09"


def pick_ids(cells: list[str]) -> list[dict]:
    """bills_sql 的行 → ia/voucher 的 ids。"""
    ids = []
    for cell in cells:
        parts = cell.split("|")
        if len(parts) != q.BILL_COLS or not parts[1].strip().isdigit():
            raise ValueError("挑单据回读不对：" + cell[:60])
        ids.append({"type": parts[0].strip(), "id": int(parts[1])})
    return ids


def plan_problem(payload: dict) -> str:
    """预演响应：validate 模式、detail.vouchers 非空、每张凭证借贷平衡。"""
    shape = (payload.get("dry_run"), payload.get("mode"), payload.get("action"))
    if shape != (True, "validate", "ia_voucher"):
        return f"预演响应应 dry_run 为 true、mode 为 validate、action 为 ia_voucher：{shape}"
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    vouchers = detail.get("vouchers")
    if not isinstance(vouchers, list) or not vouchers:
        return "预演响应没有 detail.vouchers"
    for item in vouchers:
        lines = item.get("lines") if isinstance(item, dict) and isinstance(item.get("lines"), list) else []
        debit = sum(_money(line.get("debit")) for line in lines if isinstance(line, dict))
        credit = sum(_money(line.get("credit")) for line in lines if isinstance(line, dict))
        if len(lines) < 2 or debit != credit:
            return f"计划的凭证 {item.get('pz_id') if isinstance(item, dict) else ''} 行数 {len(lines)}、借 {debit} 贷 {credit}"
    return ""


def marks_problem(cell: str, voucher: dict) -> str:
    """marks_sql 的一行与响应的一张凭证比：全部行都是 IA / 存货核算、借贷平衡、一张凭证、未记账、明细账行数相同，
    写表头凭证号的单据（HEAD_TABLES 的类型）表头都带本号。"""
    parts = cell.split("|")
    if len(parts) != 8:
        return "凭证回读不对：" + cell[:60]
    n, marked, debit, credit, keys, subs, unposted, heads = parts
    bills = [bill for bill in voucher.get("bills") or [] if isinstance(bill, dict)]
    rows = sum(int(bill.get("rows") or 0) for bill in bills)
    marked_heads = sum(1 for bill in bills if bill.get("type") in q.HEAD_TABLES)
    lines = voucher.get("lines") if isinstance(voucher.get("lines"), list) else []
    ok = n == marked == unposted == str(len(lines)) and keys == "1" and _money(debit) == _money(credit) and subs == str(rows)
    ok = ok and heads == str(marked_heads)
    return "" if ok and n != "0" else f"凭证 {voucher.get('pz_id')} 回读 {cell}，响应 {len(lines)} 行、单据 {rows} 行"


def _cycle(run: _IaRun, made: list[str]) -> tuple[str, str]:
    status, payload = run.send(POST_ROUTE, "post", on_uncosted="skip")
    if tier2_refused(status, payload):
        return "N/A", _NOT_TEST_NA
    if _refused_for(status, payload, _ENV):
        return "N/A", ("账套不满足记账条件：" + str(payload.get("message")))[:240]
    if status != 200:
        return "FAIL", "正式记账应 200，实际 " + _brief(status, payload)
    ids, extra = split_ids(pick_ids(_cells(run, q.bills_sql(run.year, run.month))))
    stop = _plan(run, ids)
    if stop is not None:
        return stop
    status, payload = _send(run.env, VOUCHER_ROUTE, {"ids": ids})
    if status != 200:
        return "FAIL", "制单应 200，实际 " + _brief(status, payload)
    vouchers = [item for item in payload.get("vouchers") or [] if isinstance(item, dict)]
    made += [q.pz_id(item.get("pz_id")) for item in vouchers]
    problem = _check(run, ids, vouchers)
    notes: dict[str, str] = {}
    for item in extra:
        if problem:
            break
        problem, notes[item["type"]] = _extra(run, item, made)
    problem = problem or _drop(run, made)
    return ("FAIL", problem[:240]) if problem else ("PASS", coverage(notes))


def _marks(run: _IaRun, voucher: dict) -> str:
    return marks_problem(first_cell(run_guarded(run.env, q.marks_sql(voucher["pz_id"]), run.spec.name)), voucher)


def _extra(run: _IaRun, item: dict, made: list[str]) -> tuple[str, str]:
    """32 或成对业务单独预演、制单、回读；返回（问题，说明）。环境原因被拒记 N/A 说明。"""
    label = _EXTRA[item["type"]]
    status, payload = _send(run.env, VOUCHER_ROUTE, {"ids": [item], "dry_run": True})
    if _refused_for(status, payload, _ENV):
        return "", ("N/A（" + str(payload.get("message")))[:80] + "）"
    problem = _brief(status, payload) if status != 200 else plan_problem(payload)
    if problem:
        return f"{label} 预演制单：{problem}", ""
    status, payload = _send(run.env, VOUCHER_ROUTE, {"ids": [item]})
    if status != 200:
        return f"{label} 制单应 200，实际 {_brief(status, payload)}", ""
    vouchers = [one for one in payload.get("vouchers") or [] if isinstance(one, dict)]
    made += [q.pz_id(one.get("pz_id")) for one in vouchers]
    if len(vouchers) != 1:
        return f"{label} 制单应 1 张凭证，实际 {len(vouchers)} 张", ""
    return _marks(run, vouchers[0]) or pair_problem(item, vouchers[0]), "已测"


def _plan(run: _IaRun, ids: list[dict]) -> tuple[str, str] | None:
    """预演逐张制单；能继续返回 None。"""
    if not ids:
        return "N/A", f"{run.year} 年 {run.month} 期记账后没有可制单的入库单据"
    status, payload = _send(run.env, VOUCHER_ROUTE, {"ids": ids, "dry_run": True})
    if _refused_for(status, payload, _ENV):
        return "N/A", ("账套不满足制单条件：" + str(payload.get("message")))[:240]
    problem = _brief(status, payload) if status != 200 else plan_problem(payload)
    return ("FAIL", ("预演制单：" + problem)[:240]) if problem else None


def _check(run: _IaRun, ids: list[dict], vouchers: list[dict]) -> str:
    if len(vouchers) != len(ids):
        return f"逐张制单 {len(ids)} 张单据，响应 {len(vouchers)} 张凭证"
    for voucher in vouchers:
        problem = _marks(run, voucher)
        if problem:
            return problem
    first = vouchers[0]
    key = {"period": first.get("period"), "sign": first.get("sign"), "no": first.get("no")}
    status, loaded = _send(run.env, LOAD_ROUTE, key)
    lines = loaded.get("lines") if isinstance(loaded.get("lines"), list) else None
    if status != 200 or lines is None or len(lines) != len(first.get("lines") or []):
        return f"gl/vouchers/load 读第一张凭证：{_brief(status, loaded)}"
    status, again = _send(run.env, VOUCHER_ROUTE, {"ids": ids[:1]})
    if (status, again.get("code")) != (409, "state_mismatch"):
        return "再制单应 409 state_mismatch，实际 " + _brief(status, again)
    return ""


def _drop(run: _IaRun, made: list[str]) -> str:
    """逐张取消制单，回读不再引用；全部取消后 made 清空。"""
    while made:
        pz = made[-1]
        status, payload = _send(run.env, DELETE_ROUTE, {"pz_id": pz})
        if status != 200:
            return f"取消制单 {pz}：{_brief(status, payload)}"
        left = first_cell(run_guarded(run.env, q.left_sql(pz), run.spec.name))
        if left != "0":
            return f"取消制单 {pz} 后仍有 {left} 行引用"
        made.pop()
    return ""


def _undo(run: _IaRun, made: list[str], verdict: tuple[str, str]) -> tuple[str, str]:
    """取消还留着的凭证，再按 live_close 恢复记账。"""
    note = _drop(run, made) if made else ""
    if note:
        verdict = ("FAIL", (verdict[1] + "；还原时" + note + "，留给快照还原")[:240])
    if verdict[0] == "PASS":
        status, payload = run.send(POST_ROUTE, "unpost")
        if status != 200 or not run.same():
            return "FAIL", ("制单、取消制单通过，但恢复记账后没回到用例之前：" + _brief(status, payload))[:240]
        return verdict
    return _ia_restore(run, verdict)


def ia_voucher_post_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    year, month = _today(env)
    run = _IaRun(env, spec, year, month)
    try:
        run.before = run.state()
        run.posted = run.extra()[0]
    except _ERRORS as exc:
        return "FAIL", str(exc)[:200]
    if run.before[3] == 1:
        return "N/A", f"{year} 年 {month} 期存货核算已结账"
    if run.before[0] > 0:
        return "N/A", f"{year} 年 {month} 期已有 {run.before[0]} 行存货明细账：恢复记账整月撤销，还原不到用例之前"
    made: list[str] = []
    try:
        verdict = _cycle(run, made)
    except _ERRORS as exc:
        verdict = ("FAIL", str(exc)[:200])
    if verdict[0] == "PASS":
        verdict = ("PASS", f"{year} 年 {month} 期：记账、预演制单、逐张制单（回读、gl/vouchers/load、再制单 409）、取消制单、恢复记账"
                   + verdict[1])
    if verdict[0] == "N/A" and not made and run.same():
        return verdict
    try:
        return _undo(run, made, verdict)
    except _ERRORS as exc:
        return "FAIL", (verdict[1] + "；还原时出错：" + str(exc)[:80])[:240]


IA_VOUCHER_OPS = {"ia_voucher_post_cycle": ia_voucher_post_cycle}


def ia_voucher_cases() -> list[CaseSpec]:
    # 改整个账套的存货核算状态、生成总账凭证：与存货核算、月末结账、期初记账用例错开，并且独占。
    tags = frozenset(("ia:post", "period:close", "opening:pu", "gl", "new:gl:转"))
    return [
        CaseSpec(
            "ia-voucher-post-cycle",
            "本月记账后入库单据逐张制单、读凭证、再制单 409，销售出库 32、调拨成对制单（没有时 N/A），取消制单、恢复记账，"
            "回读回到用例之前（已有明细账、非测试账套 N/A）",
            (),
            (Step("custom", sql_name="ia_voucher_post_cycle", id_from="none"),),
            True,
            tags=tags,
            routes=(POST_ROUTE, VOUCHER_ROUTE, DELETE_ROUTE, LOAD_ROUTE),
            exclusive=True,
        )
    ]
