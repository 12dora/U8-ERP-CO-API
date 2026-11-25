"""生产订单子件的固定用量、损耗率、辅计量：自建母件和物料清单，建生产订单后改数量，回读子件。

两条用例共用一套流程（只在造子件上不同），都在快照里自建自删：
1. 按挑到的自制母件复制一个新母件（SQL 改成自制、BOM 母件、免检），新母件没有别的版本，审核不会顺延别人的失效日期。
2. mo-allocate-fixed-scrap：行 10 子件 1 变动用量 1/3、子件损耗率 3%；行 20 子件 2 用量 2、损耗率 5%，新增后用 SQL
   改成固定用量（FVFlag = 2；API 新增只送变动用量）。mo-allocate-aux：新建固定换算的计量单位组、主计量和辅计量单位
   （换算率 2.345678，6 位小数，多于常见的件数小数位），按子件 1 复制一个挂这个组的新子件作行 10（用量 2.345678/1：
   分母取 1，U8 展开时按分子或按单位用量折算辅用量都得 1），U8 没自动填辅计量时才用 SQL 补辅计量单位、换算率、辅用量 1；行 20 子件 2 用量 1。母件损耗率都是 0（生产订单修改
   遇到母件损耗率非 0 的子件 409，见 MoUpdateCols）。新增 → SQL 补丁 → 审核物料清单。
3. 建生产订单（数量 1，U8 按新物料清单展开）→ 回读全部子件（alloc_rows_sql，原样留在报告目录的 SQL 输出里）→
   vouchers/update 把数量改成 3 → 再回读。
4. 断言：展开出 2 个子件；改数量前后行数、行号、存货、用量分子分母、损耗率、FVFlag、换算率、辅用量不变；改后每个子件
   数量、件数等于 U8 的算法（MoUpdateAlloc：系数 1 + 子件损耗率/100，变动用量再除以 1 − 母件损耗率/100；有换算率时
   单位用量 = 辅用量 / 分母 × 系数 × 换算率；变动用量 × 行数量、固定用量不乘；数量按存货数量小数位、件数 = 未舍入数量 /
   换算率按件数小数位四舍五入）。新建时 U8 展开的数量是否符合同一算法、FVFlag / 辅计量是否带到生产订单，只记进说明
   （以实测为准）。生产订单子件不满足桥的辅计量自洽规则（有换算率时主用量 = 辅用量 × 换算率，
   MoUpdateAlloc.CheckAux；U8 按存货「BOM 展开单位」折算时会不一致）时，改数量应是文档写明的 409 state_mismatch
   （原因同 CheckAux）且子件原样不变，用例按这一支判定并把 U8 展开出的主用量、辅用量、换算率写进说明（observe）。
5. 删除生产订单 → 弃审、删除物料清单 → 回读没有残留 → 删除自建的存货、计量单位、计量单位组（made_guard，删不掉只记说明）。
步骤 kind 为 "custom"，sql_name 是操作名（MO_ALLOC_OPS 并入 ops_run）。
"""

from __future__ import annotations

import datetime as dt
import secrets
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more_sql import arc_more_pick_sql
from co.e2e.casespec.stock_plus import _Fail, _Run
from co.e2e.casespec.made_inv import ARC_CREATE, ARC_DELETE, Made, made_archive, made_guard, made_inventory
from co.e2e.casespec.made_inv_sql import (
    ALLOC_COLS,
    alloc_rows_sql,
    bom_aux_sql,
    bom_fixed_sql,
    bom_line_sql,
    dec,
    digits_sql,
    inv_units_sql,
    made_gone_sql,
    mo_alloc_pick_sql,
    mo_line_sql,
)

_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_MO = "production_order"
MEMO = "u8co e2e 生产订单子件用量"
_RATE = "2.345678"
_NEW_QTY = 3
_PARENT_FLAGS = {"bSelf": 1, "bBomMain": 1, "bBomSub": 0, "bPropertyCheck": 0, "bInvBatch": 0, "bInvQuality": 0, "bSerial": 0}
_CHILD_FLAGS = {"bBomSub": 1, "bBomMain": 0, "bPropertyCheck": 0, "bInvBatch": 0, "bInvQuality": 0, "bSerial": 0}
# 改数量前后应保持不变的列（ALLOC_COLS 的前 9 列：行号到换算率）。
_STATIC = 9
_EPS = Decimal("0.000001")


# ---- 造母件、子件、物料清单 ----


