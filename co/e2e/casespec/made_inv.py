"""自建档案用例：在快照里经 API 新建存货（按模板复制，再用 SQL 改标志），用完删掉。

purchase-in-shelf-made：账套里没有保质期管理的存货时（purchase-in-shelf 记 N/A），
  按挑到的外购简单存货复制一个新存货，SQL 打开批次、保质期管理（保质期 30 天），再走 purchase-in-shelf 同一套
  无来源采购入库（stock_more._shelf_run）：生产、失效日期照写，保质期取档案，U8 自己补的有效期至、cExpirationdate
  记进判定说明 → 删除入库单 → 删除新存货。「普通业务必有订单」打开时同 purchase-in-shelf 临时关闭、跑完写回。

新建档案的清理（made_guard）：先撤单据（_Run 记下的清理动作），再按建的倒序删档案；档案删不掉（例如 U8 因现存量
记录拒绝删存货）不判失败，写进判定说明、留给快照还原。删存货之前先删掉它结存为 0 的现存量记录（入库单删除后
U8 留下的空行），免得存货删不掉、后面的用例挑到它。挑不到数据记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（MADE_OPS 并入 ops_run）。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _Run
from co.e2e.casespec.stock_more import _shelf_run, _with_havepo
from co.e2e.casespec.made_inv_sql import free_inv_sql, inv_flags_sql, shelf_made_pick_sql, zero_stock_sql

ARC_CREATE = "/v1/archives/create"
ARC_DELETE = "/v1/archives/delete"
_CREATE = "/v1/vouchers/create"
_DELETE = "/v1/vouchers/delete"
_NAME = "purchase-in-shelf-made"
_OP = "purchase_in_shelf_made"
_MASS = "30"
_MASS_UNIT = "3"
# 新存货：批次 + 保质期管理（天），外购、免检、无序列号。
_SHELF_FLAGS = {
    "bInvBatch": 1, "bInvQuality": 1, "iMassDate": int(_MASS), "cMassUnit": int(_MASS_UNIT),
    "bPurchase": 1, "bPropertyCheck": 0, "bSerial": 0,
}

Made = list[tuple[str, str]]


def made_archive(run: _Run, made: Made, archive: str, code: str, fields: dict) -> None:
    """经 archives/create 新建一条档案并记下（清理时倒序删除）。"""
    run.ok(ARC_CREATE, {"archive": archive, "code": code, "fields": fields}, f"新建{archive} {code}")
    made.append((archive, code))


def made_inventory(run: _Run, made: Made, template: str, prefix: str, flags: dict[str, int]) -> str:
    """按模板复制一个新存货（编码 prefix + 4 位随机数，挑账套里还没有的），再用 SQL 改标志。返回新编码。"""
    codes = [prefix + f"{secrets.randbelow(10000):04d}" for _ in range(6)]
    code = run.cell(free_inv_sql(codes))
    if not code:
        raise _Fail("挑不到空闲的存货编码（" + prefix + "xxxx）")
    body = {"archive": "inventory", "code": code, "template": template,
            "fields": {"name": "u8co e2e 自建存货 " + code, "specs": "e2e"}}
    run.ok(ARC_CREATE, body, f"按模板 {template} 新建存货 {code}")
    made.append(("inventory", code))
    if run.cell(inv_flags_sql(code, flags)) != "1":
        raise _Fail(f"新存货 {code} 改标志没改到 1 行")
    return code


def drop_made(run: _Run, made: Made) -> str:
    """倒序删除新建的档案；返回删不掉的说明（全部删掉为空串）。"""
    left = []
    while made:
        archive, code = made.pop()
        cleared = _clear_stock(run, code) if archive == "inventory" else ""
        status, payload = run.send(ARC_DELETE, {"archive": archive, "code": code})
        if status != 200:
            left.append(f"{archive} {code}{cleared} 删除 {status} {str(payload.get('message') or '')[:40]}")
    return ("自建档案未删掉（留给快照还原）：" + "，".join(left)) if left else ""


def _clear_stock(run: _Run, code: str) -> str:
    """删存货前清掉它的零数量现存量记录；返回给删除失败说明用的括注。"""
    try:
        return f"（已清零数量现存量 {run.cell(zero_stock_sql(code)) or '?'} 行）"
    except (RuntimeError, ValueError) as exc:
        return f"（清零数量现存量失败：{str(exc)[:40]}）"


def made_guard(env: Env, spec: CaseSpec, body: Callable[[_Run, Made], tuple[str, str]]) -> tuple[str, str]:
    """同 _guard，另在单据清理之后删掉本用例新建的档案（删不掉只记说明）。"""

    def wrapped(run: _Run) -> tuple[str, str]:
        made: Made = []
        try:
            status, detail = body(run, made)
        except (_Fail, KeyError, RuntimeError, ValueError) as exc:
            left = run.cleanup()
            note = drop_made(run, made)
            raise _Fail("；".join(part for part in (str(exc), "清理失败：" + left if left else "", note) if part)) from exc
        except Exception:
            # 其他异常（如 _NotApplicable）照原样交给 _guard，先把单据和档案清掉。
            run.cleanup()
            drop_made(run, made)
            raise
        left = run.cleanup()
        if left:
            raise _Fail("清理失败：" + left + "；" + drop_made(run, made))
        count = len(made)
        note = drop_made(run, made)
        if count == 0:
            return status, detail
        return status, detail + ("；" + note if note else f"；自建档案 {count} 个已删除")

    return _guard(env, spec, wrapped)


# ---- 保质期存货的无来源采购入库（自建存货） ----


def _shelf_body(run: _Run, made: Made) -> tuple[str, str]:
    parts = run.pick(shelf_made_pick_sql(), 5)
    if parts is None:
        return "N/A", "账套挑不到可无来源入库的外购简单存货作模板（不管货位的仓库、普通采购的供应商和部门）"
    wh, template, ven, dep, ptype = parts
    inv = made_inventory(run, made, template, "9197", _SHELF_FLAGS)
    status, detail = _with_havepo(run, lambda: _shelf_run(run, [wh, inv, ven, dep, ptype, _MASS, _MASS_UNIT]))
    return status, f"自建保质期存货 {inv}（模板 {template}）；" + detail


def purchase_in_shelf_made(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return made_guard(env, spec, _shelf_body)


MADE_OPS = {_OP: purchase_in_shelf_made}


def made_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name=_OP, id_from="none")
    title = "自建保质期存货的无来源采购入库：生产 / 失效日期照写、保质期取档案，记下 U8 补的有效期至，随即删单删存货"
    # 同 purchase-in-shelf（动现存量、新建采购入库、临时改「普通业务必有订单」），另新建、删除存货档案。
    tags = frozenset(("opt:havepo", "new:purchase_in", "w:purchase_in", "stock", "arc:inventory"))
    return [CaseSpec(_NAME, title, (), (step,), True, tags=tags, routes=(ARC_CREATE, _CREATE, _DELETE, ARC_DELETE),
                     observe=True)]
