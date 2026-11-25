"""造单据（五）：生产订单、材料出库、产品检验链（QM04）和产成品入库。

- mo：BOM 母件 10 个的生产订单（类别取 mom_motype 第一行，生产部门 SD003：回归挑检验员按生产部门找在职人员，
  检验员就在这个部门），备注写标记，审核（要 U8MPool）。U8 按 BOM 自动展开子件。
- mo_plain：免检母件 5 个的生产订单（QcFlag=0），审核，之后什么都不参照它生单：回归的 mo_free（弃审再审）、
  product-in-from-mo（不需检验、剩余 >= 2 的订单行）挑它。
- mo_out：参照 mo 的第一个子件领料 2 个生成材料出库（收发类别 data.MATERIAL_OUT_RD，仓库 06，表头备注写标记，
  生成后核对按标记能找回新单，找不回就停下，免得重跑重复生成），不审核；子件还剩 8 个给 mo_alloc 挑单（回归 mo_audited 要「已有领料」的订单，mo_alloc 优先挑它）。
- mo_inspect：参照订单行做两张产品报检单，每张 2 个（U8 按选项自动审核）。
- qm04：前两张报检单各生成一张产品检验单，**不提交**：wf-qm04-* 用例要两张未提交、受审批流控制的 QM04。
- mo_inspect3 → qm04_pass → qm04_approve → mo_pin：第三张产品报检单 4 个（主键记进 ctx.state，重跑时先认它，
  没有再按数量 4 找） → 产品检验单 4 个 → 走完 QM04 审批
  （同 qc_approve，逐节点改派给操作员对应的人员再同意）→ 参照它入库 2 个（收发类别 data.PRODUCT_IN_RD，仓库 06，
  不审核）。检验单还剩 2 个合格未入库给 qm_pin / se_pq 挑单，入库单给它们提供「该存货常用的产成品入库仓库」；
  订单还剩 2 个没报检给 qmp 用例挑。

没把握的地方：产品报检要订单行 QcFlag=1。U8 按母件存货的「质检」（Inventory.bPropertyCheck）带出，data.py 里
BOM 母件已打开它；带不出来时 mo_inspect 停下并提示，可以 --skip 整条质检链（SKIP_QC）。
材料出库、产成品入库的收发类别要先由 rd_styles 步骤建好（102 / 202）。
"""

from __future__ import annotations

from co.e2e.seed import data
from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.steps_docs_base import (
    Doc,
    create_doc,
    doc_step,
    find_ids,
    find_marked,
    generate_doc,
    line_ids,
    marker,
    need_one,
    one_or_none,
    plus_days,
    safe_id,
    verify_after,
)
from co.e2e.seed.steps_docs_qc import (
    CHECK_SQL,
    INSPECT_LINES_SQL,
    approve_step,
    check_body,
    inspect_body,
    project_code,
    wf_done,
)
from co.e2e.seed.steps_docs_stock import BOM_PARENT, PLAIN_BOM_PARENT

MO_QTY = 10
MO_PLAIN_QTY = 5
INSPECT_QTY = 2
INSPECTS = 2
OUT_QTY = 2
PASS_QTY = 4
PIN_QTY = 2
MO_DEPT = data.DEPT_PURCHASE
WH = data.WH_PLAIN[0]
# 第三张产品报检单（mo_inspect3）新建时记下的主键。
PASS_STATE_KEY = "mo_pass_inspect"
# 产品质检链带不出来时一起跳过的步骤。
SKIP_QC = "mo_inspect,qm04,mo_inspect3,qm04_pass,qm04_approve,mo_pin"

MOTYPE_SQL = "SELECT TOP (1) LTRIM(RTRIM(MotypeCode)) AS code FROM mom_motype ORDER BY MoTypeId"
_MO_SQL = "SELECT DISTINCT MoId AS id FROM mom_orderdetail WHERE Remark = {mark} ORDER BY MoId"
MO_LINES_SQL = "SELECT MoDId AS id FROM mom_orderdetail WHERE MoId = {id} ORDER BY MoDId"
ALLOC_SQL = (
    "SELECT a.AllocateId AS id FROM mom_moallocate AS a JOIN mom_orderdetail AS d ON d.MoDId = a.MoDId"
    " WHERE d.MoId = {id} ORDER BY a.AllocateId"
)
QC_FLAG_SQL = "SELECT COUNT(*) AS n FROM mom_orderdetail WHERE MoId = {id} AND ISNULL(QcFlag, 0) <> 1"
_INSPECT_SQL = (
    "SELECT DISTINCT h.ID AS id FROM QMINSPECTVOUCHER AS h JOIN QMINSPECTVOUCHERS AS b ON b.ID = h.ID"
    " WHERE h.CVOUCHTYPE = N'QM02' AND b.IPROORDERID = {id} ORDER BY h.ID"
)
PASS_INSPECT_SQL = (
    "SELECT DISTINCT h.ID AS id FROM QMINSPECTVOUCHER AS h JOIN QMINSPECTVOUCHERS AS b ON b.ID = h.ID"
    " WHERE h.CVOUCHTYPE = N'QM02' AND b.IPROORDERID = {id} AND b.FQUANTITY = {qty} ORDER BY h.ID"
)


