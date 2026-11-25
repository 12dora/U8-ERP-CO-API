"""价格表报表（reports/price_list）的列，用造出来的价格行逐列核对（价格表可能是空的）。

report-price-list-live：挑编码最小的客户（连同客户分类）、可销售又可采购的存货、供应商和本位币，用 SQL 往三张价格表插带标记
  （cMemo = MARK）的行（price_list_sql）：客户价格「本客户有效」「客户分类有效」「本客户已失效」，存货价格「有效」「已失效」，
  供应商存货价格「有效」「已失效」。再按报表读：
  - customer（customer=客户、inv=存货，缺省 as_of 登录日）：两条有效行都在（按客户查连同所属分类的行），失效行不在；
    customer、customer_class、currency、start_date、end_date、min_qty、tax_included、promotion、memo、invalid、quote、
    discount_rate、price、min_price 与插入的值一致；all_dates 时失效行也在、end_date 是昨天。
  - inventory：有效行在、失效行不在，levels 只列有价的级别（1 级 10 / 11.3，3 级只有无税 9）。
  - vendor（vendor=供应商、inv=存货）：有效行的 vendor、price、tax_price、tax_rate、min_qty、max_qty、tax_included、
    supply_type 一致；all_dates 时失效行也在。
恢复：按标记删掉三张表里本用例的行，回读剩 0 行。插入出错时（例如表结构与这里假设的列不同）整批回滚，记 FAIL 并带上 SQL 原文。
挑不全记 N/A。步骤 kind 为 "custom"，sql_name 是操作名（PRICE_LIST_OPS 经 flip 并入 ops_run）。
"""

from __future__ import annotations

from datetime import date

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.flip_util import Fail, ok, row, rows, same_num, with_restore
from co.e2e.casespec.price_list_sql import (
    MARK,
    customer_insert_sql,
    delete_sql,
    ids_sql,
    inventory_insert_sql,
    pick_sql,
    vendor_insert_sql,
)

ROUTE = "/v1/reports/price_list"


def shift(today: str, days: int) -> str:
    return date.fromordinal(date.fromisoformat(today).toordinal() + days).isoformat()


def field_problem(item: dict, want: dict) -> str:
    """item 的各项与 want 一致（数值按 same_num，其余相等）；返回第一处不同，没有时为空串。"""
    for key, value in want.items():
        got = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            good = same_num(got, value)
        else:
            good = got == value
        if not good:
            return f"{key} 应为 {value!r}，实际 {got!r}"
    return ""


def levels_problem(levels: object) -> str:
    want = [(1, 10, 11.3), (3, 9, None)]
    if not isinstance(levels, list) or len(levels) != len(want):
        return f"levels 应为 1 级、3 级两项，实际 {levels!r}"[:200]
    for got, (level, price, tax) in zip(levels, want):
        if not isinstance(got, dict) or got.get("level") != level or not same_num(got.get("price"), price) \
                or not same_num(got.get("tax_price"), tax):
            return f"levels 第 {level} 级应为 {price} / {tax}，实际 {got!r}"[:200]
    return ""


class _Run:
    def __init__(self, env: Env, spec: CaseSpec, picked: list[str]) -> None:
        self.env = env
        self.spec = spec
        self.cus, self.cls, self.inv, self.ven, self.home = picked
        self.today = env.today
        # kind → {价格: 主键}
        self.ids: dict[str, dict[str, int]] = {}

    def report(self, body: dict) -> dict[int, dict]:
        payload = ok(self.env, ROUTE, dict(body, limit=1000), "价格表 " + body["kind"])
        items = payload.get("items")
        if not isinstance(items, list) or payload.get("kind") != body["kind"]:
            raise Fail(f"价格表 {body['kind']} 的 kind 或 items 不对")
        return {item.get("id"): item for item in items if isinstance(item, dict)}

    def id_of(self, kind: str, price: str) -> int:
        found = self.ids.get(kind, {}).get(price)
        if found is None:
            raise Fail(f"插入后回读不到 {kind} 价格 {price} 的行：{self.ids}")
        return found

    def insert(self) -> None:
        left = row(self.env, self.spec, delete_sql())
        if len(left) != 2 or left[1] != "0":
            raise Fail(f"清掉上次残留的标记行后应剩 0 行，实际 {left}")
        for kind, query in (
            ("客户", customer_insert_sql(self.cus, self.cls, self.inv, self.today)),
            ("存货", inventory_insert_sql(self.inv, self.today)),
            ("供应商存货", vendor_insert_sql(self.ven, self.inv, self.today)),
        ):
            got = row(self.env, self.spec, query)
            if not got or got[0] != "OK":
                raise Fail(f"插入{kind}价格行失败（已回滚）：{'|'.join(got[1:]) or '没有结果'}"[:240])
        for cells in rows(self.env, self.spec, ids_sql()):
            if len(cells) == 3 and cells[1].isdigit():
                price = cells[2].rstrip("0").rstrip(".") if "." in cells[2] else cells[2]
                self.ids.setdefault(cells[0], {})[price] = int(cells[1])

    def cleanup(self) -> str:
        got = row(self.env, self.spec, delete_sql())
        return "" if len(got) == 2 and got[1] == "0" else f"按标记删价格行后应剩 0 行，实际 {got}"


