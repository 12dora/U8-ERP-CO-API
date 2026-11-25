"""造单据（四）：来料检验链（来料检验件）。

qc_po（审核，订 6 个）→ qc_arrival（到 3 个，审核）→ qc_inspect 来料报检单（报 3 个，U8 按选项自动审核）→
qc_check 来料检验单（检验员 SD00301，检验方案取 QMCHECKPROJECT 第一个未停用的）→ qc_approve 走 QM03 审批流
（提交，再逐个把当前待办改派给操作员对应的人员后同意，直到通过）→ qc_in 参照检验单入库（审核）。
订单留 3 个没到货，给 qm01 用例挑；入库单给它提供「该存货常用的入库仓库」，检验单给它提供检验方案。

没把握的地方（现场核对，不行就 --skip qc_approve,qc_in 或整条链）：
- 报检单要靠 U8 的「来料报检单保存自动审核」选项（bArrInspectAutoVerify）才能生检验单。
- 检验方案不分来料 / 产品，取编码最小的一个；检验单模板若把别的字段设为必输，桥会 400 列出来。
- 某些账套的检验单模板把 chDefine11–13 设为必输，这里统一填 SEED。
- 审批要改派 Table_Task（同回归的 shape_qm04），操作员必须已在 UserHrPersonContro 里对上人员（operator_link 步骤）。

追加的第二条链 qc2_*（不改上面各步）：qc2_po 订 7 个 → qc2_arrival 到 4 个（审核）→ qc2_inspect 报 4 个 →
qc2_check 检 4 个 → qc2_approve（同 qc_approve 的 QM03 改派同意）→ qc2_in 只入库 1 个（审核）。
检验单剩余可入库 3 个：purchase-in-from-qm 要 >= 1，purchase-in-qm-update 要 >= 2；订单留 3 个没到货。
"""

from __future__ import annotations

from collections.abc import Callable

from co.e2e.seed import data
from co.e2e.seed.base import Probe, Step, quoted
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
    post_ok,
    safe_id,
    verify_after,
)
from co.e2e.seed.steps_docs_purchase import PO_LINES_SQL, arrival_body, po_body, po_line

QC_INVENTORY = data.INVENTORY_ROLES[4][0]
CHECKER = data.INSPECTORS[0]
WH = data.WH_PLAIN[0]
QC_PO_QTY = 6
QC_QTY = 3
MAX_ROUNDS = 6
DEFINE_VALUE = "SEED"
QC2_PO_QTY = 7
QC2_QTY = 4
QC2_IN_QTY = 1
_Find = Callable[[SeedContext], list[int]]
_Make = Callable[[SeedContext], int]

ARR_LINES_SQL = "SELECT Autoid AS id FROM PU_ArrivalVouchs WHERE ID = {id} ORDER BY Autoid"
INSPECT_LINES_SQL = "SELECT AUTOID AS id FROM QMINSPECTVOUCHERS WHERE ID = {id} ORDER BY AUTOID"
PROJECT_SQL = (
    "SELECT TOP (1) LTRIM(RTRIM(CPROJECTCODE)) AS code FROM QMCHECKPROJECT"
    " WHERE ISNULL(BSTOPUSE, 0) = 0 AND LEN(LTRIM(RTRIM(CPROJECTCODE))) BETWEEN 1 AND 30 ORDER BY CPROJECTCODE"
)
_INSPECT_SQL = (
    "SELECT DISTINCT h.ID AS id FROM QMINSPECTVOUCHER AS h JOIN QMINSPECTVOUCHERS AS b ON b.ID = h.ID"
    " WHERE h.CVOUCHTYPE = N'QM01' AND b.SOURCEAUTOID IN (SELECT a.Autoid FROM PU_ArrivalVouchs AS a WHERE a.ID = {id})"
    " ORDER BY h.ID"
)
CHECK_SQL = "SELECT ID AS id FROM QMCHECKVOUCHER WHERE CVOUCHTYPE = N'{biz}' AND INSPECTID = {id} ORDER BY ID"
WF_DONE_SQL = "SELECT COUNT(*) AS n FROM QMCHECKVOUCHER WHERE ID = {id} AND ISNULL(iVerifyStateNew, 0) = 2"

# 把单据当前节点 cUserID 最小的一条待办改派给操作员对应的人员（同 casespec/sql_shape.shape_qm04）。
REASSIGN_SQL = """DECLARE @person nvarchar(40);
SELECT TOP 1 @person = cPsn_Num FROM UserHrPersonContro WHERE cUser_Id = {operator};
UPDATE Table_Task SET cUserID = @person
WHERE @person IS NOT NULL AND cVoucherID = {id} AND RTRIM(cVoucherType) = N'{biz}'
  AND cTK_State = 0 AND cTaskType IN (1, 4)
  AND cUserID = (SELECT TOP 1 t.cUserID FROM Table_Task AS t WHERE t.cVoucherID = {id}
    AND RTRIM(t.cVoucherType) = N'{biz}' AND t.cTK_State = 0 AND t.cTaskType IN (1, 4) ORDER BY t.cUserID)"""


