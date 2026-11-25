"""造单据（六）：不良品处理单（QM05 / QM06）用例要的来源检验单。

来料一侧：qmr_po 采购订单（审核，订 2 个）→ qmr_arrival 到货单（到 2 个，审核）→ qmr_inspect 来料报检单（报 2 个，
U8 按选项自动审核）→ qmr_check 来料检验单（检 2 个：合格 1、不良 1）→ qmr_approve 走完 QM03 审批（同 qc_approve）。
产品一侧：qmr_mo 生产订单（BOM 母件 2 个，审核）→ qmr_pinspect 产品报检单（报 2 个）→ qmr_pcheck 产品检验单
（合格 1、不良 1）→ qmr_papprove 走完 QM04 审批（同 qm04_approve）。

两张检验单都已审核、BREJFLAG=0、不良数量 1，留给 qm-incoming-reject-cycle、qm-product-reject-cycle、
qm-reject-bad-request：用例自己生成、审核、弃审、删除不良品处理单，删除后 U8 把 BREJFLAG 改回 0，检验单可以反复用。
用例按到货单备注 seed:qmr_arrival、生产订单行备注 seed:qmr_mo 找回检验单（回归用例的挑单 SQL）。
检验单不入库：合格的 1 个不影响不良品处理单。

不良原因（Reason，iReasontype=1）由 steps_archives 的 qm_reason 步骤经原因码档案接口建，排在本组之前。
没把握的地方：检验单模板若把不良原因（CREASONCODE）设为必输，桥会 400 列出来，届时 --skip 这一组步骤（SKIP_QMR）。
"""

from __future__ import annotations

from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedContext
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
    safe_id,
    verify_after,
)
from co.e2e.seed.steps_docs_mo import MO_LINES_SQL, _mo_type, mo_body
from co.e2e.seed.steps_docs_purchase import PO_LINES_SQL, arrival_body
from co.e2e.seed.steps_docs_qc import (
    ARR_LINES_SQL,
    CHECK_SQL,
    INSPECT_LINES_SQL,
    approve_step,
    check_body,
    inspect_body,
    project_code,
    qc_po_body,
)
from co.e2e.seed.steps_docs_stock import BOM_PARENT

QTY = 2
DIS_QTY = 1
# 用例按这两个标记找回检验单（回归用例的挑单 SQL 里是同样的字符串）。
ARRIVAL_KEY = "qmr_arrival"
MO_KEY = "qmr_mo"
# 这一组步骤带不出来时一起跳过。
SKIP_QMR = "qmr_po,qmr_arrival,qmr_inspect,qmr_check,qmr_approve,qmr_mo,qmr_pinspect,qmr_pcheck,qmr_papprove"

_ARR_INSPECT_SQL = (
    "SELECT DISTINCT h.ID AS id FROM QMINSPECTVOUCHER AS h JOIN QMINSPECTVOUCHERS AS b ON b.ID = h.ID"
    " WHERE h.CVOUCHTYPE = N'QM01' AND b.SOURCEAUTOID IN (SELECT a.Autoid FROM PU_ArrivalVouchs AS a WHERE a.ID = {id})"
    " ORDER BY h.ID"
)
_MO_SQL = "SELECT DISTINCT MoId AS id FROM mom_orderdetail WHERE Remark = {mark} ORDER BY MoId"
_MO_INSPECT_SQL = (
    "SELECT DISTINCT h.ID AS id FROM QMINSPECTVOUCHER AS h JOIN QMINSPECTVOUCHERS AS b ON b.ID = h.ID"
    " WHERE h.CVOUCHTYPE = N'QM02' AND b.IPROORDERID = {id} ORDER BY h.ID"
)


# ---- 正文（纯函数） ----


def split_check_body(kind: str, inspect_id: int, line_id: int, project: str) -> dict:
    """检验 QTY 个：合格 QTY - DIS_QTY、不良 DIS_QTY（检验单的 fRegQuantity / fDisQuantity）。"""
    body = check_body(kind, inspect_id, line_id, QTY, project)
    body["lines"][0].update({"fRegQuantity": QTY - DIS_QTY, "fDisQuantity": DIS_QTY})
    return body


# ---- 查找 ----


def find_qmr_po(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "qmr_po")


def find_qmr_arrival(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "arrival", ARRIVAL_KEY)


