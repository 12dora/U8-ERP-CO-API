"""单据搜索（vouchers/search）用例名 `vouchers-search`。只读，不改账套；标签 list:sale_order 让新建、改销售订单的用例不同时跑。

以 vouchers/list 为准（同一过滤条件、同一 limit，第一页主键和有无下一页都相同）：
  pick：列表里挑一张有客户、单号、日期的销售订单（没有两张以上销售订单记 N/A）；
  客户 partner ↔ 列表 cus_code，日期 date_from/date_to ↔ 列表同名条件，verified true / false ↔ 列表同名条件；
    客户那一页每行 cus_code 都是它，搜索的行 = 列表的行 + partner_name；
  单号包含 code_like：每行单号都含该片段，从它前一个 id 起取 1 行正是它；
  存货 inventory：取该单第一行的存货（SQL），搜索结果都在 SQL 查到的含该存货的订单里，从它前一个 id 起取 1 行正是它；
  翻页：limit 1 → next 等于这一行的 id，after=next 再取 1 行，两行与列表 limit 2 相同；
  调拨单没有客户列：partner 400 field=partner；销售订单表头没有仓库：warehouse 400 field=warehouse。
"""

from __future__ import annotations

import re

from co.e2e.casespec.assertutil import sql_cells, status_problem
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_judge import middle_piece
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.meta_batch_judge import item_ids, page_problem, same_as_list
from co.e2e.casespec.sqlexec import run_guarded

ROUTE = "/v1/vouchers/search"
_LIST = "/v1/vouchers/list"
_KIND = "sale_order"
_PAGE = 50
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INV = re.compile(r"^[0-9A-Za-z._-]{1,20}\Z")
_KEYS = ("mbs_id", "mbs_code", "mbs_cus", "mbs_date")


def _call(env: Env, route: str, body: dict) -> dict:
    status, payload = send(env, route, dict(body, type=body.get("type", _KIND)))
    if status != 200 or not isinstance(payload.get("items"), list):
        raise ValueError(f"{route} {body} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _search(env: Env, **body: object) -> dict:
    return _call(env, ROUTE, body)


def _list(env: Env, limit: int, filters: dict | None = None) -> dict:
    body: dict = {"limit": limit}
    if filters:
        body["filter"] = filters
    return _call(env, _LIST, body)


def _usable(row: object) -> bool:
    return isinstance(row, dict) and all(str(row.get(key) or "") for key in ("id", "code", "cus_code", "doc_date"))


def pick(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _list(env, _PAGE)["items"]
    target = next((row for row in rows if _usable(row)), None)
    if len(rows) < 2 or target is None:
        return "N/A", "本账套没有两张以上带客户的销售订单"
    values = (target["id"], target["code"], target["cus_code"], str(target["doc_date"])[:10])
    env.bag.update({key: str(value) for key, value in zip(_KEYS, values)})
    return "PASS", f"{target['id']} {target['code']}"


def _filter_problem(env: Env, search: dict, listed: dict, label: str) -> tuple[str, dict, dict]:
    got, want = _search(env, limit=_PAGE, **search), _list(env, _PAGE, listed)
    return page_problem(got, want, label), got, want


def filters(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    cus, day = env.bag["mbs_cus"], env.bag["mbs_date"]
    problem, got, want = _filter_problem(env, {"partner": cus}, {"cus_code": cus}, "partner")
    if not problem and any(row.get("cus_code") != cus for row in got["items"]):
        problem = "partner 的结果里有别的客户"
    if not problem:
        problem = same_as_list(got["items"][0], want["items"][0]) if got["items"] else "partner 查不到这张单"
    pairs = (
        ({"date_from": day, "date_to": day}, {"date_from": day, "date_to": day}, "date"),
        ({"verified": True}, {"verified": True}, "verified=true"),
        ({"verified": False}, {"verified": False}, "verified=false"),
    )
    for search, listed, label in pairs:
        problem = problem or _filter_problem(env, search, listed, label)[0]
    return ("FAIL", problem[:240]) if problem else ("PASS", "partner、date、verified 与列表一致")


def _anchored(env: Env, **body: object) -> str:
    doc_id = int(env.bag["mbs_id"])
    ids = item_ids(_search(env, after=doc_id - 1, limit=1, **body))
    return "" if ids == [doc_id] else f"{body} 从 {doc_id - 1} 起取 1 行得 {ids}，期望 [{doc_id}]"


def code_like(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    code = env.bag["mbs_code"]
    piece = middle_piece(code)[:40] or code  # 单号不足 4 个字时用整个单号
    rows = _search(env, code_like=piece, limit=200)["items"]
    stray = [row.get("code") for row in rows if piece.casefold() not in str(row.get("code") or "").casefold()]
    problem = f"code_like={piece} 的结果里有 {stray[:3]}" if stray else _anchored(env, code_like=piece)
    return ("FAIL", problem[:240]) if problem else ("PASS", f"code_like={piece} {len(rows)} 行")


def inv_sql(doc_id: str) -> str:
    if _ID.fullmatch(doc_id or "") is None:
        raise ValueError("销售订单 id 不合法")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP 1 N'ROW:' + d.cInvCode AS v\nFROM dbo.SO_SODetails AS d\n"
        f"WHERE d.ID = {doc_id} AND d.cInvCode NOT LIKE N'%[^0-9A-Za-z._-]%'\nORDER BY d.iSOsID;\n"
    )


def holders_sql(inv: str, top_id: int) -> str:
    """含该存货、主键不大于 top_id 的销售订单主键（搜索结果必须都在里面；权限只会让搜索更少）。"""
    if _INV.fullmatch(inv or "") is None or not 0 < top_id <= 2147483647:
        raise ValueError("存货编码或主键不合法")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), h.ID) AS v\nFROM dbo.SO_SOMain AS h\n"
        f"WHERE h.ID <= {top_id} AND EXISTS (SELECT 1 FROM dbo.SO_SODetails AS d WHERE d.ID = h.ID AND d.cInvCode = N'{inv}')\n"
        "ORDER BY h.ID;\n"
    )


