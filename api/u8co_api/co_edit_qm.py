"""质量单据修改（vouchers/update）的专用校验：来料 / 产品检验单、其他检验单、其他报检单（桥 QmEditReq），
来料 / 产品不良品处理单（桥 QmRejEditReq）。

检验单、报检单只收 head，不收 lines。检验单表头可改 dDate、cCheckPersonCode、cChkConclusion、cReasonCode、
fRegQuantity、fConQuantiy、fDisQuantity、cDefine1–16、chDefine11–16 和 items（按检验项目、指标覆盖已有行）；
三个数量之和由桥按单据上已有的检验数量核对。
其他报检单只改 dDate、cInspectDepCode、cDefine1–16、chDefine11–16。表头键不区分大小写，同名只能出现一次。
产品报检单不能修改（测试账套实测 U8 对该单据不提供修改），vouchers/verify 只收 unverify（check_qm_verify）。
不良品处理单表头同生单（dDate、cDefine1–16、chDefine11–16），lines 按 op 增删改处理行（line_id 是表体 AUTOID）；
处理数量合计、处理方式与原因的档案、降级后存货由桥按单据和检验单核对。
"""

from __future__ import annotations

from u8co_api.co_doctext import section
from u8co_api.co_gen_qm import (
    REJECT_KINDS,
    YIELD_CODE,
    YIELD_DATE,
    _date,
    _define,
    _lowered,
    _number,
    _reject_head,
    _text,
    check_items,
)

QM_EDIT_CHECKS = ("qm_incoming_check", "qm_product_check", "qm_other_check")
QM_EDIT_INSPECT = "qm_other_inspect"
QM_EDIT_KINDS = QM_EDIT_CHECKS + (QM_EDIT_INSPECT,)
QM_PRO_INSPECT = "qm_product_inspect"
QM_PRO_VERIFY_REFUSED = "产品报检单只支持弃审（action=unverify）：审核由 U8 在保存时按选项自动完成"
QM_UPDATE_HELP = "\n\n".join(
    (
        section(
            "来料检验单、产品检验单、其他检验单（只收 head、不收 lines）",
            (
                "可改 dDate、cCheckPersonCode、cChkConclusion、cReasonCode、cDefine1–16、chDefine11–16",
                "可改 fRegQuantity / fConQuantiy / fDisQuantity：三者之和必须等于单据上已有的检验数量",
                "送了其中任一个时，没送的让步、不良按 0，合格按检验数量减二者",
                "只送 fDisQuantity 会把让步数量清零；没送结论时按不良数量重写结论",
                "cReasonCode 是让步接收原因：有让步数量时必填；其他检验单没有让步数量时不收",
                "cYielderCode：让步接收核准人的人员编码，来料 / 产品检验单有让步数量时必填，单据上已有的算数",
                "dYieldDate：核准日期，缺省 dDate 或登录日期",
                "items：按 cChkItemCode + cChkGuideCode 覆盖已有检验项目的 cCheckValue、cTargetQJug，不增删行",
                "检验数量、存货、仓库、检验方案不能改",
            ),
        ),
        section("其他报检单", ("只改 dDate、cInspectDepCode、cDefine1–16、chDefine11–16",)),
        section(
            "来料不良品处理单、产品不良品处理单",
            (
                "head 可改 dDate、cDefine1–16、chDefine11–16；处理行见 lines",
                "已审核的先弃审；受审批流控制且已提交的先在审批流中撤回",
            ),
        ),
        section(
            "质量单据通用",
            (
                "只改未提交审批、未审核、没有下游的单据（已审核的其他报检单、其他检验单先弃审）",
                "保存由 U8 自行提交，dry_run 只做校验",
            ),
        ),
    )
)
QM_REJECT_LINES_HELP = section(
    "来料不良品处理单、产品不良品处理单的处理行",
    (
        "add：quantity、cScrapDisCode、cReasonCode 必填，可带 cDimInvCode、fDimQuantity、cbWhCode",
        "update：line_id（表体 AUTOID）加上述字段至少一个；delete：只有 op 和 line_id",
        "改完后各行 quantity 之和必须等于本处理单的待处理不良品数量（FSUMQUANTITY）",
        "不良原因 + 处理方式不能重复，至少保留一行",
        "降级类处理方式（处理流程 2）必须有 cDimInvCode；fDimQuantity 缺省同 quantity，必须大于 0",
        "改成非降级类处理方式时，处理后存货与数量随之清除",
        "不收检验单、存货、来源字段；行已入库、已返工或单据有下游时 409",
    ),
)
_REJECT_FIELDS = frozenset({"quantity", "cscrapdiscode", "creasoncode", "cdiminvcode", "fdimquantity", "cbwhcode"})
_REJECT_ADD_NEED = ("quantity", "cscrapdiscode", "creasoncode")
_REJECT_OPS = ("add", "update", "delete")
_ID_MAX = 2147483647
_TEXT_NEED = ("ccheckpersoncode", "cchkconclusion", "creasoncode", YIELD_CODE)
_SPLIT = ("fregquantity", "fconquantiy", "fdisquantity")


