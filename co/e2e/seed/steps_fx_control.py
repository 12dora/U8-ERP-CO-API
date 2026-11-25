"""造数：应付的美元受控科目（fx_ap_control），排在外币单据（steps_docs_fx）之前。

外币付款单、应付单的受控科目要与表头同币种（实测人民币受控科目报「第1行的科目与表头币种不一致」），steps_docs_fx.usd_control
在 2202 下找币种为美元的末级科目。从系统模板复制来的科目表里应收一侧有美元户（1122 下），应付一侧没有，fx_ap_* 造不出来，
ap-writeoff-fx 只能 N/A。会计科目写入接口不开放（ICode.Transact 实测报「未设置对象变量」），这一步用 SQL 补一条：

- 来源：造数年度 2202 下带供应商辅助核算（bsup=1）、不是美元户的第一个末级科目（同 steps_docs_arap.control_km 的口径），
  受控系统、辅助核算、方向等各列原样复制（列清单同 scripts/account/create-years.sql，不写自增列 i_id）。
- 新科目：与来源同一上级、同一级次的下一个空编码（来源是一级科目时失败，请先在 U8 客户端建下级科目），名称「应付美元户」，
  币种美元，末级。触发器 gl_v_code_InsteadTrigger 只改 iyear = 0 的行，这里写造数年度，不停触发器。
- 幂等：已有美元户（usd_control 找得到）就跳过；写完再按 usd_control 回读。
只在可造数通道的调试会话里执行，`close --keep` 留进基线；拼进 SQL 的编码都过 quoted / 数字校验。
"""

from __future__ import annotations

import re

from co.e2e.seed.base import Probe, Step, quoted, remember
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.steps_docs_fx import CURRENCY, period_of, usd_control

NAME = "fx_ap_control"
SIDE = "ap_payment"
PREFIX = "2202"
AP_USD_NAME = "应付美元户"
AP_USD_ENGL = "AP USD"
_DIGITS = re.compile(r"^[0-9]{1,30}\Z")

_SOURCE_SQL = (
    "SELECT TOP (1) LTRIM(RTRIM(ccode)) AS code, igrade AS grade FROM code WHERE iyear = {year} AND bend = 1 AND bsup = 1"
    " AND ccode LIKE N'" + PREFIX + "%' AND ISNULL(cexch_name, N'') <> {name} ORDER BY ccode"
)
_PARENT_SQL = (
    "SELECT TOP (1) LTRIM(RTRIM(ccode)) AS code FROM code WHERE iyear = {year} AND igrade = {grade}"
    " AND {child} LIKE LTRIM(RTRIM(ccode)) + N'%' ORDER BY LEN(ccode) DESC"
)
_SIBLINGS_SQL = (
    "SELECT LTRIM(RTRIM(ccode)) AS code FROM code WHERE iyear = {year} AND ccode LIKE N'{parent}%'"
    " AND LEN(LTRIM(RTRIM(ccode))) = {width} ORDER BY ccode"
)
# 复制的列（create-years.sql 的列清单）；ccode、ccode_name、ccode_engl、cexch_name、bend、iyear 换成新值。
_COLS = (
    "cclass, cclass_engl, cclassany, cclassany_engl, ccode, ccode_name, ccode_engl, igrade, bproperty,"
    " cbook_type, cbook_type_engl, chelp, cexch_name, cmeasure, bperson, bcus, bsup, bdept, bitem, cass_item,"
    " br, be, cgather, bend, bexchange, bcash, bbank, bused, bd_c, dbegin, dend, itrans, bclose, cother, iotherused,"
    " bcDefine1, bcDefine2, bcDefine3, bcDefine4, bcDefine5, bcDefine6, bcDefine7, bcDefine8, bcDefine9, bcDefine10,"
    " iViewItem, bGCJS, bCashItem, bcDefine11, bcDefine12, bcDefine13, bcDefine14, bcDefine15, bcDefine16,"
    " bReport, cUserDefineType, iyear, dModifyDate, bparacc, bpardiff"
)
_OVERRIDE = {
    "ccode": "{code}",
    "ccode_name": "{cname}",
    "ccode_engl": "{ename}",
    "cexch_name": "{name}",
    "bend": "1",
    "iyear": "{year}",
}


