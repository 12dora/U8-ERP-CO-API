"""造数 ato：生产订单参照销售订单生成（ATO）用例的固定数据。一个步骤，重跑时已有的都跳过。

1. 存货（data.ATO_ROLES）：两个选项子件、选项类、ATO 模型、必选子件、普通 ATO 件，按模板复制，再用 SQL 改标志（每次都覆盖）。
2. 物料清单（vouchers/create type bom，再审核）：选项类 = 两个选项子件，都 optional、mutex_rule 1（单选）；
   ATO 模型 = 必选子件（领用）+ 选项类（wip_type 4 虚拟件；U8 不要求模型下的选项类选配，不打 optional）；
   普通 ATO 件 = 必选子件。
   选项类物料清单审核前核对有 2 行选配：没有（桥不支持 optional）就停下，留着不审核；重跑时先弃审、删掉这样的旧单再重建。
3. 销售订单（客户 C0001，表头备注 seed:ato）：第 1 行普通 ATO 件 5 个（不选配），第 2 行 ATO 模型 3 个。先只保存。
4. 第 2 行的选配结果选 ATO_OPTION_A：主键经 comprobe 探针用 U8 的 sp_GetID 取（id_steps），再用会话 SQL 写最少的几行
   （cbom_sql，理由见 steps_docs_ato_sql）。U8 的选配组件 UFU8MBOMISrv.clsBom（ATOCBomEdit / ATOCBomSave）要逐行改
   ADO 记录集再存，comprobe 脚本不能按行循环、记录集的列结构也没有实测，所以只借 U8 的取号，不走它保存。
   写之前先清掉上次半途留下的状态（repair_sql：行指着没有子件的选配、没有子件也没人指着的造数选配表头）。
5. 有了选配再审核销售订单（U8 实测：ATO 模型件的行没有选配不能审核）。重跑时订单已保存未选配、已选配未审核、已审核
   都从断点接着走（选配按行上 iCusBomID 认，审核按审核人认）。
6. 测试数据键 ato.*（so_id、line_plain、line_cbom、cbom_id、model、std、option、base、mo_type、dept）合并进通道的测试数据文件。
"""

from __future__ import annotations

from pathlib import Path

from co.e2e.seed import data, emit
from co.e2e.seed.base import Probe, Step, ensure_archives, existing_codes, quoted
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.steps_docs_ato_sql import (
    BOM_OF_SQL,
    CBOM_KEY,
    CBOM_OF_LINE_SQL,
    OPCOMP_SQL,
    OPTIONAL_SQL,
    PART_SQL,
    CbomPart,
    Stamp,
    cbom_sql,
    id_steps,
    parse_ids,
    repair_sql,
)
from co.e2e.seed.steps_docs_base import (
    VERIFY,
    create_doc,
    ensure_verified,
    find_ids,
    find_marked,
    is_verified,
    line_ids,
    marker,
    plus_days,
    post_ok,
    safe_id,
    taxed_amounts,
)
from co.e2e.seed.steps_docs_mo import MO_DEPT, _mo_type
from co.e2e.seed.steps_docs_sale import CUSTOMER, SO_LINES_SQL, SO_PRICE, so_body
from co.e2e.seed.steps_inventory import flags_sql

WH = data.WH_PLAIN[0]
SO_KEY = "ato"
SO_LINES_INV_SQL = "SELECT iSOsID AS id, cInvCode AS inv FROM SO_SODetails WHERE ID = {id} ORDER BY iSOsID"
MUTEX_ONE = 1
WIP_VIRTUAL = 4
WIP_ISSUE = 3


# ---- 正文（纯函数） ----


def class_bom_body() -> dict:
    head = {"inv_code": data.ATO_CLASS, "version_desc": marker("ato_class"), "eff_date": data.SEED_DATE, "parent_scrap": 0}
    lines = [
        {"inv_code": code, "base_qty_n": 1, "base_qty_d": 1, "wip_type": WIP_ISSUE, "wh_code": WH, "optional": True,
         "mutex_rule": MUTEX_ONE}
        for code in (data.ATO_OPTION_A, data.ATO_OPTION_B)
    ]
    return {"type": "bom", "head": head, "lines": lines}


def model_bom_body() -> dict:
    head = {"inv_code": data.ATO_MODEL, "version_desc": marker("ato_model"), "eff_date": data.SEED_DATE, "parent_scrap": 0}
    lines = [
        {"inv_code": data.ATO_BASE, "base_qty_n": 1, "base_qty_d": 1, "wip_type": WIP_ISSUE, "wh_code": WH},
        {"inv_code": data.ATO_CLASS, "base_qty_n": 1, "base_qty_d": 1, "wip_type": WIP_VIRTUAL},
    ]
    return {"type": "bom", "head": head, "lines": lines}


