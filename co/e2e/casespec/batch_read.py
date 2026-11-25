"""批量读取用例。只读，不改账套；每项都和逐个读取（vouchers/load、archives/get）的正文对照（去掉 ok）。

batch-load-sql：走 SQL 读取的类型（采购发票，没有就产品检验单、生产订单）列表里取至多 2 张，加一个不存在的 id：
  items 按请求顺序，前几项与单张读取相同，不存在的是 {id, error.code=not_found}；21 个 id 400 field=ids。
  三种都没有单据时记 N/A。
batch-load-com：逐张用 COM 读取的销售订单取 2 张加一个不存在的 id，同上；6 个 id（超过 COM 类型的 5 张）400 field=ids。
archive-get-many：客户档案取 2 个编码加一个不存在的编码：前两项与 archives/get 相同，不存在的是 {code, error.code=not_found}；
  21 个编码 400 field=codes。
"""

from __future__ import annotations

import os

from co.e2e.casespec.assertutil import status_problem
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.meta_batch_judge import batch_problem, item_ids

LOAD_MANY = "/v1/vouchers/load_many"
GET_MANY = "/v1/archives/get_many"
_LOAD = "/v1/vouchers/load"
_GET = "/v1/archives/get"
_LIST = "/v1/vouchers/list"
_ARC_LIST = "/v1/archives/list"
SQL_KINDS = ("purchase_invoice", "qm_product_check", "production_order")
_COM_KIND = "sale_order"
# 不存在的单据 id：在 int32 之内、远大于测试账套的主键。
MISSING_ID = 2147480001


def _ok(env: Env, route: str, body: dict) -> dict:
    status, payload = send(env, route, body)
    if status != 200:
        raise ValueError(f"{route} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _refused(env: Env, route: str, body: dict, field: str) -> str:
    status, payload = send(env, route, body)
    bad = status_problem(status, payload, 400, "bad_request")
    if bad:
        return f"{route} 期望 400：{bad}"[:200]
    return "" if payload.get("field") == field else f"{route} field={payload.get('field')!r}，期望 {field}"


def _ids(env: Env, kind: str) -> list[int]:
    return [doc_id for doc_id in item_ids(_ok(env, _LIST, {"type": kind, "limit": 2})) if isinstance(doc_id, int)]


def _load_problem(env: Env, kind: str, ids: list[int]) -> str:
    singles: list[tuple[object, object]] = [(doc_id, _ok(env, _LOAD, {"type": kind, "id": doc_id})) for doc_id in ids]
    singles.append((MISSING_ID, None))
    payload = _ok(env, LOAD_MANY, {"type": kind, "ids": [key for key, _body in singles]})
    if payload.get("type") != kind:
        return f"type={payload.get('type')}，期望 {kind}"
    return batch_problem(payload.get("items"), singles, "id")


def sql_kind(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    for kind in SQL_KINDS:
        ids = _ids(env, kind)
        if ids:
            problem = _load_problem(env, kind, ids)
            problem = problem or _refused(env, LOAD_MANY, {"type": kind, "ids": list(range(1, 22))}, "ids")
            return ("FAIL", problem[:240]) if problem else ("PASS", f"{kind} {ids} + 不存在的 id")
    return "N/A", "本账套没有采购发票、产品检验单、生产订单"


def com_kind(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    ids = _ids(env, _COM_KIND)
    if len(ids) < 2:
        return "N/A", "本账套没有两张销售订单"
    problem = _load_problem(env, _COM_KIND, ids)
    six = ids + [MISSING_ID + offset for offset in range(4)]
    problem = problem or _refused(env, LOAD_MANY, {"type": _COM_KIND, "ids": six}, "ids")
    return ("FAIL", problem[:240]) if problem else ("PASS", f"{ids} + 不存在的 id，6 张 400")


def archives(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _ok(env, _ARC_LIST, {"archive": "customer", "limit": 2}).get("items") or []
    codes = [str(row.get("code")) for row in rows if isinstance(row, dict) and row.get("code")]
    if not codes:
        return "N/A", "本账套没有客户档案"
    singles: list[tuple[object, object]] = [(code, _ok(env, _GET, {"archive": "customer", "code": code})) for code in codes]
    singles.append(("ZZE2E" + os.urandom(4).hex(), None))
    payload = _ok(env, GET_MANY, {"archive": "customer", "codes": [key for key, _body in singles]})
    problem = "" if payload.get("archive") == "customer" else f"archive={payload.get('archive')}"
    problem = problem or batch_problem(payload.get("items"), singles, "code")
    many = [f"ZZE2E{index:02d}" for index in range(21)]
    problem = problem or _refused(env, GET_MANY, {"archive": "customer", "codes": many}, "codes")
    return ("FAIL", problem[:240]) if problem else ("PASS", f"{codes} + 不存在的编码")


MB_BATCH_OPS = {"mb_lm_sql": sql_kind, "mb_lm_com": com_kind, "mb_gm_arc": archives}


def _one(name: str, route: str, op: str, title: str, tags: tuple[str, ...]) -> CaseSpec:
    reads = (_GET, _ARC_LIST) if route == GET_MANY else (_LOAD, _LIST)
    step = Step("custom", route, sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=frozenset(tags), routes=(route, *reads))


def batch_cases() -> list[CaseSpec]:
    sql_tags = tuple("list:" + kind for kind in SQL_KINDS)
    return [
        _one("batch-load-sql", LOAD_MANY, "mb_lm_sql", "批量读单（SQL 类型）：逐项与单张读取相同，不存在的 id 单项 not_found，21 个 400",
             sql_tags),
        _one("batch-load-com", LOAD_MANY, "mb_lm_com", "批量读单（COM 类型，销售订单）：逐项与单张读取相同，不存在的 id 单项 not_found，6 张 400",
             ("list:" + _COM_KIND,)),
        _one("archive-get-many", GET_MANY, "mb_gm_arc", "批量读档案：客户逐项与 archives/get 相同，不存在的编码单项 not_found，21 个 400",
             ("arc-read:customer",)),
    ]
