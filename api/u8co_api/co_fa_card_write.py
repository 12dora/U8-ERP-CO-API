"""archives/create 的两类新档案在 API 层的 fields 检查（与桥同一规则，库里的状态由桥查）：
fa_card 固定资产卡片（U8 官方 EAI capitalasserts，code 是资产编号，导入的是原始卡片）、
equipment 设备台账（U8 官方 EAI eqdata，fields 是 EAI 标签，制单人、制单日期由桥填）。两类都不收 template。"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

from u8co_api.co_doctext import section
from u8co_api.co_fa_write import AMOUNT_MAX, EQUIPMENT, FA_CARD, FA_CARD_NO_UPDATE, bad, number, text_ok, whole

_REQUIRED = (
    "name",
    "type_code",
    "original_value",
    "start_date",
    "origin_code",
    "status_code",
    "depreciation_method_code",
    "dept_code",
)
_OPTIONAL = (
    "useful_life_months",
    "used_months",
    "accumulated_depreciation",
    "net_salvage",
    "net_salvage_rate",
    "spec",
    "location",
    "keeper",
    "impairment",
    "currency",
)
# 文字字段的最长字符数（类别 20、部门 12 同卡片读取；增加方式、使用状况、折旧方法的编码长度由桥按库校验）。
_TEXTS = {
    "name": 50,
    "type_code": 20,
    "dept_code": 12,
    "origin_code": 30,
    "status_code": 30,
    "depreciation_method_code": 30,
    "spec": 50,
    "location": 50,
    "keeper": 20,
    "currency": 8,
}
_MONTHS_MAX = 11988
_INT_MAX = 2147483647
_YMD = re.compile(r"\d{4}-\d{2}-\d{2}")
_EQ_TAGS = frozenset(
    (
        "name ceqtypecode cabccode csupeqcode cstacode cpcode cdepcode cseq cpiccode cvencode dtccdate dtgmdate "
        "dtazdate dtsydate intsynx dtbxdate dbldjgl intdjnum cdjdw dblzdsj cassetnum cmemo"
    ).split()
) | frozenset(f"cdefine{index}" for index in range(1, 17))

FA_WRITE_DESC = "\n\n".join(
    (
        section(
            "fa_card 固定资产卡片（可新增、撤销本期新增）",
            (
                "新增走 U8 官方 EAI 导入原始卡片；code 是资产编号，最长 20",
                "fields 必填：name、type_code、original_value、start_date、origin_code、status_code、"
                "depreciation_method_code、dept_code",
                "start_date 早于固定资产当前期间",
                "fields 可选：useful_life_months、used_months、accumulated_depreciation、net_salvage、"
                "net_salvage_rate、spec、location、keeper、impairment、currency",
                "响应的 code 是 U8 编的卡片编号，另有 asset_num、card_id",
                "删除的 code 是卡片编号，只删本期新增、没有变动单的卡片",
                FA_CARD_NO_UPDATE,
                "资产减少也请在 U8 客户端录入",
            ),
        ),
        section(
            "equipment 设备台账（只能新增）",
            (
                "code 是设备编码，最长 30",
                "fields 收 name（必填）和 EAI 标签 ceqtypecode、cdepcode、cvencode、dtsydate、cassetnum、cmemo、"
                "cdefine1 到 cdefine16 等",
            ),
        ),
    )
)
FA_RO_DESC = "`equipment` 设备台账：编码是设备编码，支持 changed_since"


def check_fa_write(archive: str, fields: dict[str, Any], template: str | None) -> None:
    """archives/create 的 fa_card、equipment；其他档案不管。"""
    if archive not in (FA_CARD, EQUIPMENT):
        return
    if template is not None:
        raise bad(f"档案 {archive} 不支持 template", "template")
    if archive == FA_CARD:
        _check_card(fields)
    else:
        _check_equipment(fields)


def _check_card(fields: dict[str, Any]) -> None:
    known = _REQUIRED + _OPTIONAL
    unknown = sorted(tag for tag in fields if tag not in known)
    if unknown:
        raise bad("固定资产卡片不收字段 " + ", ".join(unknown), "fields." + unknown[0])
    missing = [tag for tag in _REQUIRED if tag not in fields]
    if missing:
        raise bad("新增固定资产卡片缺少 " + "、".join(missing), "fields." + missing[0])
    for tag, limit in _TEXTS.items():
        if tag in fields and not text_ok(fields[tag], limit):
            raise bad(f"{tag} 必须是 1 到 {limit} 个字符", "fields." + tag)
    _check_date(fields["start_date"])
    _check_amounts(fields)
    _check_months(fields)


def _check_date(value: object) -> None:
    ok = isinstance(value, str) and _YMD.fullmatch(value) is not None
    if ok:
        try:
            dt.date.fromisoformat(str(value))
        except ValueError:
            ok = False
    if not ok:
        raise bad("start_date 必须是 yyyy-mm-dd", "fields.start_date")


def _amount(fields: dict[str, Any], tag: str, high: Decimal, *, open_high: bool = False) -> None:
    if tag not in fields:
        return
    value = number(fields[tag])
    if value is None or value < 0 or value > high or (open_high and value == high):
        raise bad(f"{tag} 超出范围", "fields." + tag)


def _check_amounts(fields: dict[str, Any]) -> None:
    original = number(fields["original_value"])
    if original is None or not Decimal(0) < original <= AMOUNT_MAX:
        raise bad("original_value 必须是大于 0、不超过 1000000000000 的金额", "fields.original_value")
    _amount(fields, "accumulated_depreciation", original)
    _amount(fields, "net_salvage", original)
    _amount(fields, "impairment", AMOUNT_MAX)
    _amount(fields, "net_salvage_rate", Decimal(1), open_high=True)


def _check_months(fields: dict[str, Any]) -> None:
    for tag, high in (("useful_life_months", _MONTHS_MAX), ("used_months", _INT_MAX)):
        if tag not in fields:
            continue
        months = whole(fields[tag])
        if months is None or not 0 <= months <= high:
            raise bad(f"{tag} 必须是 0 到 {high} 的整数", "fields." + tag)


def _check_equipment(fields: dict[str, Any]) -> None:
    unknown = sorted(tag for tag in fields if tag not in _EQ_TAGS)
    if unknown:
        raise bad("设备台账不收字段 " + ", ".join(unknown) + "（制单人、制单日期由桥填）", "fields." + unknown[0])
    if not text_ok(fields.get("name"), 200):
        raise bad("新增设备台账必须给 name（设备名称）", "fields.name")
