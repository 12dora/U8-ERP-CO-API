"""期初单据在「启用月份已结账」的测试账套上走完整生命周期——用例里临时清掉 GL_mend 的结账标志，跑完一整轮再原样改回。

openings-arap-live-cycle / openings-arap-live-cycle-ap：应收 / 应付启用月份已结账（bflag_AR / bflag_AP=1）时，先预演审核一张
  不存在的期初单据确认是测试账套（403 test_account_only / feature_disabled 记 N/A，这时不动标志），再把启用月份那一行的标志改成 0，跑
  openings_arap.run_cycle（预演新增 → 新增 → 审核 → SQL 回读期初形态 → 弃审 → 删除 → 回读已不存在），最后改回 1。
  应收启用月份未结账时 openings-arap-cycle 已覆盖，记 N/A；应付启用月份未结账时不动标志、直接跑一整轮。
stock-opening-live-cycle：库存期初结存单。先确认是测试账套，读出库存已月结的全部期间（iperiod > 0 且 bflag_ST=1；U8 的
  「库存启用的第一个月已经月结」看任一已月结期间，已实测），逐行清成 0，然后：新增 2 行（应得 2 张单据，单号为
  '-' + (ID - 900000000)，单据日期为库存启用日前一天）→ 审核第一张（dVeriDate = dDate）→ 删除已审核的应 409 →
  弃审 → 两张都删除 → rdrecord34 张数回到原值；最后按同一张清单改回 1。没有已月结期间时不动标志、照样跑一整轮。
  库存未启用、存货核算已期初记账、挑不到两种存货、非测试账套记 N/A。

恢复设计：标志改动只在 _with_flags 里发生——清标志之后的全部步骤包在 try 里，finally 先清理本用例新建的单据（月份还开着，
U8 才让删），再恢复标志；步骤抛出的异常也在这里转成 FAIL，不会跳过恢复。恢复语句只改回清单里的行、核对改动行数，
对不上或出错时整条记 FAIL 并写明，快照还原兜底。运行被中断（Run.stop）后 SQL 通道拒绝再发语句，这时标志留给快照还原。

步骤 kind 为 "custom"，sql_name 是操作名（OPENINGS_LIVE_OPS 并入 ops_run）。三条都独占（exclusive），并带 period:close：
标志清掉期间不会有别的用例同时跑。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.openings_arap import ROUTE, SIDE_TEXT, run_cycle, side_state
from co.e2e.casespec.openings_live_sql import (
    MAX_PERIODS,
    arap_flag_sql,
    opening_doc_sql,
    parse_periods,
    st_closed_sql,
    st_flag_sql,
    stock_pick2_sql,
)
from co.e2e.casespec.stock_opening import CREATE, DELETE, KIND, VERIFY, stock_state
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import tier2_refused

_PROBE_ID = 2147483647
_NOT_TEST_NA = "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only），不动结账标志"
# U8 期初结存单的主键从 900000000 往上编，单号是 '-' + (ID - 900000000)（实测核对过）。
_CODE_BASE = 900000000
_MEMO = "回归期初（临时打开月结）"

Body = Callable[[], tuple[str, str]]


class _Fail(Exception):
    pass


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def _count(env: Env, spec: CaseSpec, query: str) -> int:
    cell = first_cell(run_guarded(env, query, spec.name)).strip()
    return int(cell) if cell.isdigit() else -1


def _attempt(body: Body) -> tuple[str, str]:
    # 步骤里的任何异常都转成 FAIL，保证后面的清理和恢复照常执行。
    try:
        return body()
    except _Fail as exc:
        return "FAIL", str(exc)[:240]
    except Exception as exc:  # 异常类型不定（桥调用、SQL 通道、断言），一律记失败
        return "FAIL", f"{type(exc).__name__}：{exc}"[:240]


def _with_flags(body: Body, cleanup: Callable[[], str], restore: Callable[[], str]) -> tuple[str, str]:
    """跑 body；无论成败都先 cleanup（删本用例的单据）再 restore（改回标志）。两者返回问题说明，空串表示正常。"""
    result: tuple[str, str] = ("FAIL", "步骤没有跑完")
    try:
        result = _attempt(body)
    finally:
        left = _safe(cleanup, "清理单据")
        undo = _safe(restore, "恢复结账标志")
    notes = "；".join(note for note in (left, undo) if note)
    if not notes:
        return result
    head = result[1] + "；" if result[0] == "FAIL" else ""
    return "FAIL", (head + notes)[:240]


def _safe(action: Callable[[], str], what: str) -> str:
    try:
        return action()
    except Exception as exc:  # 恢复阶段不能再抛，原因写进判定
        return f"{what}出错（留给快照还原）：{type(exc).__name__} {exc}"[:200]


# ---- 应收 / 应付期初单据 ----


def _arap_live(env: Env, spec: CaseSpec, side: str) -> tuple[str, str]:
    name = SIDE_TEXT[side][0]
    start, closed = side_state(env, spec, side)
    if not start:
        return "N/A", f"{name}款管理未启用"
    if closed != "1":
        if side == "ar":
            return "N/A", f"应收启用月份 {start[:7]} 未结账（openings-arap-cycle 覆盖）"
        return run_cycle(env, spec, side, start)
    status, payload = _send(env, ROUTE, {"side": side, "action": "verify", "id": _PROBE_ID, "dry_run": True})
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    year, month = int(start[:4]), int(start[5:7])

    def body() -> tuple[str, str]:
        opened = _count(env, spec, arap_flag_sql(side, year, month, 0))
        if opened != 1:
            raise _Fail(f"临时打开{name}启用月份 {start[:7]} 应改 1 行，实际 {opened}")
        status, detail = run_cycle(env, spec, side, start)
        return status, f"{detail}（启用月份 {start[:7]} 临时打开）"

    def restore() -> str:
        # 进 body 之前标志是 1：不论 body 有没有改成功，都改回 1；已经是 1 时改动 0 行也算对。
        done = _count(env, spec, arap_flag_sql(side, year, month, 1))
        if done not in (0, 1):
            return f"恢复{name}启用月份结账标志：改动行数读不到（{done}）"
        flag = side_state(env, spec, side)[1]
        return "" if flag == "1" else f"恢复之后{name}启用月份 {start[:7]} 的结账标志仍是 {flag or '空'}"

    return _with_flags(body, lambda: "", restore)


def openings_arap_live_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    return _arap_live(env, spec, "ar")


def openings_arap_live_cycle_ap(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    return _arap_live(env, spec, "ap")


# ---- 库存期初结存单 ----


class _Stock:
    """一轮期初结存单：记下新建的单据，失败时逐张弃审、删除。"""

    def __init__(self, env: Env, spec: CaseSpec, day: str) -> None:
        self.env = env
        self.spec = spec
        self.day = day
        self.live: list[int] = []

    def send(self, route: str, body: dict) -> tuple[int, dict]:
        return _send(self.env, route, dict(body, type=KIND))

    def ok(self, route: str, body: dict, what: str) -> dict:
        status, payload = self.send(route, body)
        if status != 200:
            raise _Fail(f"{what}应 200，实际 " + _brief(status, payload))
        return payload

    def doc(self, doc_id: int) -> list[str]:
        cell = first_cell(run_guarded(self.env, opening_doc_sql(doc_id), self.spec.name))
        return [part.strip() for part in cell.split("|")] if cell else []

    def total(self) -> int:
        return stock_state(self.env, self.spec)[3]

    def cleanup(self) -> str:
        left = []
        for doc_id in list(self.live):
            self.send(VERIFY, {"id": doc_id, "action": "unverify"})
            status, payload = self.send(DELETE, {"id": doc_id})
            if status in (200, 404):
                self.live.remove(doc_id)
            else:
                left.append(f"{doc_id}（{_brief(status, payload)}）")
        return "删不掉的期初结存单：" + "，".join(left) if left else ""


def _created_ids(payload: dict) -> list[int]:
    docs = payload.get("docs") if isinstance(payload.get("docs"), list) else []
    ids = [doc.get("id") for doc in docs if isinstance(doc, dict)]
    return [doc_id for doc_id in ids if type(doc_id) is int and doc_id > 0]


def _check_docs(run: _Stock, ids: list[int]) -> None:
    for doc_id in ids:
        row = run.doc(doc_id)
        want = [str(doc_id), "-" + str(doc_id - _CODE_BASE), run.day, "", "1"]
        if row != want:
            raise _Fail(f"期初结存单 {doc_id} 应为 {want}（ID|单号|单据日期|审核日期|期初标志），实际 {row}")


def _stock_steps(run: _Stock, picked: list[tuple[str, str]], before: int) -> tuple[str, str]:
    head = {"cwhcode": picked[0][0], "cmemo": _MEMO}
    lines = [{"cinvcode": inv, "iquantity": 1} for _wh, inv in picked]
    payload = run.ok(CREATE, {"head": head, "lines": lines}, "新增 2 行期初结存单")
    ids = _created_ids(payload)
    run.live.extend(ids)
    if len(ids) != 2 or payload.get("count") != 2 or len(set(ids)) != 2:
        raise _Fail(f"新增 2 行应得 2 张单据，实际 count={payload.get('count')} ids={ids}")
    _check_docs(run, ids)
    if run.total() != before + 2:
        raise _Fail(f"新增后期初结存单张数应为 {before + 2}")
    first = ids[0]
    run.ok(VERIFY, {"id": first, "action": "verify"}, "审核期初结存单")
    row = run.doc(first)
    if len(row) != 5 or row[3] != row[2] or row[2] != run.day:
        raise _Fail(f"审核后审核日期应等于单据日期 {run.day}，实际 {row}")
    status, payload = run.send(DELETE, {"id": first})
    if status != 409:
        raise _Fail("删除已审核的期初结存单应 409，实际 " + _brief(status, payload))
    if not run.doc(first):
        raise _Fail("409 之后单据却不在了")
    run.ok(VERIFY, {"id": first, "action": "unverify"}, "弃审期初结存单")
    for doc_id in ids:
        run.ok(DELETE, {"id": doc_id}, f"删除期初结存单 {doc_id} ")
        run.live.remove(doc_id)
        if run.doc(doc_id):
            raise _Fail(f"删除之后 rdrecord34 里仍有 {doc_id}")
    after = run.total()
    if after != before:
        raise _Fail(f"删除后期初结存单张数应回到 {before}，实际 {after}")
    return "PASS", f"期初结存单 {ids[0]}、{ids[1]}：新增、单号、审核日期、已审核拒删、弃审、删除都对"


def _stock_na(env: Env, spec: CaseSpec) -> tuple[str, str, int]:
    """(N/A 原因, 库存启用日期, 原张数)；原因为空表示可以跑。"""
    start, _closed, ia_posted, before = stock_state(env, spec)
    if not start:
        return "库存管理未启用", start, before
    if ia_posted == "1":
        return "存货核算已期初记账（桥 409，期初结存单不能增删）", start, before
    if before < 0:
        return "读不到期初结存单张数", start, before
    status, payload = _send(env, VERIFY, {"type": KIND, "id": _PROBE_ID, "action": "verify", "dry_run": True})
    if _not_test(status, payload):
        return _NOT_TEST_NA, start, before
    return "", start, before


def _pick2(env: Env, spec: CaseSpec) -> list[tuple[str, str]]:
    found = []
    for cell in sql_cells(run_guarded(env, stock_pick2_sql(), spec.name)):
        wh, _sep, inv = cell.partition("|")
        if wh.strip() and inv.strip():
            found.append((wh.strip(), inv.strip()))
    return found


def st_closed_periods(env: Env, spec: CaseSpec) -> list[tuple[int, int]]:
    """库存已月结的全部期间（iperiod > 0 且 bflag_ST=1）。存货核算期初记账用例（ia_opening）也用。"""
    return parse_periods(sql_cells(run_guarded(env, st_closed_sql(), spec.name)))


def open_st_flags(env: Env, spec: CaseSpec, periods: list[tuple[int, int]]) -> None:
    """把清单里的库存月结标志临时清成 0；改动行数不对时抛 _Fail（由 _with_flags 记 FAIL 并照常恢复）。"""
    if periods:
        opened = _count(env, spec, st_flag_sql(periods, 0))
        if opened != len(periods):
            raise _Fail(f"临时清库存月结标志应改 {len(periods)} 行，实际 {opened}")


def restore_st_flags(env: Env, spec: CaseSpec, periods: list[tuple[int, int]]) -> str:
    """按同一张清单改回 1；返回问题说明，空串表示正常。"""
    if not periods:
        return ""
    done = _count(env, spec, st_flag_sql(periods, 1))
    if done < 0:
        return "恢复库存月结标志：改动行数读不到"
    # 清除失败时部分行仍是 1（改动行数会少）；以改完后清单内全部为 1 为准。
    missing = sorted(set(periods) - set(st_closed_periods(env, spec)))
    return f"恢复库存月结标志后仍有期间未结账：{missing[:6]}" if missing else ""


def stock_opening_live_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    reason, start, before = _stock_na(env, spec)
    if reason:
        return "N/A", reason
    picked = _pick2(env, spec)
    if len(picked) != 2:
        return "N/A", "挑不到不管货位的仓库和两种不管批次、保质期的单计量存货"
    periods = st_closed_periods(env, spec)
    if len(periods) > MAX_PERIODS:
        return "N/A", f"库存已月结的期间有 {len(periods)} 个，超过 {MAX_PERIODS}，不动标志"
    run = _Stock(env, spec, (date.fromisoformat(start) - timedelta(days=1)).isoformat())

    def body() -> tuple[str, str]:
        open_st_flags(env, spec, periods)
        status, detail = _stock_steps(run, picked, before)
        return status, f"{detail}（临时清 {len(periods)} 个月结标志）"

    return _with_flags(body, run.cleanup, lambda: restore_st_flags(env, spec, periods))


OPENINGS_LIVE_OPS = {
    "openings_arap_live_cycle": openings_arap_live_cycle,
    "openings_arap_live_cycle_ap": openings_arap_live_cycle_ap,
    "stock_opening_live_cycle": stock_opening_live_cycle,
}


def _case(name: str, title: str, op: str, tags: frozenset[str], routes: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=tags, routes=routes, exclusive=True)


def openings_live_cases() -> list[CaseSpec]:
    # 临时改 GL_mend 的结账标志：独占，并带 period:close（与月末结账、其余期初用例同一张表）。
    ar = frozenset(("new:ar_bill", "w:ar_bill", "period:close"))
    ap = frozenset(("new:ap_bill", "w:ap_bill", "period:close"))
    stock = frozenset(("new:stock_opening", "w:stock_opening", "stock", "period:close"))
    return [
        _case("openings-arap-live-cycle",
              "应收启用月份已结账时临时打开：期初应收单新增、审核、期初形态、弃审、删除，结账标志原样恢复（未结账、非测试账套 N/A）",
              "openings_arap_live_cycle", ar, (ROUTE,)),
        _case("openings-arap-live-cycle-ap",
              "期初应付单新增、审核、期初形态、弃审、删除；启用月份已结账时临时打开并原样恢复（非测试账套 N/A）",
              "openings_arap_live_cycle_ap", ap, (ROUTE,)),
        _case("stock-opening-live-cycle",
              "库存已月结时临时清标志：期初结存单新增 2 行、审核、已审核拒删、弃审、删除，张数回原值，标志原样恢复（非测试账套 N/A）",
              "stock_opening_live_cycle", stock, (CREATE, VERIFY, DELETE)),
    ]