def _aux_child(run: _Run, made: Made, template: str) -> tuple[str, str]:
    """新建固定换算组、主计量、辅计量单位和挂这个组的新子件。返回（子件, 辅计量单位）。"""
    group = run.cell(arc_more_pick_sql("unit_group"))
    if not group:
        raise _Fail("挑不到空闲的计量单位组编码")
    made_archive(run, made, "unit_group", group, {"name": "e2e换算组" + group, "type": 1})
    tail = f"{secrets.randbelow(10**8):08d}"
    main, aux = "ZY" + tail, "ZX" + tail
    made_archive(run, made, "unit", main, {"name": "e2e主" + tail[-4:], "group_code": group})
    made_archive(run, made, "unit", aux, {"name": "e2e辅" + tail[-4:], "group_code": group, "changerate": float(_RATE)})
    child = made_inventory(run, made, template, "9195", _CHILD_FLAGS)
    if run.cell(inv_units_sql(child, group, main, aux)) != "1":
        raise _Fail(f"新子件 {child} 挂计量单位组 {group} 没改到 1 行")
    return child, aux


def _bom_lines(variant: str, first: str, second: str) -> list[dict]:
    if variant == "aux":
        # 分母 1：U8 展开时不论辅用量按分子还是按单位用量（分子 / 分母）折算，都等于 分子 / 换算率 = 1。
        return [{"inv_code": first, "base_qty_n": float(_RATE), "base_qty_d": 1, "sort_seq": 10},
                {"inv_code": second, "base_qty_n": 1, "base_qty_d": 1, "sort_seq": 20}]
    return [{"inv_code": first, "base_qty_n": 1, "base_qty_d": 3, "comp_scrap": 3, "sort_seq": 10},
            {"inv_code": second, "base_qty_n": 2, "base_qty_d": 1, "comp_scrap": 5, "sort_seq": 20}]


def _patch_bom(run: _Run, bom: int, variant: str, aux: str) -> str:
    """新增后、审核前改物料清单行；返回说明。"""
    if variant != "aux":
        if run.cell(bom_fixed_sql(str(bom), 20)) != "1":
            raise _Fail(f"物料清单 {bom} 行 20 改固定用量没改到 1 行")
        return "行 20 SQL 改固定用量"
    line = (run.cell(bom_line_sql(str(bom), 10)).split("|") + [""] * 5)[:5]
    if line[2] and dec(line[3]) != 0 and dec(line[4]) != 0:
        return f"U8 自动填了辅计量 {line[2]}（换算率 {line[3]}、辅用量 {line[4]}）"
    if run.cell(bom_aux_sql(str(bom), 10, aux, _RATE)) != "1":
        raise _Fail(f"物料清单 {bom} 行 10 补辅计量没改到 1 行")
    return f"U8 没填辅计量（{line[2] or '空'}），SQL 补 {aux}"


def _bom(run: _Run, parent: str, lines: list[dict], variant: str, aux: str) -> tuple[int, str]:
    head = {"inv_code": parent, "version_desc": MEMO}
    bom = run.made(_CREATE, {"type": "bom", "head": head, "lines": lines}, "新建物料清单")
    run.drop("bom", bom)
    note = _patch_bom(run, bom, variant, aux)
    run.ok(_VERIFY, {"type": "bom", "id": bom, "action": "verify"}, "审核物料清单")
    run.unverify("bom", bom)
    return bom, note


def _mo(run: _Run, parent: str, dept: str, mo_type: str) -> int:
    due = (dt.date.fromisoformat(run.env.today) + dt.timedelta(days=2)).isoformat()
    line = {"inv_code": parent, "qty": 1, "start_date": run.env.today, "due_date": due, "mo_type": mo_type, "dept_code": dept}
    payload = run.ok(_CREATE, {"type": _MO, "head": {"remark": MEMO}, "lines": [line]}, "新建生产订单")
    mo = payload.get("id")
    if type(mo) is not int or mo <= 0:
        raise _Fail("新建生产订单：响应没有新单据 id")
    run.drop(_MO, mo)
    if payload.get("allocates") != 2:
        raise _Fail(f"生产订单 {mo} 应按新物料清单展开 2 个子件，实际 {payload.get('allocates')}")
    return mo


# ---- 判定 ----


def _rows(run: _Run, mo: int) -> list[dict[str, str]]:
    rows = []
    for cell in run.cells(alloc_rows_sql(str(mo))):
        parts = cell.split("|")
        if len(parts) != len(ALLOC_COLS):
            raise _Fail("子件回读格式不对：" + cell[:80])
        rows.append(dict(zip(ALLOC_COLS, parts, strict=True)))
    return rows


