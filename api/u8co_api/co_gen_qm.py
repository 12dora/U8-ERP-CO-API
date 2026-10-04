"""质量单据参照生单的专用校验：来料报检单、产品报检单、来料检验单、产品检验单。与桥 QmGen* 一致。

报检单（QM01 / QM02）参照已审核的到货单或生产订单，明细 1 到 200 行；
检验单（QM03 / QM04）参照已审核的报检单，只能 1 行，表头必须有检验员 ccheckpersoncode，
可带检验项目 items（覆盖检验方案的缺省项目）。
不良品处理单（QM05 / QM06）参照已审核、有不良数量的检验单，每行一种处理方式，
行的 source_line_id 就是检验单 ID。表头键不区分大小写，同名只能出现一次。
"""

from __future__ import annotations

import math
import re

from u8co_api.co_doctext import section

INSPECT_KINDS = ("qm_incoming_inspect", "qm_product_inspect")
# 其他检验单（QM15）参照其他报检单（QM11）生单，请求规则同来料检验单。
OTHER_CHECK = "qm_other_check"
OTHER_INSPECT = "qm_other_inspect"
CHECK_KINDS = ("qm_incoming_check", "qm_product_check", OTHER_CHECK)
REJECT_KINDS = ("qm_incoming_reject", "qm_product_reject")
QM_GEN_KINDS = INSPECT_KINDS + CHECK_KINDS + REJECT_KINDS
# 目标类型 → 允许的来源类型（第一个是缺省）。并入 co_gen_source.SOURCES。
QM_SOURCES = {
    "qm_incoming_inspect": ("arrival",),
    "qm_product_inspect": ("production_order",),
    "qm_incoming_check": ("qm_incoming_inspect",),
    "qm_product_check": ("qm_product_inspect",),
    "qm_incoming_reject": ("qm_incoming_check",),
    "qm_product_reject": ("qm_product_check",),
    OTHER_CHECK: (OTHER_INSPECT,),
}
QM_GEN_HELP = "\n\n".join(
    (
        section(
            "来料报检单、产品报检单",
            (
                "来料报检单（qm_incoming_inspect）参照已审核的蓝字到货单（id 是到货单 ID）",
                "产品报检单（qm_product_inspect）参照已审核未关闭的生产订单（id 是 MoId）",
                "明细 1 到 200 行，每行 source_line_id（到货单行 Autoid 或 MoDId）、大于 0 的 quantity，可带 cWhCode",
                "表头可省略，只收 dDate、cDepCode、cInspectDepCode、cDefine1–16",
            ),
        ),
        section(
            "来料检验单、产品检验单",
            (
                "来料检验单（qm_incoming_check）参照已审核的来料报检单（id 是报检单 ID）",
                "产品检验单（qm_product_check）参照已审核的产品报检单（id 是报检单 ID）",
                "只能 1 行：source_line_id 是报检单表体 AUTOID，quantity 是本次检验数量",
                "行可带 fRegQuantity（合格）、fConQuantiy（让步）、fDisQuantity（不良），三者之和必须等于 quantity",
                "表头必须有 cCheckPersonCode（检验员）",
                "表头可带 cDepCode、project_code（检验方案）、cChkConclusion、fDtQuantity（抽检量，大于 0）",
                "表头可带 dDate、cDefine1–16、chDefine11–16、dYieldDate（核准日期）",
                "cYielderCode：让步接收核准人的人员编码，有让步数量时来料 / 产品检验单必填",
                "items：1 到 50 个检验项目 {cChkItemCode, cChkGuideCode, cCheckValue?, cTargetQJug?}",
                "cTargetQJug 只能是 合格 或 不合格",
            ),
        ),
        section(
            "来料不良品处理单、产品不良品处理单",
            (
                "来料不良品处理单（qm_incoming_reject）参照来料检验单，产品不良品处理单（qm_product_reject）参照产品检验单",
                "id 是检验单 ID；检验单须已审核、有未处理的不良数量、未生成过不良品处理单",
                "每行一种处理方式：source_line_id 等于 id，quantity 大于 0，各行之和必须等于检验单的不良数量",
                "cScrapDisCode（不良品处理方式编码）、cReasonCode（不良原因编码）必填",
                "处理流程为降级时必须有 cDimInvCode（降级存货）；可带 cbWhCode",
                "表头可省略，只收 dDate、cDefine1–16、chDefine11–16（处理单没有备注列）",
            ),
        ),
        section(
            "其他检验单（qm_other_check）",
            (
                "参照已审核的其他报检单：id 是其他报检单 ID，source_line_id 是表体 AUTOID，每行只能生成一张",
                "表头表体规则同来料检验单；cDepCode（检验部门）省略时取检验员的所属部门",
            ),
        ),
        section("质量单据通用", ("保存由 U8 自行提交，dry_run 只做校验",)),
    )
)
# 其他报检单（qm_other_inspect）无来源新增（vouchers/create）的表头、表体字段，与桥 QmOthReq 一致。
QM_OTHER_CREATE_HELP = section(
    "其他报检单（qm_other_inspect）无来源新增",
    (
        "表头只收 dDate、cInspectDepCode、cDefine1–16、chDefine11–16",
        "cInspectDepCode 省略时取本操作员最近一张的报检部门",
        "U8 单据模板设为必输的（如 cDefine10、chDefine16）缺了 400",
        "明细 1 到 200 行，每行 cInvCode、大于 0 的 quantity；不收来源字段",
        "每行可带 iTestStyle（0 到 3 的整数，省略时取存货档案）和 cWhCode",
        "保存由 U8 自行提交（按选项自动审核），dry_run 只做校验",
    ),
)
_QTY_MAX = 10**12
_ID_MAX = 2147483647
_TEXT_MAX = 60
_ITEMS_MAX = 50
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DEFINE = re.compile(r"cdefine([1-9]\d?)")
_HDEFINE = re.compile(r"chdefine([1-9]\d?)")
_INSPECT_HEAD = frozenset({"ddate", "cdepcode", "cinspectdepcode"})
_CHECK_TEXT = frozenset({"ccheckpersoncode", "cdepcode", "project_code", "cchkconclusion"})
# 让步接收核准人（人员编码）与核准日期，检验单生单和修改都收（桥 QmYield）。
YIELD_CODE, YIELD_DATE = "cyieldercode", "dyielddate"
_INSPECT_LINE = frozenset({"source_line_id", "quantity", "cwhcode"})
_SPLIT = ("fregquantity", "fconquantiy", "fdisquantity")
_CHECK_LINE = frozenset({"source_line_id", "quantity"} | set(_SPLIT))
_ITEM_KEYS = frozenset({"cchkitemcode", "cchkguidecode", "ccheckvalue", "ctargetqjug"})
_ITEM_NEED = ("cchkitemcode", "cchkguidecode")
_REJECT_LINE = frozenset(
    {"source_line_id", "quantity", "cscrapdiscode", "creasoncode", "cdiminvcode", "cbwhcode"}
)
_REJECT_NEED = ("cscrapdiscode", "creasoncode")
_REJECT_CODES = ("cdiminvcode", "cbwhcode")