# ---- 正文（纯函数） ----


def qc_po_body(day: str, key: str = "qc_po", qty: int = QC_PO_QTY) -> dict:
    return po_body(day, key, [po_line(QC_INVENTORY, qty)])


def inspect_body(kind: str, source_type: str, source_id: int, line_id: int, qty: int) -> dict:
    line = {"source_line_id": line_id, "quantity": qty}
    return {"type": kind, "source_type": source_type, "id": source_id, "lines": [line]}


def check_head(project: str) -> dict:
    head = {"cCheckPersonCode": CHECKER, "project_code": project}
    for number in ("11", "12", "13"):
        head["chDefine" + number] = DEFINE_VALUE
    return head


def check_body(kind: str, inspect_id: int, line_id: int, qty: int, project: str) -> dict:
    line = {"source_line_id": line_id, "quantity": qty}
    return {"type": kind, "id": inspect_id, "head": check_head(project), "lines": [line]}


def qc_in_body(check_id: int, key: str = "qc_in", qty: int = QC_QTY) -> dict:
    head = {"cwhcode": WH, "cmemo": marker(key)}
    line = {"source_line_id": check_id, "quantity": qty}
    return {"type": "purchase_in", "source_type": "qm_incoming_check", "id": check_id, "head": head, "lines": [line]}


def reassign_sql(biz: str, doc_id: int, operator: str) -> str:
    if biz not in ("QM03", "QM04"):
        raise SeedError("只改派 QM03 / QM04")
    return REASSIGN_SQL.format(operator=quoted([operator]), id=safe_id(doc_id), biz=biz)


# ---- 查找 ----


def find_qc_po(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "qc_po")


def find_qc_arrival(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "arrival", "qc_arrival")


def _inspect_of(ctx: SeedContext, find_arrival: Callable[[SeedContext], list[int]]) -> list[int]:
    arr = one_or_none(ctx, find_arrival)
    return find_ids(ctx, _INSPECT_SQL.format(id=safe_id(arr))) if arr else []


def _check_of(ctx: SeedContext, find_inspect: Callable[[SeedContext], list[int]]) -> list[int]:
    inspect = one_or_none(ctx, find_inspect)
    return find_ids(ctx, CHECK_SQL.format(biz="QM03", id=safe_id(inspect))) if inspect else []


def find_qc_inspect(ctx: SeedContext) -> list[int]:
    return _inspect_of(ctx, find_qc_arrival)


def find_qc_check(ctx: SeedContext) -> list[int]:
    return _check_of(ctx, find_qc_inspect)


