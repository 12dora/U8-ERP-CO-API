"""客户信用报表在「信用余额控制用余额表」（销售选项 SA / bUseBanlaceTable）翻转后的口径。

report-customer-credit-flip：先读一遍 reports/customer_credit（第一页，最多 200 个客户），核对 balance_table 与选项一致；
  用例里把选项翻过来（1↔0、True↔False，空按关闭改成 True），再读一遍：balance_table 跟着翻，check_point、formula、
  credit_control 不变，每个客户的 order、dispatch、invoice、ar、expense、used、available、credit_line、controlled 与翻转前
  逐项相同——桥只实现余额表打开时的口径（api-reference「customer_credit」：关闭时 U8 另有一套算法），选项只回显。
  U8 关闭余额表时自己的信用余额表另算，与这里的数差多少要在 U8 客户端对照，本用例不能代替。
恢复：按原值改回选项并回读核对。选项行不是恰好一行、没有客户记 N/A。改的是销售选项（U8 保存订单、发货单时的信用检查
也读它），独占。步骤 kind 为 "custom"，sql_name 是操作名（CREDIT_FLIP_OPS 经 flip 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.credit_flip_sql import option_set_sql, option_sql
from co.e2e.casespec.flip_util import Fail, ok, row, rowcount, with_restore

ROUTE = "/v1/reports/customer_credit"
LIMIT = 200
_HEAD = ("check_point", "formula", "credit_control", "ar_enabled")
_ITEM = ("code", "controlled", "credit_line", "order", "dispatch", "invoice", "ar", "expense", "used", "available")


def is_on(value: str) -> bool:
    """同桥 GlSql.Bit：1 或 true（不分大小写）为打开。"""
    text = value.strip()
    return text == "1" or text.lower() == "true"


def flipped(value: str) -> str:
    text = value.strip()
    if text in ("0", "1"):
        return "0" if text == "1" else "1"
    return "False" if is_on(text) else "True"


def _items(payload: dict) -> list[dict]:
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise Fail("items 不是对象列表")
    return items


def snapshot(payload: dict) -> list[tuple[str, ...]]:
    return [tuple(repr(item.get(key)) for key in _ITEM) for item in _items(payload)]


def diff_text(before: dict, after: dict) -> str:
    """两次报表的差别（表头几项、每个客户的各项）；相同时为空串。"""
    for key in _HEAD:
        if before.get(key) != after.get(key):
            return f"{key} 翻转前 {before.get(key)!r}，翻转后 {after.get(key)!r}"
    old, new = snapshot(before), snapshot(after)
    if len(old) != len(new):
        return f"客户数翻转前 {len(old)}，翻转后 {len(new)}"
    for left, right in zip(old, new):
        if left != right:
            return f"客户 {left[0]} 翻转前 {left}，翻转后 {right}"[:240]
    return ""


def _report(env: Env) -> dict:
    return ok(env, ROUTE, {"limit": LIMIT}, "客户信用报表")


def _restore(env: Env, spec: CaseSpec, old: str, new: str) -> str:
    done = rowcount(env, spec, option_set_sql(old, new))
    got = row(env, spec, option_sql())
    if got != ["1", old]:
        return f"bUseBanlaceTable 应改回 {old or '空'}，实际 {got}（改动 {done} 行）"
    return ""


def credit_flip(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    opt = row(env, spec, option_sql())
    if len(opt) != 2 or opt[0] != "1":
        return "N/A", "销售选项 bUseBanlaceTable 不是恰好一行，不动选项"
    old = opt[1]
    before = _report(env)
    if not _items(before):
        return "N/A", "本账套没有客户"
    if before.get("balance_table") is not is_on(old):
        return "FAIL", f"选项是 {old or '空'}，balance_table 却是 {before.get('balance_table')!r}"
    new = flipped(old)

    def body() -> tuple[str, str]:
        if rowcount(env, spec, option_set_sql(new, old)) != 1:
            raise Fail(f"临时把 bUseBanlaceTable 从 {old or '空'} 改成 {new} 应改 1 行")
        after = _report(env)
        if after.get("balance_table") is not is_on(new):
            raise Fail(f"选项改成 {new} 之后 balance_table 应为 {is_on(new)}，实际 {after.get('balance_table')!r}")
        problem = diff_text(before, after)
        if problem:
            raise Fail("翻转余额表选项后报表数变了（桥应只回显选项）：" + problem)
        count = len(_items(after))
        return "PASS", f"bUseBanlaceTable {old or '空'}→{new}：balance_table 跟着翻，{count} 个客户各项与 used 不变"

    return with_restore(body, (("恢复 bUseBanlaceTable", lambda: _restore(env, spec, old, new)),))


CREDIT_FLIP_OPS = {"credit_flip": credit_flip}


def credit_flip_cases() -> list[CaseSpec]:
    # 报表用例的约定：wave:2 + rpt:<域>（同 report-customer-credit 的 arap、trace）；改销售选项，另外独占。
    tags = frozenset(("wave:2", "rpt:arap", "rpt:trace"))
    step = Step("custom", sql_name="credit_flip", id_from="none")
    title = "客户信用：临时翻转「信用余额控制用余额表」选项，balance_table 跟着变、各客户各项和 used 不变，选项原样恢复"
    return [CaseSpec("report-customer-credit-flip", title, (), (step,), True, tags=tags, routes=(ROUTE,), exclusive=True)]
