"""库存待实测补用例：全程自建自删。

sale-out-batch-dates：同 sale-out-batch-override（订单 → 发货单写批号 1 → 出库单换成批号 2），另核对换了批号的出库行：
  生产日期、失效日期、有效期至、cExpirationdate 被桥清空（非保质期存货），批次属性 10（日期）按 yyyy-mm-ddThh:mi:ss
  写入后与档案相同；批号 2 优先挑属性 10 有值的，没有时在说明里注明日期属性未覆盖。
purchase-in-shelf：保质期管理存货的无来源采购入库（新批号、生产日期取当天、失效日期按存货保质期推算，不给有效期至）→
  回读：生产日期、失效日期照请求，保质期和单位取存货档案；U8 自己补的有效期至、cExpirationdate、推算方式
  只核对自洽（有效期至在生产日期与失效日期之间，cExpirationdate 与之相同）并写进判定说明 → 删除，现存量不留数量。
  「普通业务必有订单」打开时同 binned-purchase-in-delete 临时关闭、跑完写回。
stock-check-by-bin：货位仓库里结存分在各货位的存货建盘点单（账面不给）→ 账面数量 = 现存量 = 各货位存量合计，
  行不分货位；单号按卡片 0307 取号、表体 cCVCode 已填 → 删除。
shape-change-number：形态转换单新建（单号按卡片 0305、表体 cAVCode 已填）→ 审核 → 生成的转换入库 / 转换出库单号
  分别与卡片 0301 / 0302 的流水对得上 → 弃审（生成单随之删除）→ 删除。
transfer-closer：调拨申请单（申请、核准各 2；单号按卡片 0324、表体 cTVCode 已填）→ 审核 → 一次生成全部核准数量的调拨单 →
  表头 cTranRequestCode = 申请单号、行带 iTRIds、累计调拨 2；行关闭人 cBCloser / 表头关闭人在生单后、删调拨单后各是什么
  写进判定说明（待实测项）→ 删调拨单（累计回 0）→ 若 U8 自动关闭了，清掉本单的关闭人 → 弃审、删除申请单。

不做 sale-out-compensate：第二步失败要靠坏货位、跨仓库或删行，前两者桥在生成前就拒绝，跨仓库要特定数据，
没有不注入故障就能稳定触发的办法。挑不到数据记 N/A；清理按建的倒序（_guard），清理失败记 FAIL。
步骤 kind 为 "custom"，sql_name 是操作名（STOCK_MORE_OPS 并入 ops_run）。
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_other_docs_sql import sm_av_sql, sm_sql
from co.e2e.casespec.stock_plus import _SALE_ROUTES, _SALE_TAGS, _dec, _Fail, _guard, _Run, _sale_chain
from co.e2e.casespec.stock_plus_sql import havepo_sql, safe_lot
from co.e2e.casespec.transfer_gen_sql import tg_tr_sql
from co.e2e.casespec.stock_binned_sql import head_rows_sql, havepo_value_sql, set_havepo_sql
from co.e2e.casespec.stock_more_sql import (
    batch_dates_pick_sql,
    bin_check_pick_sql,
    bin_sums_sql,
    body_code_sql,
    check_gone_sql,
    check_rows_sql,
    head_number_sql,
    lot_dates_sql,
    made_card,
    made_number_sql,
    number_card,
    reopen_sql,
    req_state_sql,
    shelf_pick_sql,
    shelf_rows_sql,
    shelf_stock_sql,
    tv_state_sql,
)
from co.e2e.testdata import value as td

_CREATE = "/v1/vouchers/create"
_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_MEMO = "CO桥回归"
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\Z")
_TR_RD = ("stock.transfer_out_rd", "stock.transfer_in_rd")


# ---- 单号 ----


def _number_problem(run: _Run, query: str, card: str) -> tuple[str, str]:
    """（问题, 单号）：卡片 card 里有一条流水行的种子出现在单号里、单号以它的 cNumber 结尾。
    单号后面括注对上的「种子/流水号」，写进判定说明。"""
    parts = run.pick(query, 4)
    if parts is None:
        return "回读单号与卡片 " + card + " 的流水失败", ""
    code, rows, hit, matched = parts
    if not code:
        return "单号为空（卡片 " + card + "）", ""
    if rows == "0":
        return f"卡片 {card} 没有编号流水，单号 {code} 不是按该卡片取的", code
    if hit == "0":
        return f"单号 {code} 与卡片 {card} 的流水（{rows} 行）对不上：没有种子在单号里、流水号是单号末尾的行", code
    return "", f"{code}（流水 {matched}）"


def _head_code(run: _Run, kind: str, doc: int) -> tuple[str, str]:
    """新建单据的单号按登记的卡片取号，表体单号列都等于表头单号。"""
    problem, code = _number_problem(run, head_number_sql(kind, str(doc)), number_card(kind))
    if problem:
        return problem, code
    rows, same = (run.cell(body_code_sql(kind, str(doc))).split("|") + ["", ""])[:2]
    if rows in ("", "0") or rows != same:
        return f"{kind} {code} 的表体单号列只有 {same or '?'} / {rows or '?'} 行等于表头单号", code
    return "", code


# ---- 销售出库改批号后的日期列 ----


def _dates_check(seen: list[str]) -> Callable[[_Run, int, str], str]:
    def check(run: _Run, out: int, line: str) -> str:
        rows = run.cells(lot_dates_sql(str(out), line))
        if not rows:
            return "回读不到出库行的批号"
        for row in rows:
            parts = row.split("|")
            if len(parts) != 10:
                return "日期列回读格式不对：" + row[:80]
            problem = _lot_dates_problem(parts)
            if problem:
                return problem
            seen.append(parts[8])
        return ""

    return check


def _lot_dates_problem(parts: list[str]) -> str:
    lot, made, valid, _mass, _unit, cexp, dexp, _calc, prop, arc = parts
    if made or valid or dexp or cexp:
        return f"批号 {lot} 的生产日期 / 失效日期 / 有效期至没被清空：{made or '空'} / {valid or '空'} / {dexp or cexp or '空'}"
    if prop != arc:
        return f"批号 {lot} 的批次属性 10 是 {prop or '空'}，档案是 {arc or '空'}"
    if prop and _ISO.fullmatch(prop) is None:
        return f"批号 {lot} 的批次属性 10 回读不是 yyyy-mm-ddThh:mi:ss：{prop}"
    return ""


def _batch_dates_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(batch_dates_pick_sql(), 9)
    if parts is None:
        return "N/A", "账套挑不到有两个可用批号的可销售批次存货"
    seen: list[str] = []
    status, detail = _sale_chain(run, parts, safe_lot(parts[6]), [(1, safe_lot(parts[7]), "")], _dates_check(seen))
    dated = [value for value in seen if value]
    if dated:
        return status, detail + f"；日期列已清空，批次属性 10 = {dated[0]}（与档案相同）"
    return status, detail + "；日期列已清空；档案没有日期属性，属性 10 的写法未覆盖"


# ---- 保质期存货的无来源采购入库 ----


def _valid_until(made: str, mass: int, unit: str) -> str:
    """失效日期 = 生产日期 + 保质期（单位 1 年、2 月、3 日）。"""
    start = date.fromisoformat(made)
    if unit == "3":
        return (start + timedelta(days=mass)).isoformat()
    months = start.month - 1 + (mass * 12 if unit == "1" else mass)
    year, month = start.year + months // 12, months % 12 + 1
    day = start.day
    while True:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            day -= 1


def _with_havepo(run: _Run, body: Callable[[], tuple[str, str]]) -> tuple[str, str]:
    # 同 binned-purchase-in-delete：「普通业务必有订单」打开时在快照里临时关掉，跑完原样写回（写回失败记 FAIL）。
    if run.cell(havepo_sql()) != "1":
        return body()
    original = run.cell(havepo_value_sql())
    if original not in ("True", "TRUE", "true", "1") or run.cell(set_havepo_sql("False")) != "1":
        return "N/A", "采购选项「普通业务必有订单」打开且无法临时关闭，不能无来源录入采购入库单"
    try:
        result = body()
    finally:
        restored = run.cell(set_havepo_sql(original)) == "1"
    if not restored:
        return "FAIL", "采购选项「普通业务必有订单」没能写回原值（留给快照还原）"
    return result[0], result[1] + "（「普通业务必有订单」临时关闭后已写回）"


def _shelf_problem(row: str, want: tuple[str, ...]) -> str:
    """want = （批号, 生产日期, 失效日期, 保质期, 保质期单位）。"""
    parts = row.split("|")
    if len(parts) != 8:
        return "采购入库行回读格式不对：" + row[:80]
    lot, made, valid, mass, unit, cexp, dexp, _calc = parts
    if (lot.upper(), made, valid) != (want[0].upper(), want[1], want[2]):
        return f"批号 / 生产日期 / 失效日期应为 {want[0]} / {want[1]} / {want[2]}，实际 {lot} / {made} / {valid}"
    if _dec(mass or "0") != _dec(want[3]) or unit != want[4]:
        return f"保质期应取存货档案 {want[3]}（单位 {want[4]}），实际 {mass or '空'}（单位 {unit or '空'}）"
    if dexp and not made <= dexp <= valid:
        return f"U8 补的有效期至 {dexp} 不在生产日期 {made} 与失效日期 {valid} 之间"
    if cexp and cexp[:10] != dexp:
        return f"cExpirationdate {cexp} 与有效期至 {dexp or '空'} 不一致"
    return ""


def _shelf_run(run: _Run, parts: list[str]) -> tuple[str, str]:
    wh, inv, ven, dep, ptype = (safe_lot(part) for part in parts[:5])
    made = run.env.today
    want = ("E2ESL" + secrets.token_hex(4).upper(), made, _valid_until(made, int(parts[5]), parts[6]), parts[5], parts[6])
    head = {"cwhcode": wh, "cvencode": ven, "cdepcode": dep, "cptcode": ptype, "ddate": made, "cmemo": _MEMO}
    line = {"cinvcode": inv, "iquantity": 1, "iunitcost": 1, "itaxrate": 13, "cbatch": want[0],
            "dmadedate": made, "dvdate": want[2]}
    doc = run.made(_CREATE, {"type": "purchase_in", "head": head, "lines": [line]}, "新建保质期存货的无来源采购入库")
    run.drop("purchase_in", doc)
    rows = run.cells(shelf_rows_sql(str(doc)))
    saved = run.cell(shelf_stock_sql(wh, inv, want[0]))
    # 先删除，再判回读，断言失败也不会把单据留在库里。
    run.settle(1)
    problem = _shelf_problem(rows[0], want) if len(rows) == 1 else f"采购入库应有 1 行，实际 {len(rows)} 行"
    left = (run.cell(shelf_stock_sql(wh, inv, want[0])).split("|") + ["", ""])[1]
    if not problem and run.cell(head_rows_sql("01", str(doc))) != "0":
        problem = "删除后采购入库单表头仍在"
    if not problem and _dec(left or "0") != 0:
        problem = f"删除后批号 {want[0]} 的现存量仍有 {left}"
    if problem:
        raise _Fail(problem)
    fields = rows[0].split("|")
    return "PASS", (
        f"采购入库 {doc}（{wh} / {inv} / 批号 {want[0]}）：生产日期 {made}、失效日期 {want[2]} 照写，保质期取档案；"
        f"U8 补的有效期至 {fields[6] or '空'}、cExpirationdate {fields[5] or '空'}、推算方式 {fields[7] or '空'}；"
        f"保存后现存量（行数|数量|失效|有效期至）{saved or '无'}；删除后回退"
    )


def _shelf_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(shelf_pick_sql(), 7)
    if parts is None:
        return "N/A", "账套挑不到可无来源入库的保质期管理存货（外购、免检、档案有保质期）"
    return _with_havepo(run, lambda: _shelf_run(run, parts))


# ---- 货位仓库的盘点单 ----


def _bin_check_problem(rows: list[str], sums: list[str]) -> str:
    if len(rows) != 1 or len(rows[0].split("|")) != 3:
        return f"盘点单应有 1 行，实际 {rows[:3]}"
    book = _dec(rows[0].split("|")[1])
    stock, bins = _dec(sums[0]), _dec(sums[1])
    if not book == stock == bins:
        return f"账面数量 {book} 应等于现存量 {stock} 和各货位存量合计 {bins}（{sums[2] or '无货位存量'}）"
    return ""


def _bin_check_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(bin_check_pick_sql(), 4)
    if parts is None:
        return "N/A", "账套挑不到货位仓库里货位存量与现存量一致、且没有未审核盘点单的存货"
    wh, inv = safe_lot(parts[0]), safe_lot(parts[1])
    head = {"cWhCode": wh, "dCVDate": run.env.today, "cCVMemo": _MEMO}
    body = {"type": "stock_check", "head": head, "lines": [{"cInvCode": inv, "iCVCQuantity": 1}]}
    doc = run.made(_CREATE, body, "新建货位仓库的盘点单")
    run.drop("stock_check", doc)
    sums = run.pick(bin_sums_sql(wh, inv), 3)
    rows = run.cells(check_rows_sql(str(doc)))
    numbered, code = _head_code(run, "stock_check", doc)
    run.settle(1)
    problem = "回读现存量与货位存量失败" if sums is None else _bin_check_problem(rows, sums)
    problem = problem or numbered
    if not problem and run.cell(check_gone_sql(str(doc))) != "0":
        problem = "删除后盘点单表头仍在"
    if problem:
        raise _Fail(problem)
    pos = rows[0].split("|")[2]
    return "PASS", (
        f"盘点单 {code}（{wh} / {inv}）：账面 = 现存量 = 各货位合计 {sums[1]}（{sums[2]}），"
        f"行不分货位（cPosition {pos or '空'}）；单号按卡片 0307、表体单号已填；删除后不在"
    )


# ---- 形态转换单与生成单的单号 ----


def _shape_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(sm_av_sql(), 4)
    if parts is None:
        return "N/A", "账套挑不到同一仓库里两种有现存量的简单存货和末级部门"
    wh, src, dst, dep = (safe_lot(part) for part in parts)
    before = {"cInvCode": src, "cWhCode": wh, "bAVType": "转换前", "iGroupNO": 1, "iAVQuantity": 1}
    after = {"cInvCode": dst, "cWhCode": wh, "bAVType": "转换后", "iGroupNO": 1, "iAVQuantity": 1}
    head = {"dAVDate": run.env.today, "cDepCode": dep, "cAVMemo": _MEMO}
    doc = run.made(_CREATE, {"type": "shape_change", "head": head, "lines": [before, after]}, "新建形态转换单")
    run.drop("shape_change", doc)
    problems = [_head_code(run, "shape_change", doc)]
    run.ok(_VERIFY, {"type": "shape_change", "id": doc, "action": "verify"}, "审核形态转换单")
    run.unverify("shape_change", doc)
    kids = run.cell(sm_sql("sm_av_kids", str(doc), {}))
    for rd in ("08", "09"):
        problems.append(_number_problem(run, made_number_sql(rd, str(doc)), made_card(rd)))
    run.settle(2)
    first = next((text for text, _code in problems if text), "")
    if not first and kids != "2":
        first = f"审核应生成转换入库、转换出库各 1 张，实际 {kids or '空'} 张"
    gone = (run.cell(sm_sql("sm_av_kids", str(doc), {})), run.cell(sm_sql("sm_av_gone", str(doc), {})))
    if not first and gone != ("0", "0"):
        first = f"弃审删除后生成单、形态转换单应为 0、0，实际 {gone}"
    if first:
        raise _Fail(first)
    codes = [code for _text, code in problems]
    return "PASS", (
        f"形态转换单 {codes[0]}（卡片 0305，表体 cAVCode 已填）、转换入库 {codes[1]}（0301）、转换出库 {codes[2]}（0302）"
        "的单号都与编号流水对得上；弃审删除后不在"
    )


# ---- 调拨申请单全部调完后的关闭人 ----


def _req_state(run: _Run, req: int) -> list[str]:
    parts = run.pick(req_state_sql(str(req)), 6)
    if parts is None:
        raise _Fail("回读调拨申请单失败")
    return parts


def _closers(state: list[str]) -> str:
    return f"行关闭人 {state[4] or '空'}、表头关闭人 {state[5] or '空'}"


def _tr_problem(state: list[str], link: list[str] | None, gen: list[str], dropped: list[str]) -> str:
    if link is None or link[0] != state[0]:
        return f"调拨单表头 cTranRequestCode 应为申请单号 {state[0]}，实际 {link[0] if link else '回读失败'}"
    if link[1] in ("", "0") or link[1] != link[2]:
        return f"调拨单 {link[2]} / {link[1]} 行带 iTRIds"
    if _dec(gen[3]) != Decimal(2) or _dec(dropped[3]) != 0:
        return f"累计调拨数量生单后应为 2、删调拨单后应为 0，实际 {gen[3]}、{dropped[3]}"
    return ""


def _tr_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(tg_tr_sql(), 3)
    if parts is None:
        return "N/A", "账套挑不到两个不管货位的仓库和调出仓库里有现存量的简单存货（或允许超调拨申请调拨）"
    src, dst, inv = (safe_lot(part) for part in parts)
    head = {"dTVDate": run.env.today, "cOWhCode": src, "cIWhCode": dst, "cTVMemo": _MEMO,
            "cORdCode": td(_TR_RD[0]), "cIRdCode": td(_TR_RD[1])}
    line = {"cInvCode": inv, "iTVQuantity": 2, "iTvChkQuantity": 2}
    req = run.made(_CREATE, {"type": "transfer_request", "head": head, "lines": [line]}, "新建调拨申请单")
    run.drop("transfer_request", req)
    numbered = _head_code(run, "transfer_request", req)[0]
    run.ok(_VERIFY, {"type": "transfer_request", "id": req, "action": "verify"}, "审核调拨申请单")
    run.unverify("transfer_request", req)
    state = _req_state(run, req)
    gen = {"type": "transfer", "id": req, "source_type": "transfer_request",
           "head": {"dTVDate": run.env.today, "cMemo": _MEMO}, "lines": [{"source_line_id": int(state[1]), "quantity": 2}]}
    tv = run.made(_GEN, gen, "参照调拨申请单生成全部核准数量的调拨单")
    run.drop("transfer", tv)
    link = run.pick(tv_state_sql(str(tv)), 3)
    after_gen = _req_state(run, req)
    run.settle(1)
    after_drop = _req_state(run, req)
    reopened = after_drop[4] != "" or after_drop[5] != ""
    if reopened and run.cell(reopen_sql(str(req))) in ("", "0"):
        raise _Fail("U8 关闭了调拨申请单，清关闭人失败，申请单留给清理和快照还原")
    problem = numbered or _tr_problem(state, link, after_gen, after_drop)
    if problem:
        raise _Fail(problem)
    note = "；关闭人由用例清空后再弃审删除" if reopened else ""
    return "PASS", (
        f"调拨单 {tv}：cTranRequestCode = 申请单号 {state[0]}，累计调拨 2 → 删除后 0；"
        f"调完核准数量后{_closers(after_gen)}，删调拨单后{_closers(after_drop)}{note}"
    )


# ---- 接线 ----


def sale_out_batch_dates(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _batch_dates_body)


def purchase_in_shelf(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _shelf_body)


def stock_check_by_bin(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _bin_check_body)


def shape_change_number(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _shape_body)


def transfer_closer(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _tr_body)


STOCK_MORE_OPS = {
    "sale_out_batch_dates": sale_out_batch_dates,
    "purchase_in_shelf": purchase_in_shelf,
    "stock_check_by_bin": stock_check_by_bin,
    "shape_change_number": shape_change_number,
    "transfer_closer": transfer_closer,
}


def _case(name: str, title: str, op: str, tags: frozenset[str], routes: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=tags, routes=routes)


def stock_more_cases() -> list[CaseSpec]:
    # 都动现存量、货位存量或单据号（stock、new:*），与其他库存用例互斥。
    shape_tags = ("new:shape_change", "w:shape_change", "new:other_in", "new:other_out", "stock")
    tr_tags = ("new:transfer_request", "w:transfer_request", "new:transfer", "w:transfer", "stock")
    return [
        _case("sale-out-batch-dates", "销售出库生单改批号：换批号的行日期列清空、批次属性 10 按 ISO 日期写入并与档案相同",
              "sale_out_batch_dates", _SALE_TAGS, _SALE_ROUTES),
        _case("purchase-in-shelf", "保质期存货的无来源采购入库：生产 / 失效日期照写、保质期取档案，U8 补的有效期至自洽",
              "purchase_in_shelf", frozenset(("opt:havepo", "new:purchase_in", "w:purchase_in", "stock")),
              (_CREATE, _DELETE)),
        _case("stock-check-by-bin", "货位仓库的盘点单：账面数量 = 现存量 = 各货位存量合计，行不分货位，单号按卡片 0307",
              "stock_check_by_bin", frozenset(("new:stock_check", "w:stock_check", "stock")), (_CREATE, _DELETE)),
        _case("shape-change-number", "形态转换单及审核生成的转换入库 / 出库：单号与编号卡片流水一致，表体单号已填",
              "shape_change_number", frozenset(shape_tags), (_CREATE, _VERIFY, _DELETE)),
        # 收发类别取测试数据（同 transfer-from-request），缺键时用例记 SKIP。
        replace(_case("transfer-closer",
                      "调拨申请单全部调完：cTranRequestCode 为申请单号、累计调拨回写，记下 U8 是否自动写关闭人",
                      "transfer_closer", frozenset(tr_tags), (_CREATE, _VERIFY, _GEN, _DELETE)), data=_TR_RD),
    ]