def _year(ctx: SeedContext) -> int:
    return period_of(ctx.day)[0]


def _select_list() -> str:
    parts = []
    for col in (part.strip() for part in _COLS.split(",")):
        parts.append(_OVERRIDE.get(col, "c." + col))
    return ", ".join(parts)


def insert_sql(year: int, source: str, code: str) -> str:
    """按来源科目复制一行新科目；新编码已存在时不插。"""
    for text in (source, code):
        if _DIGITS.fullmatch(text) is None:
            raise SeedError("科目编码不合法：" + text)
    fill = {
        "code": "N'" + code + "'",
        "cname": quoted([AP_USD_NAME]),
        "ename": quoted([AP_USD_ENGL]),
        "name": quoted([CURRENCY]),
        "year": str(year),
    }
    select = _select_list().format(**fill)
    return (
        f"INSERT INTO code ({_COLS})\n"
        f"SELECT {select}\n"
        f"FROM code AS c WHERE c.iyear = {year} AND c.ccode = N'{source}'\n"
        f"  AND NOT EXISTS (SELECT 1 FROM code AS t WHERE t.iyear = {year} AND t.ccode = N'{code}')"
    )


def next_code(parent: str, width: int, siblings: list[str]) -> str:
    """同一上级、同一长度的下一个空编码：已有下级末段的最大值加 1，按段长补零。段满时失败。"""
    seg = width - len(parent)
    if seg <= 0:
        raise SeedError("科目编码级次不对：上级 " + parent + "，长度 " + str(width))
    tails = [int(code[len(parent) :]) for code in siblings if code.startswith(parent) and code[len(parent) :].isdigit()]
    nxt = max(tails, default=0) + 1
    if nxt >= 10**seg:
        raise SeedError("科目 " + parent + " 下的编码已用完")
    return parent + str(nxt).zfill(seg)


def _source(ctx: SeedContext, year: int) -> tuple[str, int]:
    rows = ctx.rows(_SOURCE_SQL.format(year=year, name=quoted([CURRENCY])))
    row = rows[0] if rows else {}
    code, grade = row.get("code", "").strip(), row.get("grade", "").strip()
    if _DIGITS.fullmatch(code) is None or not grade.isdigit():
        raise SeedError(f"{year} 年科目表里找不到 {PREFIX} 下带供应商辅助核算的末级科目")
    if int(grade) < 2:
        raise SeedError(f"应付受控科目 {code} 是一级末级科目，先在 U8 客户端给它建下级科目，再跑 {NAME}")
    return code, int(grade)


def plan_code(ctx: SeedContext) -> tuple[str, str]:
    """（来源科目, 新科目编码）。"""
    year = _year(ctx)
    source, grade = _source(ctx, year)
    rows = ctx.rows(_PARENT_SQL.format(year=year, grade=grade - 1, child="N'" + source + "'"))
    parent = rows[0].get("code", "").strip() if rows else ""
    if _DIGITS.fullmatch(parent) is None or not source.startswith(parent) or parent == source:
        raise SeedError("找不到科目 " + source + " 的上级科目")
    siblings = [row.get("code", "").strip() for row in ctx.rows(_SIBLINGS_SQL.format(year=year, parent=parent, width=len(source)))]
    return source, next_code(parent, len(source), siblings)


def _probe(ctx: SeedContext) -> Probe:
    code = usd_control(ctx, SIDE)
    if code:
        return Probe(("account:" + code,), ())
    return Probe((), ("account:" + PREFIX + "*:" + CURRENCY,))


def _apply(ctx: SeedContext) -> tuple[int, int]:
    if usd_control(ctx, SIDE):
        return 0, 1
    source, code = plan_code(ctx)
    ctx.exec(insert_sql(_year(ctx), source, code))
    got = usd_control(ctx, SIDE)
    if got != code:
        raise SeedError(f"新增科目 {code}（复制 {source}）之后按币种{CURRENCY}回读到的应付受控科目是 {got or '空'}", saved=ctx.last_call)
    remember(ctx, "account", code)
    return 1, 0


STEPS = (Step(NAME, "应付美元受控科目（" + PREFIX + " 下，SQL 复制同级科目；已有沿用）", _probe, _apply),)