def std_bom_body() -> dict:
    head = {"inv_code": data.ATO_STD, "version_desc": marker("ato_std"), "eff_date": data.SEED_DATE, "parent_scrap": 0}
    lines = [{"inv_code": data.ATO_BASE, "base_qty_n": 1, "base_qty_d": 1, "wip_type": WIP_ISSUE, "wh_code": WH}]
    return {"type": "bom", "head": head, "lines": lines}


def ato_so_body(day: str) -> dict:
    body = so_body(day, SO_KEY)
    lines = []
    for inv, qty in data.ATO_SO_LINES:
        line: dict = {"cinvcode": inv, "iquantity": qty}
        line.update(taxed_amounts(qty, SO_PRICE, data.TAX_RATE))
        line["dpredate"] = plus_days(day, 7)
        line["dpremodate"] = plus_days(day, 7)
        lines.append(line)
    body["lines"] = lines
    return body


def testdata_payload(so: int, lines: list[int], cbom: int, motype: str) -> dict:
    ato = {
        "so_id": so,
        "line_plain": lines[0],
        "line_cbom": lines[1],
        "cbom_id": cbom,
        "model": data.ATO_MODEL,
        "std": data.ATO_STD,
        "option": data.ATO_OPTION_A,
        "base": data.ATO_BASE,
        "mo_type": motype,
        "dept": MO_DEPT,
    }
    return {"ato": ato}


# ---- 查找 ----


def _one_id(ctx: SeedContext, sql: str, what: str) -> int:
    found = find_ids(ctx, sql)
    if not found:
        raise SeedError("查不到" + what, saved=ctx.last_call)
    return found[0]


def find_bom(ctx: SeedContext, code: str) -> list[int]:
    return find_ids(ctx, BOM_OF_SQL.format(code=quoted([code])))


def find_so(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "sale_order", SO_KEY)


def cbom_of(ctx: SeedContext, line: int) -> int:
    """销售订单行已有的选配结果主键；没有、或主键指向的选配没有子件时为 0。"""
    rows = ctx.rows(CBOM_OF_LINE_SQL.format(line=safe_id(line)))
    if not rows or rows[0].get("n", "0").strip() in ("", "0"):
        return 0
    return safe_id(rows[0].get("id", ""))


# ---- 各段 ----


def _inventories(ctx: SeedContext) -> tuple[int, int]:
    new, existed = ensure_archives(ctx, data.ATO_INVENTORIES)
    ctx.exec(flags_sql(data.ATO_ROLES))
    return new, existed


def _bom(ctx: SeedContext, code: str, body: dict) -> tuple[int, int]:
    found = find_bom(ctx, code)
    bom = found[0] if found else create_doc(ctx, body)
    ensure_verified(ctx, "bom", bom)
    return bom, 0 if found else 1


def _optional_ok(ctx: SeedContext, bom: int) -> bool:
    rows = ctx.rows(OPTIONAL_SQL.format(bom=safe_id(bom)))
    return bool(rows) and rows[0].get("n", "").strip() == "2"


def _drop_bom(ctx: SeedContext, bom: int) -> None:
    what = "选项类物料清单 " + str(bom)
    if is_verified(ctx, "bom", bom):
        post_ok(ctx, VERIFY, {"type": "bom", "id": bom, "action": "unverify"}, "弃审" + what)
    post_ok(ctx, "v1/vouchers/delete", {"type": "bom", "id": bom}, "删除" + what)


def _class_bom(ctx: SeedContext) -> int:
    # 上次留下的没有选配标志的选项类物料清单删掉重建；新建后审核前核对，桥不支持 optional 时留着不审核、停下。
    found = find_bom(ctx, data.ATO_CLASS)
    if found and not _optional_ok(ctx, found[0]):
        _drop_bom(ctx, found[0])
        found = []
    bom = found[0] if found else create_doc(ctx, class_bom_body())
    if not _optional_ok(ctx, bom):
        text = "选项类物料清单 " + str(bom) + " 的子件没有打上选配标志（桥要支持 optional / mutex_rule）；未审核，重跑时删掉重建"
        raise SeedError(text, saved=ctx.last_call)
    ensure_verified(ctx, "bom", bom)
    return 0 if found else 1


def _boms(ctx: SeedContext) -> tuple[int, int]:
    new_cls = _class_bom(ctx)
    _model, new_model = _bom(ctx, data.ATO_MODEL, model_bom_body())
    _std, new_std = _bom(ctx, data.ATO_STD, std_bom_body())
    new = new_cls + new_model + new_std
    return new, 3 - new


