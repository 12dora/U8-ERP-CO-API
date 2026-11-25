"""采购结算单（purchase_settle）只读用例，名 `purchase-settle-read`。不改账套。

挑最新一张有发票行的结算单（pu_settle_read_sql.settle_pick_sql），然后：
  vouchers/load：code、state（verified 为 false、行数、已记账行数）、表体行数，挑中的结算行带对得上的
    入库单 in_id 和发票 invoice_id；
  vouchers/list 按结算号：正好一行，id、供应商、verified、line_count、accounted_lines 对得上，ufts 是十进制数；
  keys_only + changed_since：水位减一能看到这张单，等于水位时看不到（增量按 psufts / psdufts）；
  vouchers/search 按结算号、供应商、存货能找到它；
  vouchers/verify 400（结算单没有审核，桥在登录 U8 之前拒绝）。

挑不到数据记 N/A（空账套没有结算单）。步骤 kind 为 "custom"，sql_name 是操作名（PU_SETTLE_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _Run
from co.e2e.casespec.pu_settle_read_sql import settle_pick_sql

KIND = "purchase_settle"
_LOAD = "/v1/vouchers/load"
_LIST = "/v1/vouchers/list"
_SEARCH = "/v1/vouchers/search"
_VERIFY = "/v1/vouchers/verify"
_FALSE = (False, 0, "0")


class _Pick:
    def __init__(self, parts: list[str]) -> None:
        self.doc, self.code, self.ven, self.lines, self.acc, self.line, self.rd, self.bill, self.inv = parts


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _load_problem(run: _Run, pick: _Pick) -> str:
    payload = run.ok(_LOAD, {"type": KIND, "id": int(pick.doc)}, "读取采购结算单")
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    if _text(payload.get("code")) != pick.code:
        return f"读取的 code 应为 {pick.code}，实际 {payload.get('code')}"
    if state.get("verified") not in _FALSE or _text(state.get("line_count")) != pick.lines:
        return f"state 不符（应未审核、{pick.lines} 行）：{state}"[:240]
    if _text(state.get("accounted_lines")) != pick.acc or len(lines) != int(pick.lines):
        return f"已记账行数应为 {pick.acc}、表体 {pick.lines} 行，实际 {state.get('accounted_lines')}、{len(lines)} 行"
    mine = [line for line in lines if isinstance(line, dict) and _text(line.get("ID")) == pick.line]
    if len(mine) != 1:
        return f"表体里没有结算行 {pick.line}"
    if _text(mine[0].get("in_id")) != pick.rd or _text(mine[0].get("invoice_id")) != pick.bill:
        return f"结算行 {pick.line} 的对照应为入库单 {pick.rd}、发票 {pick.bill}，实际 {mine[0].get('in_id')}、{mine[0].get('invoice_id')}"
    return ""


def _listed(run: _Run, pick: _Pick, extra: dict) -> list[dict]:
    body = {"type": KIND, "filter": {"code": pick.code}, "limit": 10}
    body.update(extra)
    items = run.ok(_LIST, body, "列出采购结算单").get("items")
    if not isinstance(items, list) or not all(isinstance(row, dict) for row in items):
        raise _Fail("列表响应缺 items")
    return items


def _row_problem(row: dict, pick: _Pick) -> str:
    if _text(row.get("id")) != pick.doc or _text(row.get("ven_code")) != pick.ven:
        return f"列表行应为 {pick.doc} / 供应商 {pick.ven}，实际 {row.get('id')} / {row.get('ven_code')}"
    if row.get("verified") not in _FALSE or row.get("verifier") is not None:
        return f"列表行 verified 应为 false、verifier 为 null，实际 {row.get('verified')} / {row.get('verifier')}"
    if _text(row.get("line_count")) != pick.lines or _text(row.get("accounted_lines")) != pick.acc:
        return f"列表行 line_count / accounted_lines 应为 {pick.lines} / {pick.acc}，实际 {row.get('line_count')} / {row.get('accounted_lines')}"
    if not _text(row.get("ufts")).isdigit():
        return f"列表行 ufts 不是十进制数：{row.get('ufts')}"
    return ""


def _list_problem(run: _Run, pick: _Pick) -> tuple[str, str]:
    rows = _listed(run, pick, {})
    if len(rows) != 1:
        return f"按结算号 {pick.code} 列表应为 1 行，实际 {len(rows)} 行", ""
    return _row_problem(rows[0], pick), _text(rows[0].get("ufts"))


def _since_problem(run: _Run, pick: _Pick, ufts: str) -> str:
    before = _listed(run, pick, {"keys_only": True, "changed_since": str(int(ufts) - 1)})
    if [_text(row.get("id")) for row in before] != [pick.doc] or _text(before[0].get("ufts")) != ufts:
        return f"changed_since={int(ufts) - 1} 应只列出 {pick.doc}（ufts {ufts}），实际 {before}"[:240]
    after = _listed(run, pick, {"keys_only": True, "changed_since": ufts})
    return f"changed_since={ufts} 不应再列出，实际 {after}"[:240] if after else ""


def _search_problem(run: _Run, pick: _Pick) -> str:
    body = {"type": KIND, "code_like": pick.code, "partner": pick.ven, "inventory": pick.inv, "limit": 10}
    items = run.ok(_SEARCH, body, "查找采购结算单").get("items")
    ids = [_text(row.get("id")) for row in items if isinstance(row, dict)] if isinstance(items, list) else []
    return "" if pick.doc in ids else f"按结算号、供应商、存货 {pick.inv} 查找没有 {pick.doc}：{ids[:5]}"


def _verify_problem(run: _Run, pick: _Pick) -> str:
    status, payload = run.send(_VERIFY, {"type": KIND, "id": int(pick.doc), "action": "verify"})
    return "" if status == 400 else f"结算单审核应 400，实际 {status} {payload.get('message')}"[:240]


def _case_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(settle_pick_sql(), 9)
    if parts is None:
        return "N/A", "本账套没有带发票行的采购结算单"
    pick = _Pick(parts)
    problem = _load_problem(run, pick)
    ufts = ""
    if not problem:
        problem, ufts = _list_problem(run, pick)
    problem = (
        problem
        or _since_problem(run, pick, ufts)
        or _search_problem(run, pick)
        or _verify_problem(run, pick)
    )
    if problem:
        raise _Fail(problem)
    return "PASS", f"结算单 {pick.doc}（{pick.code}，{pick.lines} 行）：读取对照、列表、增量、查找一致，审核 400"


def pu_settle_read(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _case_body)


PU_SETTLE_OPS = {"pu_settle_read": pu_settle_read}


def pu_settle_cases() -> list[CaseSpec]:
    # 只读；list:purchase_settle 让以后写采购结算单的用例不同时跑。
    step = Step("custom", sql_name="pu_settle_read", id_from="none")
    title = "采购结算单只读：读取的入库 / 发票对照、列表附加列、psufts 增量、查找，审核 400"
    tags = frozenset(("list:" + KIND,))
    return [CaseSpec("purchase-settle-read", title, (), (step,), True, tags=tags, routes=(_LOAD, _LIST, _SEARCH, _VERIFY))]