def _need(found: dict[int, dict], doc_id: int, what: str) -> dict:
    if doc_id not in found:
        raise Fail(f"{what}（id {doc_id}）不在报表里")
    return found[doc_id]


def _customer(run: _Run) -> None:
    common = {"inv_code": run.inv, "currency": run.home, "start_date": shift(run.today, -10), "end_date": None,
              "memo": MARK, "invalid": False}
    found = run.report({"kind": "customer", "customer": run.cus, "inv": run.inv})
    mine = _need(found, run.id_of("customer", "114"), "本客户有效价格")
    want = dict(common, customer=run.cus, customer_class=None, min_qty=2, tax_included=True, promotion=False,
                quote=120, discount_rate=95, price=114, min_price=100)
    problem = field_problem(mine, want)
    if not problem and run.cls:
        cls_row = _need(found, run.id_of("customer", "110"), "客户分类有效价格")
        problem = field_problem(cls_row, dict(common, customer=None, customer_class=run.cls, tax_included=False,
                                                       promotion=True, quote=115, discount_rate=96, price=110, min_price=None))
    expired = run.id_of("customer", "99")
    if not problem and expired in found:
        problem = "已失效的客户价格出现在缺省 as_of 的报表里"
    if problem:
        raise Fail("客户价格表：" + problem)
    old = _need(run.report({"kind": "customer", "customer": run.cus, "inv": run.inv, "all_dates": True}), expired, "all_dates 的失效价格")
    problem = field_problem(old, {"end_date": shift(run.today, -1), "start_date": shift(run.today, -30), "price": 99})
    if problem:
        raise Fail("客户价格表 all_dates：" + problem)


def _inventory(run: _Run) -> None:
    found = run.report({"kind": "inventory", "inv": run.inv})
    mine = _need(found, run.id_of("inventory", "10"), "存货有效价格")
    want = {"inv_code": run.inv, "currency": run.home, "start_date": shift(run.today, -10), "end_date": None,
            "min_qty": 1, "tax_included": False, "promotion": False, "memo": MARK, "invalid": False}
    problem = field_problem(mine, want) or levels_problem(mine.get("levels"))
    if not problem and run.id_of("inventory", "8") in found:
        problem = "已失效的存货价格出现在缺省 as_of 的报表里"
    if problem:
        raise Fail("存货价格表：" + problem)


def _vendor(run: _Run) -> None:
    ask = {"kind": "vendor", "vendor": run.ven, "inv": run.inv}
    found = run.report(ask)
    mine = _need(found, run.id_of("vendor", "50"), "供应商有效价格")
    want = {"inv_code": run.inv, "vendor": run.ven, "currency": run.home, "start_date": shift(run.today, -10),
            "end_date": None, "min_qty": 1, "max_qty": 100, "tax_included": True, "price": 50, "tax_price": 56.5,
            "tax_rate": 13, "supply_type": 0, "promotion": False, "memo": MARK}
    problem = field_problem(mine, want)
    expired = run.id_of("vendor", "40")
    if not problem and expired in found:
        problem = "已失效的供应商价格出现在缺省 as_of 的报表里"
    if not problem and expired not in run.report(dict(ask, all_dates=True)):
        problem = "all_dates 时失效的供应商价格不在报表里"
    if problem:
        raise Fail("供应商存货价格表：" + problem)


def price_list_live(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    picked = row(env, spec, pick_sql())
    if len(picked) != 5 or not all(picked[i] for i in (0, 2, 3, 4)):
        return "N/A", "挑不到客户、可销售可采购的存货、供应商或本位币"
    run = _Run(env, spec, picked)

    def body() -> tuple[str, str]:
        run.insert()
        _customer(run)
        _inventory(run)
        _vendor(run)
        scope = "含客户分类行" if run.cls else "客户没有分类，不测分类行"
        return "PASS", f"客户 {run.cus}、存货 {run.inv}、供应商 {run.ven}：三种价格表各列与插入值一致，失效行按日期过滤（{scope}）"

    return with_restore(body, (("删除造的价格行", run.cleanup),))


PRICE_LIST_OPS = {"price_list_live": price_list_live}


def price_list_cases() -> list[CaseSpec]:
    # 报表用例的约定：wave:2 + rpt:price；自己写三张价格表，另带 price:rows（彼此互斥）。
    tags = frozenset(("wave:2", "rpt:price", "price:rows"))
    step = Step("custom", sql_name="price_list_live", id_from="none")
    title = "价格表：临时插入客户、存货、供应商价格行，报表各列与插入值一致、失效行按日期过滤，结束删掉"
    return [CaseSpec("report-price-list-live", title, (), (step,), True, tags=tags, routes=(ROUTE,))]
