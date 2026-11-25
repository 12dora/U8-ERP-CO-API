"""存货核算记账、期末处理与月末结账的真实写入（ia/post、ia/period_end、periods/close）。只对桥 config.json 的
testAccounts 里的账套开放（回归在测试账套上跑），其余账套登录前 403 test_account_only（桥总开关未打开时 feature_disabled），记 N/A。

ia-post-period-end-live：本年本月（存货核算未结账、还没有明细账）正式记账（on_uncosted=skip）→ 明细账行数、
  已记账的单据行都增加，counts.subsidiary_month 与回读一致 → 期末处理 → 汇总表有已期末处理的行、出库都有成本
  → 取消期末处理 → 恢复记账 → 明细账行数、已记账的单据行、期末处理标记、bflag_IA 回到用例之前。
  本月已结账、已有明细账（恢复记账整月撤销，还原不到用例之前）、没有可记账的单据（counts.area 为 0）、
  已知的环境原因（接口暂不支持、未启用、已结账）记 N/A。
periods-unclose-reclose：总账最后一个已结账的期间，按 U8 结账顺序的反向逐个取消结账（总账 → 应收、应付 → 存货核算
  → 库存 → 销售、采购，只取该期已结账、以后没有再结账的模块，有依赖它的模块仍结着账时跳过），再按结账顺序重新结账。
  库存：取消后五张月结快照表该月没有行、stock_rows 等于取消前的行数；重结后 stock_rows 与回读一致，
  结账前有快照时行数与结账前相同。存货核算（该月有数据时）：取消后下月的结转明细行被删掉，重结后汇总表和结转行与结账前相同。
  最后 GL_mend 全部结账标志与用例之前相同。桥按环境原因拒绝取消某个模块（未启用、接口不支持、请在 U8 里处理）时
  跳过它和被它挡住的模块，写进说明；一个模块都没取消时记 N/A。

两条都改整个账套的期间状态（GL_mend、IA_*、ST_MonthAccount*），独占且排在登记表最后。失败时照状态尽力还原：
记账用例按回读取消期末处理、恢复记账；结账用例把已取消的模块按结账顺序重新结账。还原不了的写进说明并记 FAIL
（快照还原兜底）。步骤 kind 为 "custom"，sql_name 是操作名（LIVE_CLOSE_OPS 并入 ops_run）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.periods_close_sql import period_flags_sql
from co.e2e.casespec.ia_post_sql import ia_month_sql
from co.e2e.casespec.live_close_sql import ia_close_sql, ia_extra_sql, stock_month_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import tier2_refused

POST_ROUTE = "/v1/ia/post"
PERIOD_END_ROUTE = "/v1/ia/period_end"
CLOSE_ROUTE = "/v1/periods/close"
_NOT_TEST_NA = "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only）"
_ERRORS = (KeyError, RuntimeError, ValueError)
# 记账被拒时记 N/A 的已知环境原因（桥的中文 message 片段），同 ia_post。
_IA_ENV = ("接口暂不支持", "启用", "已结账")
# 取消结账被拒时按环境原因跳过的 message 片段：模块未启用、接口不支持（含「接口无法自动取消」）、要在 U8 里处理、
# 存货核算下月已有数据（取消会删掉下月的记账）。
_STUCK = ("未启用", "接口", "请在 U8 里", "取消本月结账会删掉")
_NAMES = {"pu": "采购", "sa": "销售", "st": "库存", "ia": "存货核算", "ar": "应收", "ap": "应付", "gl": "总账"}
# GL_mend 一行（periods_close_sql）里各模块标志的下标：年度、期间、采购、销售、库存、存货、应收、应付、总账。
_COLUMN = {"pu": 2, "sa": 3, "st": 4, "ia": 5, "ar": 6, "ap": 7, "gl": 8}
# 同期结账前必须已结账的模块（docs/api-reference.md §31 的顺序闸门）；取消结账反过来，依赖它的模块要先取消。
_NEEDS = {
    "st": ("pu", "sa"),
    "ia": ("pu", "sa", "st"),
    "ar": ("sa",),
    "ap": ("pu",),
    "gl": ("pu", "sa", "st", "ia", "ar", "ap"),
}
REOPEN_ORDER = ("gl", "ar", "ap", "ia", "st", "sa", "pu")
CLOSE_ORDER = ("pu", "sa", "st", "ia", "ar", "ap", "gl")
_STOCK_KEYS = ("account", "accounts", "v", "vs", "check")


def _read(env: Env, spec: CaseSpec, query: str, size: int) -> tuple[int, ...]:
    parts = first_cell(run_guarded(env, query, spec.name)).split("|")
    if len(parts) != size or not all(part.strip().isdigit() for part in parts):
        raise RuntimeError(f"回读格式不对：{parts}"[:120])
    return tuple(int(part) for part in parts)


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _today(env: Env) -> tuple[int, int]:
    text = str(env.today or "")
    return int(text[:4]), int(text[5:7])


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def _refused_for(status: int, payload: dict, texts: tuple[str, ...]) -> bool:
    if status != 409 or payload.get("code") != "state_mismatch":
        return False
    message = str(payload.get("message") or "")
    return any(text in message for text in texts)


# ---- 存货核算：记账 → 期末处理 → 取消期末处理 → 恢复记账 ----


@dataclass
class _IaRun:
    env: Env
    spec: CaseSpec
    year: int
    month: int
    # 用例之前：(明细账行数, 汇总表行数, 已期末处理的汇总行数, 结账标志)，以及全账套已记账的单据行数。
    before: tuple[int, ...] = ()
    posted: int = 0
    out_rows: int = 0

    def state(self) -> tuple[int, ...]:
        return _read(self.env, self.spec, ia_month_sql(self.year, self.month), 4)

    def extra(self) -> tuple[int, ...]:
        return _read(self.env, self.spec, ia_extra_sql(self.year, self.month), 3)

    def send(self, route: str, action: str, **more: object) -> tuple[int, dict]:
        body = {"fiscal_year": self.year, "period": self.month, "action": action}
        body.update(more)
        return _send(self.env, route, body)

    def same(self) -> bool:
        """明细账行数、期末处理标记、结账标志和已记账的单据行都回到用例之前（汇总表 U8 恢复记账时不一定删行，不比）。"""
        now = self.state()
        keep = (self.before[0], self.before[2], self.before[3])
        return (now[0], now[2], now[3]) == keep and self.extra()[0] == self.posted


def ia_post_period_end_live(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
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
    try:
        verdict = _ia_cycle(run)
    except _ERRORS as exc:
        verdict = ("FAIL", str(exc)[:200])
    if verdict[0] == "PASS":
        return verdict
    return _ia_restore(run, verdict)


def _ia_cycle(run: _IaRun) -> tuple[str, str]:
    status, payload = run.send(POST_ROUTE, "post", on_uncosted="skip")
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    if _refused_for(status, payload, _IA_ENV):
        return "N/A", ("账套不满足记账条件：" + str(payload.get("message")))[:240]
    if status != 200:
        return "FAIL", "正式记账应 200，实际 " + _brief(status, payload)
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    if counts.get("area") == 0:
        return "N/A", f"{run.year} 年 {run.month} 期没有需要记账的单据（counts.area 为 0）"
    problem = _posted_problem(run, counts)
    problem = problem or _period_end(run)
    problem = problem or _unpost(run)
    if problem:
        return "FAIL", problem[:240]
    head = f"{run.year} 年 {run.month} 期记账 {counts.get('area')} 行（明细账 {counts.get('subsidiary_month')} 行"
    return "PASS", head + f"，出库 {run.out_rows} 行都有成本），期末处理、取消、恢复记账后回到用例之前"


def _posted_problem(run: _IaRun, counts: dict) -> str:
    rows = run.state()[0]
    posted = run.extra()[0]
    if rows <= run.before[0] or posted <= run.posted:
        return f"记账后明细账 {run.before[0]}→{rows} 行、已记账单据行 {run.posted}→{posted}，应都增加"
    if counts.get("subsidiary_month") != rows:
        return f"counts.subsidiary_month {counts.get('subsidiary_month')} 与回读的明细账 {rows} 行不符"
    return ""


def _period_end(run: _IaRun) -> str:
    status, payload = run.send(PERIOD_END_ROUTE, "run")
    if status != 200:
        return "期末处理应 200，实际 " + _brief(status, payload)
    ended = run.state()[2]
    _posted, run.out_rows, uncosted = run.extra()
    if ended == 0:
        return "期末处理后汇总表没有已期末处理（iPeriod=1）的行"
    if uncosted > 0:
        return f"期末处理后还有 {uncosted} 行出库明细没有成本（iOutCost 为空）"
    status, payload = run.send(PERIOD_END_ROUTE, "cancel")
    if status != 200:
        return "取消期末处理应 200，实际 " + _brief(status, payload)
    if run.state()[2] != 0:
        return "取消期末处理后汇总表仍有已期末处理的行"
    return ""


def _unpost(run: _IaRun) -> str:
    status, payload = run.send(POST_ROUTE, "unpost")
    if status != 200:
        return "恢复记账应 200，实际 " + _brief(status, payload)
    if not run.same():
        return f"恢复记账后明细账、期末处理标记、结账标志或已记账单据行（{run.posted}）没回到用例之前：{run.state()}"
    return ""


def _ia_undo(run: _IaRun) -> tuple[bool, str]:
    """按回读还原：有期末处理就取消，明细账比用例之前多就恢复记账。返回 (是否回到用例之前, 做了什么)。"""
    if run.same():
        return True, ""
    done = []
    if run.state()[2] > 0:
        done.append("取消期末处理 " + str(run.send(PERIOD_END_ROUTE, "cancel")[0]))
    if run.state()[0] != run.before[0]:
        done.append("恢复记账 " + str(run.send(POST_ROUTE, "unpost")[0]))
    return run.same(), "、".join(done)


def _ia_restore(run: _IaRun, verdict: tuple[str, str]) -> tuple[str, str]:
    try:
        back, done = _ia_undo(run)
    except _ERRORS as exc:
        back, done = False, "还原时出错：" + str(exc)[:80]
    if back and not done:
        return verdict
    if back:
        return verdict[0], (verdict[1] + f"；已还原（{done}）")[:240]
    return "FAIL", (verdict[1] + f"；还原后仍与用例之前不同（{done or '没有可做的'}），留给快照还原")[:240]


# ---- 月末结账：取消结账 → 重新结账 ----


def read_flag_rows(env: Env, spec: CaseSpec) -> list[tuple[int, ...]]:
    """GL_mend 第 1 到 12 期，一期一个元组（年度, 期间, 七个标志）；格式不对的行跳过。"""
    rows = []
    for cell in sql_cells(run_guarded(env, period_flags_sql(), spec.name)):
        parts = cell.split("|")
        if len(parts) == 9 and all(part.strip().isdigit() for part in parts):
            rows.append(tuple(int(part) for part in parts))
    return rows


def last_closed(rows: list[tuple[int, ...]]) -> tuple[int, int] | None:
    """总账最后一个已结账的期间 (年度, 期间)；没有为 None。"""
    closed = [(row[0], row[1]) for row in rows if row[_COLUMN["gl"]] == 1]
    return max(closed) if closed else None


def reopen_plan(rows: list[tuple[int, ...]], target: tuple[int, int]) -> tuple[str, ...]:
    """按取消结账的顺序，该期已结账、以后没有再结账、依赖它的模块都会先取消（或没结账）的模块。"""
    row = next(item for item in rows if (item[0], item[1]) == target)
    later = [item for item in rows if (item[0], item[1]) > target]
    closed = {module for module, column in _COLUMN.items() if row[column] == 1}
    plan = []
    for module in REOPEN_ORDER:
        if module not in closed or any(item[_COLUMN[module]] == 1 for item in later):
            continue
        if any(module in _NEEDS.get(other, ()) for other in closed):
            continue
        plan.append(module)
        closed.discard(module)
    return tuple(plan)


@dataclass
class _CloseRun:
    env: Env
    spec: CaseSpec
    year: int
    period: int
    plan: tuple[str, ...]
    flags: list[tuple[int, ...]]
    stock: tuple[int, ...] = ()
    ia: tuple[int, ...] = ()
    reopened: list[str] = field(default_factory=list)
    # 桥按环境原因拒绝取消结账、仍结着账的模块 → 原因。
    stuck: dict[str, str] = field(default_factory=dict)

    def send(self, module: str, action: str) -> tuple[int, dict]:
        body = {"module": module, "action": action, "fiscal_year": self.year, "period": self.period}
        return _send(self.env, CLOSE_ROUTE, body)

    def stock_now(self) -> tuple[int, ...]:
        return _read(self.env, self.spec, stock_month_sql(self.year, self.period), 5)

    def ia_now(self) -> tuple[int, ...]:
        return _read(self.env, self.spec, ia_close_sql(self.year, self.period), 4)

    def head(self, module: str) -> str:
        return f"{_NAMES[module]} {self.year} 年 {self.period} 期"


def periods_unclose_reclose(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    try:
        rows = read_flag_rows(env, spec)
    except _ERRORS as exc:
        return "FAIL", str(exc)[:200]
    target = last_closed(rows)
    if target is None:
        return "N/A", "GL_mend 没有总账已结账的期间"
    run = _CloseRun(env, spec, target[0], target[1], reopen_plan(rows, target), rows)
    try:
        run.stock = run.stock_now()
        run.ia = run.ia_now()
        verdict = _close_cycle(run)
    except _ERRORS as exc:
        verdict = ("FAIL", str(exc)[:200])
    if run.reopened:
        verdict = _reclose_left(run, verdict)
    return verdict


def _close_cycle(run: _CloseRun) -> tuple[str, str]:
    for module in run.plan:
        kind, text = _reopen(run, module)
        if kind != "ok":
            return kind, text
    if not run.reopened:
        return "N/A", (f"{run.year} 年 {run.period} 期没有能取消结账的模块：" + _stuck_note(run))[:240]
    for module in [m for m in CLOSE_ORDER if m in run.reopened]:
        problem = _close(run, module)
        if problem:
            return "FAIL", problem[:240]
    if read_flag_rows(run.env, run.spec) != run.flags:
        return "FAIL", "重新结账后 GL_mend 的结账标志与用例之前不同"
    names = "、".join(_NAMES[module] for module in REOPEN_ORDER if module in run.plan and module not in run.stuck)
    text = f"{run.year} 年 {run.period} 期 {names} 取消结账再结账，标志复原；库存快照 {run.stock_now()}"
    note = _stuck_note(run)
    return "PASS", (text + ("；" + note if note else ""))[:240]


def _stuck_note(run: _CloseRun) -> str:
    return "；".join(f"{_NAMES[module]}跳过（{reason}）" for module, reason in run.stuck.items())


def _blocked_by(run: _CloseRun, module: str) -> str:
    """仍结着账、又依赖本模块的跳过模块；没有为空串。"""
    for other in run.stuck:
        if module in _NEEDS.get(other, ()):
            return other
    return ""


def _reopen(run: _CloseRun, module: str) -> tuple[str, str]:
    """取消一个模块的结账：("ok", "") 继续，("N/A" / "FAIL", 说明) 停下。"""
    blocker = _blocked_by(run, module)
    if blocker:
        run.stuck[module] = f"{_NAMES[blocker]}仍结着账"
        return "ok", ""
    status, payload = run.send(module, "reopen")
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    if _refused_for(status, payload, _STUCK):
        run.stuck[module] = str(payload.get("message"))[:80]
        return "ok", ""
    if status != 200 or payload.get("closed") is not False:
        return "FAIL", run.head(module) + " 取消结账应 200、closed 为 false，实际 " + _brief(status, payload)
    run.reopened.append(module)
    problem = _after_reopen(run, module, payload)
    return ("FAIL", run.head(module) + "：" + problem) if problem else ("ok", "")


def _stock_rows(payload: dict) -> tuple[object, ...] | None:
    rows = payload.get("stock_rows")
    if not isinstance(rows, dict):
        return None
    return tuple(rows.get(key) for key in _STOCK_KEYS)


def _after_reopen(run: _CloseRun, module: str, payload: dict) -> str:
    if module == "st":
        now = run.stock_now()
        if any(now):
            return f"取消结账后五张月结快照表应没有该月的行，实际 {now}"
        if _stock_rows(payload) != run.stock:
            return f"stock_rows 应是删掉的行数 {run.stock}，实际 {payload.get('stock_rows')}"
    if module == "ia" and run.ia[0] > 0 and run.ia_now()[3] != 0:
        return "取消结账后下月仍有结转明细行（cVouType 24），IA_UnClose 应删掉"
    return ""


def _close(run: _CloseRun, module: str) -> str:
    status, payload = run.send(module, "close")
    if status != 200 or payload.get("closed") is not True:
        return run.head(module) + " 重新结账应 200、closed 为 true，实际 " + _brief(status, payload)
    run.reopened.remove(module)
    problem = ""
    if module == "st":
        problem = _stock_close_problem(run, payload)
    if module == "ia":
        problem = _ia_close_problem(run.ia, run.ia_now())
    return run.head(module) + "：" + problem if problem else ""


def _stock_close_problem(run: _CloseRun, payload: dict) -> str:
    now = run.stock_now()
    if _stock_rows(payload) != now:
        return f"stock_rows {payload.get('stock_rows')} 与回读的快照行数 {now} 不符"
    if any(run.stock) and now != run.stock:
        return f"重新结账写的快照行数 {now} 与结账前 {run.stock} 不同"
    return ""


def _ia_close_problem(before: tuple[int, ...], now: tuple[int, ...]) -> str:
    """该月没有数据（只改标志）或结账前就有下月结转行时全部相同；否则只比该月的汇总表。"""
    size = 4 if before[0] == 0 or before[3] > 0 else 2
    if now[:size] != before[:size]:
        return f"重新结账后（该月汇总、已期末处理、下月汇总、下月结转）{now} 与结账前 {before} 不同"
    return ""


def _reclose_left(run: _CloseRun, verdict: tuple[str, str]) -> tuple[str, str]:
    """失败后把已取消结账的模块按结账顺序重新结账；已结账的 409 也算结上了。"""
    done, left = [], []
    for module in [m for m in CLOSE_ORDER if m in run.reopened]:
        try:
            status, payload = run.send(module, "close")
        except _ERRORS:
            status, payload = 0, {}
        if status == 200 or _refused_for(status, payload, ("已结账",)):
            run.reopened.remove(module)
            done.append(_NAMES[module])
        else:
            left.append(_NAMES[module])
    text = verdict[1] + (("；已重新结账：" + "、".join(done)) if done else "")
    if left:
        text += f"；仍未结账：{'、'.join(left)}（{run.year} 年 {run.period} 期，留给快照还原）"
        return "FAIL", text[:240]
    return verdict[0], text[:240]


LIVE_CLOSE_OPS = {
    "ia_post_period_end_live": ia_post_period_end_live,
    "periods_unclose_reclose": periods_unclose_reclose,
}


def live_close_cases() -> list[CaseSpec]:
    # 改整个账套的期间状态：与存货核算、月末结账、期初记账用例错开（同 ia_post 的标签），并且独占。
    tags = frozenset(("ia:post", "period:close", "opening:pu"))
    return [
        CaseSpec(
            "ia-post-period-end-live",
            "本月正式记账、期末处理、取消期末处理、恢复记账，回读回到用例之前（已有明细账、非测试账套 N/A）",
            (),
            (Step("custom", sql_name="ia_post_period_end_live", id_from="none"),),
            True,
            tags=tags,
            routes=(POST_ROUTE, PERIOD_END_ROUTE),
            exclusive=True,
        ),
        CaseSpec(
            "periods-unclose-reclose",
            "总账最后一个已结账期间各模块取消结账再结账：库存快照、存货结转、结账标志复原（非测试账套 N/A）",
            (),
            (Step("custom", sql_name="periods_unclose_reclose", id_from="none"),),
            True,
            tags=tags,
            routes=(CLOSE_ROUTE,),
            exclusive=True,
        ),
    ]