# ---- 正文（纯函数） ----


def mo_body(day: str, mo_type: str, parent: str = BOM_PARENT, qty: int = MO_QTY, key: str = "mo") -> dict:
    line = {
        "inv_code": parent,
        "qty": qty,
        "start_date": day,
        "due_date": plus_days(day, 7),
        "mo_type": mo_type,
        "dept_code": MO_DEPT,
        "wh_code": WH,
    }
    return {"type": "production_order", "head": {"remark": marker(key)}, "lines": [line]}


def material_out_body(mo_id: int, alloc_id: int) -> dict:
    head = {"cwhcode": WH, "crdcode": data.MATERIAL_OUT_RD, "cmemo": marker("mo_out")}
    line = {"source_line_id": alloc_id, "quantity": OUT_QTY}
    return {"type": "material_out", "id": mo_id, "head": head, "lines": [line]}


def product_in_body(check_id: int) -> dict:
    head = {"cwhcode": WH, "crdcode": data.PRODUCT_IN_RD, "cmemo": marker("mo_pin")}
    line = {"source_line_id": check_id, "quantity": PIN_QTY}
    return {"type": "product_in", "id": check_id, "head": head, "lines": [line]}


# ---- 查找 ----


def find_mo(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _MO_SQL.format(mark=quoted([marker("mo")])))


def find_mo_plain(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _MO_SQL.format(mark=quoted([marker("mo_plain")])))


def find_mo_out(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "material_out", "mo_out")


def find_inspects(ctx: SeedContext) -> list[int]:
    mo = one_or_none(ctx, find_mo)
    return find_ids(ctx, _INSPECT_SQL.format(id=safe_id(mo))) if mo else []


def _check_of(ctx: SeedContext, inspect: int) -> list[int]:
    return find_ids(ctx, CHECK_SQL.format(biz="QM04", id=safe_id(inspect)))


def find_checks(ctx: SeedContext) -> list[int]:
    found: list[int] = []
    for inspect in find_inspects(ctx)[:INSPECTS]:
        found.extend(_check_of(ctx, inspect)[:1])
    return found


def find_pass_inspect(ctx: SeedContext) -> list[int]:
    # 新建时记下的那张（还在这张订单的报检单里）优先；没有记录就按数量 PASS_QTY 找（前两张是 INSPECT_QTY 个）。
    mo = one_or_none(ctx, find_mo)
    if not mo:
        return []
    known = ctx.state.get(PASS_STATE_KEY)
    if isinstance(known, int) and known in find_inspects(ctx):
        return [known]
    return find_ids(ctx, PASS_INSPECT_SQL.format(id=safe_id(mo), qty=PASS_QTY))[:1]


def find_pass_check(ctx: SeedContext) -> list[int]:
    inspect = one_or_none(ctx, find_pass_inspect)
    return _check_of(ctx, inspect)[:1] if inspect else []


def find_mo_pin(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "product_in", "mo_pin")


# ---- 新建 ----


def _mo_type(ctx: SeedContext) -> str:
    rows = ctx.rows(MOTYPE_SQL)
    code = rows[0].get("code", "").strip() if rows else ""
    if not code:
        raise SeedError("mom_motype 里没有生产订单类别（先在 U8 里建生产订单类别）")
    return code


def _make_mo(ctx: SeedContext) -> int:
    return create_doc(ctx, mo_body(ctx.day, _mo_type(ctx)))


def _make_mo_plain(ctx: SeedContext) -> int:
    return create_doc(ctx, mo_body(ctx.day, _mo_type(ctx), PLAIN_BOM_PARENT, MO_PLAIN_QTY, "mo_plain"))


