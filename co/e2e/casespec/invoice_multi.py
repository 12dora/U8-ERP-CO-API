"""一张销售发票参照多张发货单（vouchers/generate type=sale_invoice，正文 sources），表体自定义项 cbdefine1。

sale-invoice-multi-dispatch：挑一个不管货位的仓库里可用量 >= 4 的可销售简单存货，自建 1 行销售订单 4 个并审核 →
  参照它生成两张发货单 A、B 各 2 个并审核 →
  拒绝：同一张发货单列两次 400、B 行开 3 个（只剩 2）409、A 的来源下列 B 的行 400，两行累计开票都不变 →
  预演：sources [A 行 2 个、B 行 1 个] dry_run → 200、dry_run=true，两行累计开票、指向它们的发票行数都不变 →
  正式：同一正文 → 200、source_id=A、source_ids=[A, B]、lines=2；发票两行：iDLsID、数量 2 / 1（B 部分开票）、
  cbdlcode 是各自的发货单号；行金额按 U8 界面口径：价税合计 = round(数量 × 含税单价, 2) = 无税金额 + 税额
  （B 行 1 × 1 = 1.00，按无税单价 0.884956 算是 0.88 + 0.11 = 0.99，差一分，即 bsaleprice=0 时的算法）；账套有 SaleBillVouchs_extradefine.cbdefine1 时请求带 cbdefine1 数值 2.15 / 0.95，
  库里和 vouchers/load 的行上都读回同值（没有该列时不带，说明里注明）；按列目录另挑一列 cbdefine1–3 之外账套有的
  表体扩展自定义项文本列（cbdefine4 … cbdefine99 编号最小的），A 行带一个值，库里和 vouchers/load 读回同值（没有时注明）→
  A 行累计开票 +2、B 行 +1 →
  删除发票、回读已删 → 两行累计开票回到原值 → 弃审删除两张发货单、订单。
发票不复核（复核又弃复后可能删不掉，见 invoice-generate）。挑不到数据记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（INVOICE_MULTI_OPS 并入 ops_run）。
多张来源的 COM 路径（同一 Save、表头照第一张发货单）未在 U8 上实测：第一次跑失败时先看审计里的「发票行核对」记录。
"""

from __future__ import annotations

import os
from decimal import Decimal

from co.e2e.casespec.dispatch_line_bdefine import check_taxed, sale_taxed
from co.e2e.casespec.dispatch_line_bdefine_sql import amount_problem, amounts_sql
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _SALE_TAGS, _dec, _dispatch, _Fail, _guard, _Run, _sale_order
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.sale_blue_red_sql import inv_gone_sql
from co.e2e.casespec.dispatch_add_sql import pick_sql
from co.e2e.casespec.invoice_multi_sql import (
    family_col,
    family_col_sql,
    inv_count_sql,
    inv_lines_sql,
    settle_sql,
    body_define_col_sql,
)

_GEN = "/v1/vouchers/generate"
_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_LOAD = "/v1/vouchers/load"
_ORDER = 4
_SHIP = 2
_PART = 1
_SIGN = (Decimal("2.15"), Decimal("0.95"))
# 族成员列（列名, 值）：只放在 A 行上。
Family = tuple[str, str] | None


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _line(src: str, qty: int, sign: Decimal | None, family: Family = None) -> dict:
    row: dict = {"source_line_id": int(src), "quantity": qty}
    if sign is not None:
        row["cbdefine1"] = str(sign)
    if family is not None:
        row[family[0]] = family[1]
    return row


def _body(sources: list[tuple[int, list[dict]]]) -> dict:
    return {"type": "sale_invoice", "sources": [{"id": doc, "lines": lines} for doc, lines in sources]}


def _settles(run: _Run, lines: tuple[str, str]) -> tuple[Decimal, Decimal]:
    return _dec(run.cell(settle_sql(lines[0]))), _dec(run.cell(settle_sql(lines[1])))