def _check_field(low: str, value: object) -> None:
    if low == YIELD_DATE:
        _yield_date(value)
    elif low == "ddate":
        _date(value)
    elif low in _TEXT_NEED:
        _text(low, value, True)
    elif low in _SPLIT:
        _number(low, value, False)
    elif low == "items":
        check_items(value)
    elif not _define(low, low, value):
        raise ValueError("不能设置字段 " + low)


def _yield_date(value: object) -> None:
    # 共用的日期校验报的是 dDate，这里换成本字段名。
    try:
        _date(value)
    except ValueError:
        raise ValueError("dYieldDate 必须是 yyyy-MM-dd") from None


def _inspect_field(low: str, value: object) -> None:
    if low == "ddate":
        _date(value)
    elif low == "cinspectdepcode":
        _text("cInspectDepCode", value, True)
    elif not _define(low, low, value):
        raise ValueError("不能设置字段 " + low)


def _reject_values(row: dict) -> int:
    sent = 0
    for key, value in row.items():
        if key in ("op", "line_id"):
            continue
        if key not in _REJECT_FIELDS:
            raise ValueError("不能设置字段 " + key)
        sent += 1
        if key in ("quantity", "fdimquantity"):
            _number(key, value, True)
        else:
            _text(key, value, True)
    return sent


def _reject_line_id(row: dict, seen: set[int]) -> None:
    raw = row.get("line_id")
    if type(raw) is not int or raw < 1 or raw > _ID_MAX:
        raise ValueError("line_id 无效")
    if raw in seen:
        raise ValueError("line_id 重复")
    seen.add(raw)


def _reject_op(raw: object, seen: set[int]) -> None:
    if not isinstance(raw, dict):
        raise ValueError("表体行必须是对象")
    row = _lowered(raw)
    op = row.get("op")
    if op not in _REJECT_OPS:
        raise ValueError("op 只能是 add、update 或 delete")
    sent = _reject_values(row)
    if op == "add":
        if "line_id" in row:
            raise ValueError("新增行不能带 line_id")
        for key in _REJECT_ADD_NEED:
            if key not in row:
                raise ValueError("新增行必须指定 " + key)
        return
    _reject_line_id(row, seen)
    if op == "delete" and sent:
        raise ValueError("删除行只能有 op 和 line_id")
    if op == "update" and not sent:
        raise ValueError("修改行至少再改一个字段")


def _check_reject(head: dict | None, lines: list | None) -> None:
    if not head and not lines:
        raise ValueError("没有要修改的内容")
    if head:
        _reject_head(_lowered(head))
    seen: set[int] = set()
    for raw in lines or ():
        _reject_op(raw, seen)


def check_qm_update(kind: str, head: dict | None, lines: list | None) -> bool:
    """质量单据修改的校验；不是这六类返回 False（交给通用规则），是则校验通过返回 True。"""
    if kind in REJECT_KINDS:
        _check_reject(head, lines)
        return True
    if kind not in QM_EDIT_KINDS:
        return False
    if lines:
        raise ValueError("质量单据修改只收表头 head（检验项目放在 head.items），不收 lines")
    if not head:
        raise ValueError("没有要修改的内容")
    field = _inspect_field if kind == QM_EDIT_INSPECT else _check_field
    for low, value in _lowered(head).items():
        field(low, value)
    return True


def check_qm_verify(kind: str, action: str) -> None:
    """产品报检单的 vouchers/verify 只收 unverify（U8 的审核对报检单总是失败）；不合规抛 ValueError（API 层 400）。"""
    if kind == QM_PRO_INSPECT and action != "unverify":
        raise ValueError(QM_PRO_VERIFY_REFUSED)