def _round(value: Decimal, digits: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def expected(row: dict[str, str], digits: tuple[int, int]) -> tuple[Decimal, Decimal | None]:
    """按 MoUpdateAlloc 的算法：（数量, 件数；没有换算率时件数为 None）。"""
    variety = row["fv"] == "1"
    factor = 1 + dec(row["comp_scrap"]) / 100
    if variety and row["mo_class"] != "2":
        factor = factor / (1 - dec(row["parent_scrap"]) / 100)
    rate = dec(row["rate"])
    base = (dec(row["aux_n"]) * rate if rate != 0 else dec(row["qty_n"])) / dec(row["qty_d"]) * factor
    raw = base * dec(row["line_qty"]) if variety else base
    return _round(raw, digits[0]), (_round(raw / rate, digits[1]) if rate != 0 else None)


def off_formula(rows: list[dict[str, str]], digits: tuple[int, int]) -> list[str]:
    """数量、件数与算法不符的子件说明。"""
    bad = []
    for row in rows:
        qty, aux = expected(row, digits)
        if dec(row["qty"]) != qty or (aux is not None and dec(row["aux_qty"]) != aux):
            bad.append(f"行 {row['seq']} 数量 {row['qty']} / 件数 {row['aux_qty'] or '空'}，算法 {qty} / {aux if aux is not None else '-'}")
    return bad


def _brief(rows: list[dict[str, str]]) -> str:
    return "，".join(
        f"{row['seq']}:FV{row['fv']}/损{row['comp_scrap']}/主{row['qty_n']}/辅{row['aux_n'] or '空'}/率{row['rate'] or '空'}"
        f"/数{row['qty']}/件{row['aux_qty'] or '空'}"
        for row in rows
    )


def _judge(before: list[dict[str, str]], after: list[dict[str, str]], digits: tuple[int, int]) -> str:
    """改数量后的问题；没有为空串。"""
    keep = [[row[col] for col in ALLOC_COLS[:_STATIC]] for row in before]
    if [[row[col] for col in ALLOC_COLS[:_STATIC]] for row in after] != keep:
        return f"改数量后子件的行号、存货、用量、损耗率、FVFlag 或换算率变了：{_brief(before)} → {_brief(after)}"
    if any(dec(row["line_qty"]) != _NEW_QTY for row in after):
        return "改数量后订单行数量不是 " + str(_NEW_QTY)
    bad = off_formula(after, digits)
    return "改数量后与 U8 算法不符：" + "；".join(bad) if bad else ""


def _flow(run: _Run, made: Made, parts: list[str], variant: str) -> tuple[str, str]:
    template, child1, child2, dept, mo_type = parts
    dg, nd = (int(part) for part in (run.cell(digits_sql()).split("|") + ["6", "6"])[:2])
    parent = made_inventory(run, made, template, "9196", _PARENT_FLAGS)
    first, aux = _aux_child(run, made, child1) if variant == "aux" else (child1, "")
    bom, note = _bom(run, parent, _bom_lines(variant, first, child2), variant, aux)
    mo = _mo(run, parent, dept, mo_type)
    before = _rows(run, mo)
    line = run.cell(mo_line_sql(str(mo)))
    mixed = aux_mixed(before, dg, nd, Decimal(_NEW_QTY))
    refused = _update(run, mo, int(line or 0), mixed)
    after = _rows(run, mo)
    run.settle(3)
    if refused:
        problem = "" if after == before else f"桥 409 后子件变了：{_brief(before)} → {_brief(after)}"
    else:
        problem = _judge(before, after, (dg, nd))
    if not problem and run.cell(made_gone_sql(str(mo), str(bom))) != "0":
        problem = f"删除后生产订单 {mo} 或物料清单 {bom} 仍有残留"
    if problem:
        raise _Fail(problem)
    created = off_formula(before, (dg, nd))
    head = f"小数位 {dg}/{nd}；{note}；新建 {_brief(before)}（新建时{'与算法不符：' + created[0] if created else '符合算法'}）"
    if refused:
        return "PASS", f"{head}；U8 展开的{mixed[0][0]}，改数量按文档 409「{mixed[0][1]}」、子件不变；已删单"
    return "PASS", f"{head}；改 {_NEW_QTY} 后 {_brief(after)}；已删单"


def aux_mixed(rows: list[dict[str, str]], qty_digits: int = 6, aux_digits: int = 6, new_qty: Decimal = Decimal(0)) -> list[tuple[str, str]]:
    """桥改数量前的辅计量自洽规则（MoUpdateAlloc.CheckAux）：有换算率的子件要有辅用量，且主用量 = 辅用量 × 换算率
    或其按数量小数位四舍五入的值（U8 展开时这样存，实测）。返回不满足的（子件说明, 桥 409 消息里的原因）。"""
    bad = []
    for row in rows:
        rate, aux_n, base_n = dec(row["rate"]), dec(row["aux_n"]), dec(row["qty_n"])
        if rate == 0:
            continue
        what = f"行 {row['seq']} 主用量 {row['qty_n']}、辅用量 {row['aux_n'] or '空'}、换算率 {row['rate']}"
        if aux_n == 0:
            bad.append((what, "有换算率却没有辅计量用量"))
        elif min(abs(base_n - aux_n * rate), abs(base_n - _round(aux_n * rate, qty_digits))) > _EPS:
            bad.append((what, "辅计量用量与主用量不一致"))
        elif abs(base_n - aux_n * rate) > _EPS and _branches_differ(row, base_n, aux_n * rate, (qty_digits, aux_digits), new_qty):
            bad.append((what, "按主计量和辅计量算出的新数量不同"))
    return bad


def _update(run: _Run, mo: int, line: int, mixed: list[tuple[str, str]]) -> bool:
    """改数量。子件辅计量不自洽时应 409（返回 True），否则应成功（返回 False）。"""
    body = {"type": _MO, "id": mo, "lines": [{"op": "update", "line_id": line, "qty": _NEW_QTY}]}
    if not mixed:
        run.ok(_UPDATE, body, "改生产订单数量")
        return False
    status, payload = run.send(_UPDATE, body)
    message = str(payload.get("message") or "")
    if (status, payload.get("code")) != (409, "state_mismatch") or mixed[0][1] not in message:
        raise _Fail(f"{mixed[0][0]}，改数量应 409「{mixed[0][1]}」，实际 {status} {payload.get('code')} {message[:60]}")
    return True


def _body(variant: str) -> Callable[[_Run, Made], tuple[str, str]]:
    def body(run: _Run, made: Made) -> tuple[str, str]:
        parts = run.pick(mo_alloc_pick_sql(), 5)
        if parts is None:
            return "N/A", "账套挑不到自制母件模板、两个免检的简单子件、生产部门和订单类别"
        return _flow(run, made, parts, variant)

    return body


def mo_allocate_fixed_scrap(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return made_guard(env, spec, _body("fixed"))


def mo_allocate_aux(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return made_guard(env, spec, _body("aux"))


MO_ALLOC_OPS = {"mo_alloc_fixed": mo_allocate_fixed_scrap, "mo_alloc_aux": mo_allocate_aux}


def _case(name: str, title: str, op: str, arcs: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    # bom：按 BOM 展开新建生产订单、审核物料清单，与改 BOM 版本的用例不同时跑（同 mo-create-delete）。
    tags = frozenset(("bom", "new:bom", "w:bom", "new:production_order", "w:production_order", *(f"arc:{a}" for a in arcs)))
    routes = (ARC_CREATE, _CREATE, _VERIFY, _UPDATE, _DELETE, ARC_DELETE)
    return CaseSpec(name, title, (), (step,), True, tags=tags, routes=routes, observe=True)


def mo_alloc_cases() -> list[CaseSpec]:
    return [
        _case("mo-allocate-fixed-scrap",
              "生产订单子件固定用量与损耗率：自建母件和物料清单，建订单后改数量，子件数量按 U8 算法重算，随即删除",
              "mo_alloc_fixed", ("inventory",)),
        _case("mo-allocate-aux",
              "生产订单辅计量子件：自建换算组、子件和物料清单，建订单后改数量；辅用量与主用量自洽时件数按换算率重算，"
              "不自洽时桥 409 且子件不变（记下 U8 展开的辅用量），随即删除",
              "mo_alloc_aux", ("inventory", "unit", "unit_group")),
    ]


def _branches_differ(row: dict[str, str], main_n: Decimal, aux_main: Decimal, digits: tuple[int, int], qty: Decimal) -> bool:
    """主用量只与舍入后的辅用量 × 换算率相等时，桥要求主、辅两条算法的新数量和件数都相同（MoUpdateAlloc.Scale）。"""
    rate, base_d = dec(row["rate"]), dec(row.get("qty_d") or "1") or Decimal(1)
    factor = 1 + dec(row.get("comp_scrap") or "0") / 100
    variety = (row.get("fv") or "1") == "1"
    raws = [(qty if variety else 1) * n / base_d * factor for n in (main_n, aux_main)]
    return len({(_round(r, digits[0]), _round(r / rate, digits[1])) for r in raws}) > 1