def _refusals(run: _Run, docs: tuple[int, int], lines: tuple[str, str], base: tuple[Decimal, Decimal]) -> None:
    checks = (
        (_body([(docs[0], [_line(lines[0], 1, None)]), (docs[0], [_line(lines[0], 1, None)])]), (400,), "同一张发货单列两次"),
        (_body([(docs[0], [_line(lines[0], 1, None)]), (docs[1], [_line(lines[1], _SHIP + 1, None)])]), (409,),
         f"B 行开 {_SHIP + 1} 个（只剩 {_SHIP}）"),
        (_body([(docs[0], [_line(lines[1], 1, None)])]), (400,), "A 的来源下列 B 的行"),
    )
    for body, want, what in checks:
        status, payload = run.send(_GEN, body)
        if status not in want:
            raise _Fail(f"{what}应 {want[0]}，实际 {_brief(status, payload)}")
    if _settles(run, lines) != base:
        raise _Fail(f"被拒后两行累计开票应不变 {base}")


def _dry(run: _Run, body: dict, lines: tuple[str, str], base: tuple[Decimal, Decimal]) -> None:
    count = run.cell(inv_count_sql(*lines))
    payload = run.ok(_GEN, {**body, "dry_run": True}, "预演一张发票参照两张发货单")
    if payload.get("dry_run") is not True:
        raise _Fail("预演响应应带 dry_run=true")
    if _settles(run, lines) != base or run.cell(inv_count_sql(*lines)) != count:
        raise _Fail(f"预演后两行累计开票应不变 {base}、发票行数应仍为 {count}")


def _shape(run: _Run, inv: int, lines: tuple[str, str], signs: tuple[Decimal | None, Decimal | None], family: Family) -> None:
    rows = [row.split("|") for row in run.cells(inv_lines_sql(str(inv), signs[0] is not None, family[0] if family else ""))]
    want = {lines[0]: (f"{_SHIP}.0000", signs[0]), lines[1]: (f"{_PART}.0000", signs[1])}
    if len(rows) != 2:
        raise _Fail(f"发票应有 2 行，实际 {len(rows)} 行")
    for parts in rows:
        if len(parts) != (5 if family else 4) or parts[0] not in want:
            raise _Fail(f"发票行不对：{'|'.join(parts)}")
        if family and parts[0] == lines[0] and parts[4] != family[1]:
            raise _Fail(f"发货行 {parts[0]} 的 {family[0]} 应为 {family[1]}，实际 {parts[4] or '空'}")
        qty, sign = want[parts[0]]
        if parts[1] != qty or parts[2] != "1":
            raise _Fail(f"发货行 {parts[0]} 的发票行应为 数量 {qty}、cbdlcode 是该发货单号，实际 {'|'.join(parts)}")
        if sign is not None and _dec(parts[3]) != sign:
            raise _Fail(f"发货行 {parts[0]} 的 cbdefine1 应为 {sign}，实际 {parts[3] or '空'}")


def _loaded(run: _Run, inv: int, line: str, sign: Decimal | None, family: Family) -> None:
    loaded = run.ok(_LOAD, {"type": "sale_invoice", "id": inv}, "读取发票")
    for row in loaded.get("lines") or []:
        low = {str(key).lower(): value for key, value in row.items()}
        if str(low.get("idlsid")) != line:
            continue
        if sign is not None and _dec(str(low.get("cbdefine1") or "")) != sign:
            raise _Fail(f"vouchers/load 的行 {line} cbdefine1 应为 {sign}，实际 {low.get('cbdefine1')}")
        if family and str(low.get(family[0]) or "").strip() != family[1]:
            raise _Fail(f"vouchers/load 的行 {line} {family[0]} 应为 {family[1]}，实际 {low.get(family[0])}")
        return
    raise _Fail(f"vouchers/load 没有来源发货行 {line} 的行")