def find_qmr_inspect(ctx: SeedContext) -> list[int]:
    arr = one_or_none(ctx, find_qmr_arrival)
    return find_ids(ctx, _ARR_INSPECT_SQL.format(id=safe_id(arr))) if arr else []


def find_qmr_check(ctx: SeedContext) -> list[int]:
    inspect = one_or_none(ctx, find_qmr_inspect)
    return find_ids(ctx, CHECK_SQL.format(biz="QM03", id=safe_id(inspect))) if inspect else []


def find_qmr_mo(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _MO_SQL.format(mark=quoted([marker(MO_KEY)])))


def find_qmr_pinspect(ctx: SeedContext) -> list[int]:
    mo = one_or_none(ctx, find_qmr_mo)
    return find_ids(ctx, _MO_INSPECT_SQL.format(id=safe_id(mo))) if mo else []


def find_qmr_pcheck(ctx: SeedContext) -> list[int]:
    inspect = one_or_none(ctx, find_qmr_pinspect)
    return find_ids(ctx, CHECK_SQL.format(biz="QM04", id=safe_id(inspect))) if inspect else []


# ---- 新建 ----


def _make_po(ctx: SeedContext) -> int:
    return create_doc(ctx, qc_po_body(ctx.day, "qmr_po", QTY))


def _make_arrival(ctx: SeedContext) -> int:
    po_id = need_one(ctx, find_qmr_po, "qmr_po")
    first = line_ids(ctx, PO_LINES_SQL, po_id)[0]
    return generate_doc(ctx, arrival_body(po_id, first, QTY, ARRIVAL_KEY))


def _make_inspect(ctx: SeedContext) -> int:
    arr = need_one(ctx, find_qmr_arrival, ARRIVAL_KEY)
    first = line_ids(ctx, ARR_LINES_SQL, arr)[0]
    return generate_doc(ctx, inspect_body("qm_incoming_inspect", "arrival", arr, first, QTY))


def _make_check(ctx: SeedContext) -> int:
    inspect = need_one(ctx, find_qmr_inspect, "qmr_inspect")
    first = line_ids(ctx, INSPECT_LINES_SQL, inspect)[0]
    return generate_doc(ctx, split_check_body("qm_incoming_check", inspect, first, project_code(ctx)))


def _make_mo(ctx: SeedContext) -> int:
    return create_doc(ctx, mo_body(ctx.day, _mo_type(ctx), BOM_PARENT, QTY, MO_KEY))


def _make_pinspect(ctx: SeedContext) -> int:
    mo = need_one(ctx, find_qmr_mo, MO_KEY)
    first = line_ids(ctx, MO_LINES_SQL, mo)[0]
    return generate_doc(ctx, inspect_body("qm_product_inspect", "production_order", mo, first, QTY))


def _make_pcheck(ctx: SeedContext) -> int:
    inspect = need_one(ctx, find_qmr_pinspect, "qmr_pinspect")
    first = line_ids(ctx, INSPECT_LINES_SQL, inspect)[0]
    return generate_doc(ctx, split_check_body("qm_product_check", inspect, first, project_code(ctx)))


STEPS = (
    doc_step(Doc("qmr_po", "不良品处理用采购订单（审核）", find_qmr_po, _make_po, verify_after("purchase_order"))),
    doc_step(Doc("qmr_arrival", "不良品处理用到货单（审核）", find_qmr_arrival, _make_arrival, verify_after("arrival"))),
    doc_step(Doc("qmr_inspect", "不良品处理用来料报检单（参照到货单）", find_qmr_inspect, _make_inspect)),
    doc_step(Doc("qmr_check", "来料检验单（合格 1、不良 1）", find_qmr_check, _make_check)),
    approve_step("qmr_approve", "不良品处理用来料检验单走完 QM03 审批", find_qmr_check, "qmr_check", "QM03"),
    doc_step(Doc("qmr_mo", "不良品处理用生产订单（BOM 母件 2 个，审核）", find_qmr_mo, _make_mo, verify_after("production_order"))),
    doc_step(Doc("qmr_pinspect", "不良品处理用产品报检单（参照生产订单）", find_qmr_pinspect, _make_pinspect)),
    doc_step(Doc("qmr_pcheck", "产品检验单（合格 1、不良 1）", find_qmr_pcheck, _make_pcheck)),
    approve_step("qmr_papprove", "不良品处理用产品检验单走完 QM04 审批", find_qmr_pcheck, "qmr_pcheck", "QM04"),
)