def _generate_marked(ctx: SeedContext, kind: str, key: str, body: dict) -> int:
    # 生单后按标记找回新单；找不回说明 cmemo 没写进去，下次重跑按标记找不到会再生成一张。
    # 报错带生单那次调用的记录（find_marked 的 SQL 会改写 ctx.last_call，先记下）。
    doc_id = generate_doc(ctx, body)
    saved = ctx.last_call
    if doc_id not in find_marked(ctx, kind, key):
        raise SeedError(
            kind + " " + str(doc_id) + " 的造数标记没有写进单据（cmemo），重跑会重复生成", saved=saved
        )
    return doc_id


def _make_mo_out(ctx: SeedContext) -> int:
    mo = need_one(ctx, find_mo, "mo")
    first = line_ids(ctx, ALLOC_SQL, mo)[0]
    return _generate_marked(ctx, "material_out", "mo_out", material_out_body(mo, first))


def _qc_mo(ctx: SeedContext) -> int:
    mo = need_one(ctx, find_mo, "mo")
    rows = ctx.rows(QC_FLAG_SQL.format(id=safe_id(mo)))
    if rows and rows[0].get("n", "0").strip() not in ("", "0"):
        raise SeedError(
            "生产订单 " + str(mo) + " 有行 QcFlag 不是 1，不能做产品报检；"
            "检查母件存货的质检标志（bPropertyCheck）和库存选项 ST.bQuality，或 --skip " + SKIP_QC
        )
    return mo


def _make_inspect(ctx: SeedContext) -> int:
    mo = _qc_mo(ctx)
    first = line_ids(ctx, MO_LINES_SQL, mo)[0]
    return generate_doc(ctx, inspect_body("qm_product_inspect", "production_order", mo, first, INSPECT_QTY))


def _make_pass_inspect(ctx: SeedContext) -> int:
    if len(find_inspects(ctx)) < INSPECTS:
        raise SeedError("前两张产品报检单还没有（它们要留给 wf-qm04 用例），先跑 mo_inspect")
    mo = _qc_mo(ctx)
    first = line_ids(ctx, MO_LINES_SQL, mo)[0]
    made = generate_doc(ctx, inspect_body("qm_product_inspect", "production_order", mo, first, PASS_QTY))
    ctx.state[PASS_STATE_KEY] = made
    return made


def _make_pass_check(ctx: SeedContext) -> int:
    inspect = need_one(ctx, find_pass_inspect, "mo_inspect3")
    first = line_ids(ctx, INSPECT_LINES_SQL, inspect)[0]
    return generate_doc(ctx, check_body("qm_product_check", inspect, first, PASS_QTY, project_code(ctx)))


def _make_mo_pin(ctx: SeedContext) -> int:
    check = need_one(ctx, find_pass_check, "qm04_pass")
    if not wf_done(ctx, check):
        raise SeedError("产品检验单 " + str(check) + " 还没审批通过，先跑 qm04_approve")
    return _generate_marked(ctx, "product_in", "mo_pin", product_in_body(check))


def _make_check(ctx: SeedContext) -> int:
    for inspect in find_inspects(ctx)[:INSPECTS]:
        if _check_of(ctx, inspect):
            continue
        first = line_ids(ctx, INSPECT_LINES_SQL, inspect)[0]
        return generate_doc(ctx, check_body("qm_product_check", inspect, first, INSPECT_QTY, project_code(ctx)))
    raise SeedError("没有还缺检验单的产品报检单，先跑 mo_inspect")


STEPS = (
    doc_step(Doc("mo", "生产订单（BOM 母件，审核）", find_mo, _make_mo, verify_after("production_order"))),
    doc_step(
        Doc("mo_plain", "生产订单（免检母件，审核）", find_mo_plain, _make_mo_plain, verify_after("production_order"))
    ),
    doc_step(Doc("mo_out", "材料出库（参照生产订单领 2 个，不审核）", find_mo_out, _make_mo_out)),
    doc_step(Doc("mo_inspect", "产品报检单 ×2（参照生产订单）", find_inspects, _make_inspect, count=INSPECTS)),
    doc_step(Doc("qm04", "产品检验单 ×2（不提交审批）", find_checks, _make_check, count=INSPECTS)),
    doc_step(Doc("mo_inspect3", "第三张产品报检单（4 个）", find_pass_inspect, _make_pass_inspect)),
    doc_step(Doc("qm04_pass", "第三张产品检验单（参照第三张报检单）", find_pass_check, _make_pass_check)),
    approve_step("qm04_approve", "第三张产品检验单走完 QM04 审批", find_pass_check, "qm04_pass", "QM04"),
    doc_step(Doc("mo_pin", "产成品入库（参照已审批的产品检验单入 2 个，不审核）", find_mo_pin, _make_mo_pin)),
)