_VERDICTS = frozenset({"合格", "不合格"})
_EPS = 1e-9


def _lowered(row: dict) -> dict:
    found: dict = {}
    for key, value in row.items():
        low = key.lower()
        if low in found:
            raise ValueError("字段重复 " + key)
        found[low] = value
    return found


def _in_span(pattern: re.Pattern[str], low: str, first: int, last: int) -> bool:
    match = pattern.fullmatch(low)
    return match is not None and first <= int(match.group(1)) <= last


def _text(key: str, value: object, need: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(key + " 必须是字符串")
    if need and not value.strip():
        raise ValueError(key + " 不能为空")
    if len(value) > _TEXT_MAX:
        raise ValueError(f"{key} 最长 {_TEXT_MAX} 个字符")
    return value


def _number(key: str, value: object, positive: bool) -> float:
    if type(value) not in (int, float):
        raise ValueError(key + " 必须是数字")
    number = float(value)
    low_ok = number > 0 if positive else number >= 0
    if not math.isfinite(number) or not low_ok or number > _QTY_MAX:
        raise ValueError(key + " 无效")
    return number


def _date(value: object) -> None:
    if type(value) is not str or _DATE.fullmatch(value) is None:
        raise ValueError("dDate 必须是 yyyy-MM-dd")


def _define(low: str, key: str, value: object) -> bool:
    # cDefine1–16 与 chDefine11–16（检验单扩展自定义项）；值只能是字符串或数字。
    if not _in_span(_DEFINE, low, 1, 16) and not _in_span(_HDEFINE, low, 11, 16):
        return False
    if type(value) not in (str, int, float):
        raise ValueError(key + " 必须是字符串或数字")
    return True


def _source_id(row: dict, seen: set[int]) -> None:
    raw = row.get("source_line_id")
    if type(raw) is not int or raw < 1 or raw > _ID_MAX:
        raise ValueError("source_line_id 无效")
    if raw in seen:
        raise ValueError("source_line_id 重复")
    seen.add(raw)


def _inspect_head(head: dict) -> None:
    for low, value in head.items():
        if low == "ddate":
            _date(value)
        elif low in _INSPECT_HEAD:
            _text(low, value)
        elif low.startswith("chdefine") or not _define(low, low, value):
            raise ValueError("不能设置字段 " + low)


def _inspect_lines(lines: list[dict]) -> None:
    seen: set[int] = set()
    for raw in lines:
        row = _lowered(raw)
        for key in row:
            if key not in _INSPECT_LINE:
                raise ValueError("不能设置字段 " + key)
        _source_id(row, seen)
        _number("quantity", row.get("quantity"), True)
        if "cwhcode" in row:
            _text("cWhCode", row["cwhcode"], True)


def _item(raw: object, seen: set[tuple[str, str]]) -> None:
    if not isinstance(raw, dict):
        raise ValueError("items 的每一项必须是对象")
    row = _lowered(raw)
    for key in row:
        if key not in _ITEM_KEYS:
            raise ValueError("items 不能设置字段 " + key)
    pair = tuple(_text(key, row.get(key), True) for key in _ITEM_NEED)
    if "ccheckvalue" in row:
        _text("cCheckValue", row["ccheckvalue"])
    if "ctargetqjug" in row and row["ctargetqjug"] not in _VERDICTS:
        raise ValueError("cTargetQJug 只能是 合格 或 不合格")
    if pair in seen:
        raise ValueError("items 检验项目重复")
    seen.add(pair)


def check_items(value: object) -> None:
    """检验项目：1 到 50 项，cChkItemCode、cChkGuideCode 必填，字符串最长 60。"""
    if not isinstance(value, list) or not 1 <= len(value) <= _ITEMS_MAX:
        raise ValueError(f"items 必须是 1 到 {_ITEMS_MAX} 个检验项目")
    seen: set[tuple[str, str]] = set()
    for raw in value:
        _item(raw, seen)


def _check_field(low: str, value: object) -> None:
    if low in _CHECK_TEXT:
        _text(low, value)
    elif low in ("ddate", YIELD_DATE):
        _date(value)
    elif low == YIELD_CODE:
        _text("cYielderCode", value, True)
    elif low == "fdtquantity":
        _number("fDtQuantity", value, True)
    elif low == "items":
        check_items(value)
    elif not _define(low, low, value):
        raise ValueError("不能设置字段 " + low)


def _check_head(head: dict) -> None:
    for low, value in head.items():
        _check_field(low, value)
    if "ccheckpersoncode" not in head:
        raise ValueError("检验单表头必须有 cCheckPersonCode（检验员）")
    _text("cCheckPersonCode", head["ccheckpersoncode"], True)


def _check_line(lines: list[dict]) -> None:
    if len(lines) != 1:
        raise ValueError("检验单只能有 1 行")
    row = _lowered(lines[0])
    for key in row:
        if key not in _CHECK_LINE:
            raise ValueError("不能设置字段 " + key)
    _source_id(row, set())
    qty = _number("quantity", row.get("quantity"), True)
    parts = [_number(key, row[key], False) for key in _SPLIT[1:] if key in row]
    reg = _number("fRegQuantity", row["fregquantity"], False) if "fregquantity" in row else qty - sum(parts)
    if reg < -_EPS or abs(reg + sum(parts) - qty) > _EPS * max(1.0, qty):
        raise ValueError("合格、让步、不良数量之和必须等于 quantity")


def _reject_head(head: dict) -> None:
    for low, value in head.items():
        if low == "ddate":
            _date(value)
        elif not _define(low, low, value):
            raise ValueError("不能设置字段 " + low)


def _reject_line(raw: dict, source_id: int) -> None:
    row = _lowered(raw)
    for key in row:
        if key not in _REJECT_LINE:
            raise ValueError("不能设置字段 " + key)
    _source_id(row, set())
    if source_id and row["source_line_id"] != source_id:
        raise ValueError("不良品处理单的 source_line_id 必须等于 id（检验单 ID）")
    _number("quantity", row.get("quantity"), True)
    for key in _REJECT_NEED:
        _text(key, row.get(key), True)
    for key in _REJECT_CODES:
        if key in row:
            _text(key, row[key], True)


def _reject_lines(lines: list[dict], source_id: int) -> None:
    for raw in lines:
        _reject_line(raw, source_id)


_OTHER_HEAD = frozenset({"ddate", "cinspectdepcode"})
_OTHER_LINE = frozenset({"cinvcode", "quantity", "iteststyle", "cwhcode"})


def _other_line(raw: dict) -> None:
    row = _lowered(raw)
    for key in row:
        if key not in _OTHER_LINE:
            raise ValueError("不能设置字段 " + key)
    _text("cInvCode", row.get("cinvcode"), True)
    _number("quantity", row.get("quantity"), True)
    style = row.get("iteststyle")
    if "iteststyle" in row and (type(style) is not int or not 0 <= style <= 3):
        raise ValueError("iTestStyle 必须是 0 到 3 的整数")
    if "cwhcode" in row:
        _text("cWhCode", row["cwhcode"], True)


def check_qm_other_create(kind: str, head: dict | None, lines: list[dict] | None) -> None:
    """其他报检单无来源新增：表头 dDate、cInspectDepCode、cDefine1–16、chDefine11–16；明细 cInvCode、quantity 等。其他类型不管。"""
    if kind != OTHER_INSPECT:
        return
    for low, value in _lowered(head or {}).items():
        if low == "ddate":
            _date(value)
        elif low in _OTHER_HEAD:
            _text(low, value)
        elif not _define(low, low, value):
            raise ValueError("不能设置字段 " + low)
    if not lines:
        raise ValueError("必须指定明细")
    for raw in lines:
        _other_line(raw)


def check_qm_gen(kind: str, head: dict | None, lines: list[dict] | None, source_id: int = 0) -> None:
    """报检单、检验单、不良品处理单生单的表头和明细校验。head 的值可能含 items 列表，其余值不能嵌套。

    source_id 是请求的 id；不良品处理单的每行 source_line_id 都必须等于它。
    """
    if not lines:
        raise ValueError("必须指定明细")
    found = _lowered(head or {})
    if kind in REJECT_KINDS:
        _reject_head(found)
        _reject_lines(lines, source_id)
        return
    if kind in INSPECT_KINDS:
        _inspect_head(found)
        _inspect_lines(lines)
        return
    _check_head(found)
    _check_line(lines)


def nested_head(kind: str, head: dict | None) -> None:
    """只有检验单的 head.items 可以是列表；别的类型、别的键一律不能嵌套。"""
    for key, value in (head or {}).items():
        if isinstance(value, list) and not (kind in CHECK_KINDS and key.lower() == "items"):
            raise ValueError("表头的值不能嵌套：" + key)