def _sale_order(ctx: SeedContext) -> tuple[int, list[int], int]:
    """保存（不审核）销售订单，核对两行的存货：（订单主键, 两行 iSOsID, 是否新建）。"""
    found = find_so(ctx)
    so = found[0] if found else create_doc(ctx, ato_so_body(ctx.day))
    rows = ctx.rows(SO_LINES_INV_SQL.format(id=safe_id(so)))
    got = [(row.get("inv", "").strip(), row.get("id", "")) for row in rows]
    if [inv for inv, _id in got] != [inv for inv, _qty in data.ATO_SO_LINES]:
        raise SeedError("ATO 销售订单 " + str(so) + " 的行存货是 " + str([inv for inv, _id in got])
                        + "，应为 " + str([inv for inv, _qty in data.ATO_SO_LINES]) + "（旧版造数留下的？删掉这张订单后重跑）")
    return so, [safe_id(line) for _inv, line in got], 0 if found else 1


def _cbom_parts(ctx: SeedContext) -> list[CbomPart]:
    model_bom, class_bom = find_bom(ctx, data.ATO_MODEL)[0], find_bom(ctx, data.ATO_CLASS)[0]
    parts = []
    for code, bom in ((data.ATO_BASE, model_bom), (data.ATO_OPTION_A, class_bom)):
        part = _one_id(ctx, PART_SQL.format(code=quoted([code])), "物料 " + code)
        opcomp = _one_id(ctx, OPCOMP_SQL.format(bom=safe_id(bom), code=quoted([code])), "物料清单子件 " + code)
        parts.append(CbomPart(part, opcomp, WH))
    return parts


def _cbom(ctx: SeedContext, line: int) -> tuple[int, int]:
    existing = cbom_of(ctx, line)
    if existing:
        return existing, 0
    ctx.exec(repair_sql(line))
    model = _one_id(ctx, PART_SQL.format(code=quoted([data.ATO_MODEL])), "物料 " + data.ATO_MODEL)
    parts = _cbom_parts(ctx)
    ids = parse_ids(ctx.probe(id_steps(len(parts)), "sp-getid-cbom"), len(parts))
    ctx.exec(cbom_sql(line, model, parts, ids, Stamp(ctx.operator, ctx.day)))
    made = cbom_of(ctx, line)
    if made != ids.head:
        text = ("销售订单行 " + str(line) + " 写选配结果后查到 " + str(made) + "，应为 " + str(ids.head)
                + "（行被别的选配占用，或主键已存在）；核对该行 iCusBomID 和 bom_cbom 后重跑")
        raise SeedError(text, saved=ctx.last_call)
    return made, 1


def _write_testdata(ctx: SeedContext, payload: dict, cfg: object = None) -> None:
    cfg = cfg or _conf()
    target = Path(str(getattr(cfg, "testdata_file", "")))
    refused = emit.target_refusal(target, cfg)
    if refused:
        raise SeedError(refused)
    emit.write_lane(ctx, target, payload, cfg)


def _conf() -> object:
    from co.e2e.settings import conf

    return conf()


def apply(ctx: SeedContext, cfg: object = None) -> tuple[int, int]:
    new_inv, old_inv = _inventories(ctx)
    new_bom, old_bom = _boms(ctx)
    so, lines, new_so = _sale_order(ctx)
    cbom, new_cbom = _cbom(ctx, lines[1])
    # ATO 模型件的行有了选配才能审核（U8 拒绝「ATO+模型件…未选配,不能审核」）；已审核的不再审。
    ensure_verified(ctx, "sale_order", so)
    ctx.state.setdefault("docs", {})[SO_KEY] = [so]
    _write_testdata(ctx, testdata_payload(so, lines, cbom, _mo_type(ctx)), cfg)
    new = new_inv + new_bom + new_so + new_cbom
    return new, old_inv + old_bom + (1 - new_so) + (1 - new_cbom)


def probe(ctx: SeedContext) -> Probe:
    have = existing_codes(ctx, "inventory", [arc.code for arc in data.ATO_INVENTORIES])
    existing = ["inventory:" + code for code in sorted(have)]
    missing = ["inventory:" + arc.code for arc in data.ATO_INVENTORIES if arc.code not in have]
    for code in (data.ATO_CLASS, data.ATO_MODEL, data.ATO_STD):
        (existing if find_bom(ctx, code) else missing).append("bom:" + code)
    so = find_so(ctx)
    (existing if so else missing).append("sale_order:" + marker(SO_KEY))
    lines = line_ids(ctx, SO_LINES_SQL, so[0]) if so else []
    cbom = cbom_of(ctx, lines[1]) if len(lines) > 1 else 0
    (existing if cbom else missing).append("cbom:" + marker(CBOM_KEY))
    audited = bool(so) and is_verified(ctx, "sale_order", so[0])
    (existing if audited else missing).append("verify:sale_order")
    return Probe(tuple(existing), tuple(missing))


STEPS = (Step("ato", "ATO 模型、选项类、物料清单、销售订单和选配结果（客户 " + CUSTOMER + "）", probe, apply),)