def find_qc_in(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_in", "qc_in")


def find_qc2_po(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_order", "qc2_po")


def find_qc2_arrival(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "arrival", "qc2_arrival")


def find_qc2_inspect(ctx: SeedContext) -> list[int]:
    return _inspect_of(ctx, find_qc2_arrival)


def find_qc2_check(ctx: SeedContext) -> list[int]:
    return _check_of(ctx, find_qc2_inspect)


def find_qc2_in(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "purchase_in", "qc2_in")


def project_code(ctx: SeedContext) -> str:
    rows = ctx.rows(PROJECT_SQL)
    code = rows[0].get("code", "").strip() if rows else ""
    if not code:
        raise SeedError("QMCHECKPROJECT 里没有未停用的检验方案（先在 U8 里建检验方案）")
    return code


# ---- 新建 ----


def _arrival_maker(find_po: _Find, prefix: str, qty: int) -> _Make:
    def make(ctx: SeedContext) -> int:
        po_id = need_one(ctx, find_po, prefix + "_po")
        first = line_ids(ctx, PO_LINES_SQL, po_id)[0]
        return generate_doc(ctx, arrival_body(po_id, first, qty, prefix + "_arrival"))

    return make


def _inspect_maker(find_arrival: _Find, prefix: str, qty: int) -> _Make:
    def make(ctx: SeedContext) -> int:
        arr = need_one(ctx, find_arrival, prefix + "_arrival")
        first = line_ids(ctx, ARR_LINES_SQL, arr)[0]
        return generate_doc(ctx, inspect_body("qm_incoming_inspect", "arrival", arr, first, qty))

    return make


def _check_maker(find_inspect: _Find, prefix: str, qty: int) -> _Make:
    def make(ctx: SeedContext) -> int:
        inspect = need_one(ctx, find_inspect, prefix + "_inspect")
        first = line_ids(ctx, INSPECT_LINES_SQL, inspect)[0]
        return generate_doc(ctx, check_body("qm_incoming_check", inspect, first, qty, project_code(ctx)))

    return make


_make_arrival = _arrival_maker(find_qc_po, "qc", QC_QTY)
_make_inspect = _inspect_maker(find_qc_arrival, "qc", QC_QTY)
_make_check = _check_maker(find_qc_inspect, "qc", QC_QTY)


def _make_qc_in(ctx: SeedContext) -> int:
    return generate_doc(ctx, qc_in_body(need_one(ctx, find_qc_check, "qc_check")))


def _make_qc2_in(ctx: SeedContext) -> int:
    return generate_doc(ctx, qc_in_body(need_one(ctx, find_qc2_check, "qc2_check"), "qc2_in", QC2_IN_QTY))


# ---- 审批 ----


def wf_status(ctx: SeedContext, kind: str, doc_id: int) -> str:
    response = post_ok(ctx, "v1/workflow/state", {"type": kind, "id": doc_id}, "查审批状态")
    wf = response.get("wf") if isinstance(response.get("wf"), dict) else response
    return str(wf.get("status") or "")


def approve_all(ctx: SeedContext, kind: str, biz: str, doc_id: int) -> int:
    """提交（没提交时），再逐节点改派并同意，直到通过。返回同意的次数。"""
    ref = {"type": kind, "id": doc_id}
    status = wf_status(ctx, kind, doc_id)
    if status == "not_controlled":
        raise SeedError(kind + " 不受审批流控制：造数要求账套为质量检验单启用审批流（QM03/QM04），或用 --skip 跳过本步")
    if status == "not_submitted":
        post_ok(ctx, "v1/workflow/submit", ref, "提交审批")
    for rounds in range(MAX_ROUNDS):
        status = wf_status(ctx, kind, doc_id)
        if status == "approved":
            return rounds
        if status != "in_approval":
            raise SeedError(kind + " " + str(doc_id) + " 审批状态是 " + (status or "空") + "，没法继续同意")
        ctx.exec(reassign_sql(biz, doc_id, ctx.operator))
        post_ok(ctx, "v1/workflow/approve", dict(ref, opinion="造数"), "同意")
    if wf_status(ctx, kind, doc_id) == "approved":
        return MAX_ROUNDS
    raise SeedError(kind + " " + str(doc_id) + " 同意 " + str(MAX_ROUNDS) + " 次后仍未通过")


def wf_done(ctx: SeedContext, doc_id: int) -> bool:
    rows = ctx.rows(WF_DONE_SQL.format(id=safe_id(doc_id)))
    return bool(rows) and rows[0].get("n", "0").strip() not in ("", "0")


def approve_step(name: str, title: str, find: Callable[[SeedContext], list[int]], source: str, biz: str) -> Step:
    """检验单走完审批流的步骤（QM03 来料、QM04 产品）：已通过的跳过。source 是产出检验单的前一步。"""
    kind = {"QM03": "qm_incoming_check", "QM04": "qm_product_check"}[biz]

    def probe(ctx: SeedContext) -> Probe:
        check = one_or_none(ctx, find)
        if check and wf_done(ctx, check):
            return Probe((source + ":" + str(check) + " 已通过",), ())
        return Probe((), (biz + " 审批",))

    def apply(ctx: SeedContext) -> tuple[int, int]:
        check = need_one(ctx, find, source)
        if wf_done(ctx, check):
            return 0, 1
        approve_all(ctx, kind, biz, check)
        return 1, 0

    return Step(name, title, probe, apply)


STEPS = (
    doc_step(
        Doc(
            "qc_po",
            "来料检验件采购订单（审核）",
            find_qc_po,
            lambda ctx: create_doc(ctx, qc_po_body(ctx.day)),
            verify_after("purchase_order"),
        )
    ),
    doc_step(Doc("qc_arrival", "来料检验件到货单（审核）", find_qc_arrival, _make_arrival, verify_after("arrival"))),
    doc_step(Doc("qc_inspect", "来料报检单（参照到货单）", find_qc_inspect, _make_inspect)),
    doc_step(Doc("qc_check", "来料检验单（参照报检单）", find_qc_check, _make_check)),
    approve_step("qc_approve", "来料检验单走完 QM03 审批", find_qc_check, "qc_check", "QM03"),
    doc_step(Doc("qc_in", "采购入库单（参照来料检验单，审核）", find_qc_in, _make_qc_in, verify_after("purchase_in"))),
    # 追加：第二条来料检验链，入库只做 1 个，检验单留 3 个给 qmin / se_qm 挑。
    doc_step(
        Doc(
            "qc2_po",
            "来料检验件采购订单二（审核）",
            find_qc2_po,
            lambda ctx: create_doc(ctx, qc_po_body(ctx.day, "qc2_po", QC2_PO_QTY)),
            verify_after("purchase_order"),
        )
    ),
    doc_step(
        Doc("qc2_arrival", "来料检验件到货单二（审核）", find_qc2_arrival, _arrival_maker(find_qc2_po, "qc2", QC2_QTY), verify_after("arrival"))
    ),
    doc_step(Doc("qc2_inspect", "来料报检单二（参照到货单）", find_qc2_inspect, _inspect_maker(find_qc2_arrival, "qc2", QC2_QTY))),
    doc_step(Doc("qc2_check", "来料检验单二（参照报检单）", find_qc2_check, _check_maker(find_qc2_inspect, "qc2", QC2_QTY))),
    approve_step("qc2_approve", "来料检验单二走完 QM03 审批", find_qc2_check, "qc2_check", "QM03"),
    doc_step(
        Doc("qc2_in", "采购入库单（参照来料检验单二入库 1 个，审核）", find_qc2_in, _make_qc2_in, verify_after("purchase_in"))
    ),
)