def _real(run: _Run, docs: tuple[int, int], lines: tuple[str, str], signs: tuple, family: Family) -> int:
    body = _body([(docs[0], [_line(lines[0], _SHIP, signs[0], family)]), (docs[1], [_line(lines[1], _PART, signs[1])])])
    _dry(run, body, lines, _settles(run, lines))
    payload = run.ok(_GEN, body, "一张发票参照两张发货单")
    inv = payload.get("id")
    if type(inv) is not int or inv <= 0:
        raise _Fail("响应没有新发票 id")
    run.drop("sale_invoice", inv)
    if payload.get("source_id") != docs[0] or payload.get("source_ids") != list(docs) or payload.get("lines") != 2:
        raise _Fail(f"响应应为 source_id={docs[0]}、source_ids={list(docs)}、lines=2，实际 "
                    f"{payload.get('source_id')}、{payload.get('source_ids')}、{payload.get('lines')}")
    _shape(run, inv, lines, signs, family)
    if signs[0] is not None or family:
        _loaded(run, inv, lines[0], signs[0], family)
    return inv


def _amounts(run: _Run, inv: int) -> str:
    """发票行金额同 U8 界面（报价含税时价税合计 = round(数量 × 含税单价, 2)），bsaleprice 等于账套选项；返回说明。"""
    taxed = sale_taxed(run)
    problem = amount_problem(run.cells(amounts_sql("sale_invoice", str(inv))), taxed)
    if problem:
        raise _Fail(f"发票 {inv} 行金额：{problem}")
    return check_taxed(run, taxed, [("sale_invoice", inv)])


def _family(run: _Run) -> Family:
    """账套有的族成员列（列目录里 cbdefine4 … cbdefine99 的第一列文本列）和要写的值；没有时为 None。"""
    col = family_col(run.cell(family_col_sql()))
    return (col, "E2EF" + os.urandom(2).hex().upper()) if col else None


def _run_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(pick_sql(), 6)
    if parts is None:
        return "N/A", "账套挑不到不管货位的仓库里可用量 >= 4 的可销售简单存货"
    wh = safe_lot(parts[5])
    so = _sale_order(run, parts, _ORDER)
    dl_a, line_a = _dispatch(run, so, wh, _SHIP, "")
    dl_b, line_b = _dispatch(run, so, wh, _SHIP, "")
    docs, lines = (dl_a, dl_b), (line_a, line_b)
    base = _settles(run, lines)
    _refusals(run, docs, lines, base)
    has_sign = run.cell(body_define_col_sql()) == "1"
    signs = _SIGN if has_sign else (None, None)
    family = _family(run)
    inv = _real(run, docs, lines, signs, family)
    flags = _amounts(run, inv)
    after = _settles(run, lines)
    if after != (base[0] + _SHIP, base[1] + _PART):
        raise _Fail(f"开票后累计开票应为 {base[0] + _SHIP} / {base[1] + _PART}，实际 {after[0]} / {after[1]}")
    run.settle(1)
    if run.cell(inv_gone_sql(str(inv))) != "0":
        raise _Fail(f"删除后发票 {inv} 仍在")
    if _settles(run, lines) != base:
        raise _Fail(f"删除发票后两行累计开票应回到 {base}")
    note = "自定义项 cbdefine1 写入并读回" if has_sign else "账套没有 SaleBillVouchs_extradefine.cbdefine1，未带 cbdefine1"
    note += f"，族成员 {family[0]} 写入并读回" if family else "，账套没有 cbdefine4–99 的文本列，未带族成员"
    return "PASS", (
        f"发货单 {dl_a}、{dl_b} 合开发票 {inv}：{_SHIP} + {_PART}（部分，行金额按界面口径，{flags}），拒绝三例、预演不变，{note}；"
        f"累计开票 +{_SHIP} / +{_PART}，删除后回到 {base[0]} / {base[1]}"
    )


def sale_invoice_multi(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _run_body)


INVOICE_MULTI_OPS = {"sale_invoice_multi": sale_invoice_multi}
_TAGS = _SALE_TAGS | frozenset(("new:sale_invoice", "w:sale_invoice"))
_ROUTES = (_CREATE, _VERIFY, _GEN, _LOAD, _DELETE)


def invoice_multi_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="sale_invoice_multi", id_from="none")
    title = (
        "一张销售发票参照两张发货单：部分数量、自定义项 cbdefine1 和一列族成员 cbdefineN 读回，拒绝三例，预演不改累计开票，删除后回退；"
        "自建订单、发货单并清理"
    )
    return [CaseSpec("sale-invoice-multi-dispatch", title, (), (step,), True, tags=_TAGS, routes=_ROUTES)]