def inventory(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    cells = sql_cells(run_guarded(env, inv_sql(env.bag["mbs_id"]), spec.name))
    inv = cells[0].strip() if cells else ""
    if _INV.fullmatch(inv) is None:
        return "PASS", "该单第一行的存货编码含特殊字符或读不到，inventory 未测"
    ids = item_ids(_search(env, inventory=inv, limit=200))
    held = {int(cell) for cell in sql_cells(run_guarded(env, holders_sql(inv, max(ids or [1])), spec.name)) if cell.isdigit()}
    stray = [doc_id for doc_id in ids if doc_id not in held]
    problem = f"inventory={inv} 的结果里有不含它的订单 {stray[:3]}" if stray else _anchored(env, inventory=inv)
    return ("FAIL", problem[:240]) if problem else ("PASS", f"inventory={inv} {len(ids)} 行")


def paging(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    first = _search(env, limit=1)
    ids = item_ids(first)
    if len(ids) != 1 or first.get("next") != ids[0]:
        return "FAIL", f"limit 1 得 {ids} next={first.get('next')}"
    second = item_ids(_search(env, limit=1, after=first["next"]))
    want = item_ids(_list(env, 2))
    if ids + second != want:
        return "FAIL", f"两页 {ids + second}，列表 limit 2 是 {want}"
    return "PASS", f"{ids + second}"


def refused(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    for body, field in (({"type": "transfer", "partner": "X"}, "partner"), ({"type": _KIND, "warehouse": "01"}, "warehouse")):
        status, payload = send(env, ROUTE, body)
        bad = status_problem(status, payload, 400, "bad_request")
        if bad or payload.get("field") != field:
            return "FAIL", f"{body} 得 {bad or payload.get('field')!r}，期望 400 field={field}"[:240]
    return "PASS", "400 partner、warehouse"


MB_SEARCH_OPS = {
    "mb_se_pick": pick,
    "mb_se_filters": filters,
    "mb_se_code": code_like,
    "mb_se_inventory": inventory,
    "mb_se_paging": paging,
    "mb_se_refused": refused,
}


def search_case() -> CaseSpec:
    steps = tuple(Step("custom", ROUTE, sql_name=op, id_from="none") for op in MB_SEARCH_OPS)
    title = "单据搜索：客户、日期、审核、单号包含、存货各自与列表 / SQL 一致，翻页，不支持的条件 400"
    tags = frozenset(("list:" + _KIND,))
    return CaseSpec("vouchers-search", title, (), steps, True, tags=tags, routes=(ROUTE, _LIST))
