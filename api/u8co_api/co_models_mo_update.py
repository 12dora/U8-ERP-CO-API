"""生产订单修改（vouchers/update，type=production_order）的请求校验。与桥 MoUpdateReq 一致，字段名不分大小写。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from u8co_api.co_models_mo import CoMoCreatedLine, _date, _get, _only, _qty, _text

LINES_MAX = 50
_ID_MAX = 2147483647
_HEAD = {"remark": 255}
# 行上可改的文本字段和最大长度；qty 另查。自定义项只收文本型（Define22–25 是 nvarchar(60)，Define28–33 是 nvarchar(120)）。
_LINE_TEXT = {"due_date": 10, "remark": 255, "inv_code": 60}
_NO_START = "暂不支持修改开工日期（U8 重建子件时不重算需求日期）"
_DEFINES = {f"define{n}": 60 for n in (22, 23, 24, 25)} | {f"define{n}": 120 for n in range(28, 34)}
_LINE_KEYS = {"op": 0, "line_id": 0, "qty": 0, "start_date": 0, **_LINE_TEXT, **_DEFINES}
_NOT_EMPTY = ("remark", "inv_code", *_DEFINES)
MO_UPDATE_HELP = (
    "生产订单（production_order，id 是 MoId）改全部行未关闭（未审核或已审核；已关闭的先打开）、未提交审批、未报检、"
    "没有产成品入库的订单；已审核的照 U8 客户端变更直接改，状态保持已审核。已领料的改后子件需求不能少于已领数量（409），"
    "材料出库单行的子件关联由桥改到 U8 重插后的新子件：表头只收 remark（作为本次没单独给备注的各行备注）；"
    "明细 0 到 50 行，op 只能是 update，按 line_id（MoDId）定位，可改 qty、due_date、remark、"
    "define22–25、define28–33（文本），inv_code 只能送当前值；remark 和自定义项不能清空；"
    "start_date 暂不支持修改（U8 重建子件时不重算需求日期），有产出品子件的行不能改 due_date（桥 409）。"
    "子件被材料出库以外的单据或计划引用（含替代料、领料申请、调拨）的订单不能改。改了数量的行，桥按 U8 的算法重算每个子件（固定用量、损耗率、返工订单），"
    "其余子件原样重送；U8 重插子件后桥写回丢掉的关联列，回读子件数、数量或关联列对不上时 504 outcome_unknown。"
)


def check_mo_update(kind: str, head: dict | None, lines: list[dict] | None) -> bool:
    """生产订单修改不合规抛 ValueError（API 层 422），合规返回 True；其它类型不查，返回 False。"""
    if kind != "production_order":
        return False
    head = head or {}
    _only(head, _HEAD)
    _not_empty(head, "remark")
    _text(head, "remark", 255, required=False)
    rows = lines or []
    if not rows and all(value is None for value in head.values()):
        raise ValueError("没有要修改的内容")
    if len(rows) > LINES_MAX:
        raise ValueError("生产订单修改的明细不能超过 50 行")
    seen: set[int] = set()
    for row in rows:
        _check_line(row, seen)
    return True


def _check_line(row: dict, seen: set[int]) -> None:
    op = _get(row, "op")
    if op in ("add", "delete"):
        raise ValueError("生产订单修改暂不能新增或删除行，请在 U8 客户端修改")
    if op != "update":
        raise ValueError("op 只能是 update")
    _only(row, _LINE_KEYS)
    _line_id(_get(row, "line_id"), seen)
    if not any(value is not None for key, value in row.items() if key.lower() not in ("op", "line_id")):
        raise ValueError("修改行至少再改一个字段")
    for key, limit in {**_LINE_TEXT, **_DEFINES}.items():
        _text(row, key, limit, required=False)
    for key in _NOT_EMPTY:
        _not_empty(row, key)
    if _get(row, "qty") is not None:
        _qty(_get(row, "qty"))
    _dates(row)


def _dates(row: dict) -> None:
    # 开工日期不开放；完工日期不早于库里的开工日期由桥核对。
    if _get(row, "start_date") is not None:
        raise ValueError(_NO_START)
    if _get(row, "due_date") is not None:
        _date(_get(row, "due_date"), "due_date")


def _line_id(value: object, seen: set[int]) -> None:
    if type(value) is not int or not 1 <= value <= _ID_MAX:
        raise ValueError("line_id 无效")
    if value in seen:
        raise ValueError("line_id 重复")
    seen.add(value)


def _not_empty(row: dict, key: str) -> None:
    value = _get(row, key)
    if isinstance(value, str) and not value.strip():
        raise ValueError(key + " 不能清空（U8 接口不写空值）")


class CoMoUpdateOut(BaseModel):
    """修改响应（co_models_update_out.CoUpdateOut）里生产订单专有的字段；其它类型省略。"""

    model_config = ConfigDict(extra="ignore")
    allocates: int | None = Field(None, description="生产订单修改：保存后全部行的子件总行数。其它类型省略")
    details: list[CoMoCreatedLine] | None = Field(
        None, description="生产订单修改：每行的主键、行号、存货、数量、状态和子件行数。其它类型省略"
    )
    changed: int | None = Field(
        None, description="生产订单修改：本次改动的行数（0 表示请求与现值相同，没有调用 U8）。其它类型省略"
    )
