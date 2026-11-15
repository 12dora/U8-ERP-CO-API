"""客户、供应商的子档案（桥 ArcPartner）：银行账户、联系人的编码格式、说明文字和 fields 校验。

编码是两段 <客户或供应商编码>:<银行账号或联系人编码>（桥 ArcPair），一次只写一行，不整批替换同一客户的其他行。
fields 用桥的固定标签表（ArcPartner），不是 RsXml；库里的状态（默认账户、编码占用、引用）由桥查。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from u8co_api.co_doctext import section

CUSTOMER_BANK = "customer_bank"
VENDOR_BANK = "vendor_bank"
CUSTOMER_CONTACT = "customer_contact"
VENDOR_CONTACT = "vendor_contact"
PARTNER_ARCHIVES = (CUSTOMER_BANK, VENDOR_BANK, CUSTOMER_CONTACT, VENDOR_CONTACT)
# 两段的最大长度（CustomerBank.cAccountNum nvarchar(50)、Crm_Contact.cContactCode nvarchar(30)）和写法。
PARTNER_PAIR = {
    CUSTOMER_BANK: (20, 50, "<客户编码>:<银行账号>"),
    VENDOR_BANK: (20, 50, "<供应商编码>:<银行账号>"),
    CUSTOMER_CONTACT: (20, 30, "<客户编码>:<联系人编码>"),
    VENDOR_CONTACT: (20, 30, "<供应商编码>:<联系人编码>"),
}
PARTNER_CODE_MAX = {name: first + 1 + second for name, (first, second, _) in PARTNER_PAIR.items()}
# 银行账户表没有 rowversion，列表不支持 changed_since；联系人表有 ufts。
PARTNER_NO_UFTS = frozenset({CUSTOMER_BANK, VENDOR_BANK})
# 客户、供应商联系人新增时 U8 自己编号，编码写成 "<客户或供应商编码>:"（第二段留空，桥 ArcPair.OpenSecond）。
PARTNER_OPEN = frozenset({CUSTOMER_CONTACT, VENDOR_CONTACT})
_BANKS = frozenset({CUSTOMER_BANK, VENDOR_BANK})
# 银行账户的标签和最大长度（桥 ArcPartnerBank.Limits）；default 是布尔。
_BANK_TEXT = {
    "branch": 100,
    "bank_code": 5,
    "account_name": 60,
    "province": 20,
    "city": 20,
    "cbb_dep_id": 60,
    "branch_id": 60,
    "branch_id_sec": 5,
}
_BANK_TAGS = frozenset({*_BANK_TEXT, "default"})
# 客户联系人的文字标签和最大长度（Crm_Contact 的列宽，桥 ArcPartnerContactMap.Limits）。
_CONTACT_TEXT = {
    "name": 50,
    "title": 20,
    "native": 30,
    "position": 255,
    "direct_leader": 30,
    "mobile": 100,
    "office_phone": 100,
    "family_phone": 100,
    "bp": 20,
    "email": 255,
    "web": 50,
    "work_address": 150,
    "postcode": 20,
    "family_member": 100,
    "family_address": 150,
    "favorite": 255,
    "charge_person": 20,
    "memo": 240,
    **{f"self_define{i}": 20 for i in range(1, 4)},
    **{f"self_define{i}": 60 for i in range(4, 7)},
    **{f"self_define{i}": 120 for i in range(7, 11)},
}
_CONTACT_TAGS = frozenset({*_CONTACT_TEXT, "sex", "marriage", "birthday", "be_main_linker"})
# 供应商联系人不开放职务、个人爱好（表里是编号，桥 ArcPartnerContactMap.VendorPairs）。
_VENDOR_CONTACT_TAGS = _CONTACT_TAGS - {"position", "favorite"}
_SEX = ("男", "女", "不详")
_MARRIAGE = ("已婚", "未婚", "离异", "不详")
_FLAG = (True, False, 0, 1, "0", "1")

PARTNER_WRITE_DESC = "\n\n".join(
    (
        section(
            "customer_bank 客户银行账户、vendor_bank 供应商银行账户",
            (
                "编码 `<客户或供应商编码>:<银行账号>`，账号最长 50",
                "fields：branch 开户银行（新增必填）、bank_code 所属银行编码（须在银行档案里）",
                "fields 另收 account_name 账户名称、default 默认账户",
                "以及 province、city、cbb_dep_id、branch_id、branch_id_sec",
                "每个客户至多一个默认账户；设为默认时其余账户清成非默认",
                "第一个账户必须是默认；默认账户不能取消默认，也不能在还有其他账户时删除",
            ),
        ),
        section(
            "customer_contact 客户联系人",
            (
                "联系人编码由 U8 自动编号：新增的 code 写成 `<客户编码>:`（冒号后留空）",
                "响应的 code 是 `<客户编码>:<U8 编的号>`，修改、删除用它",
                "新增必须给 name",
                "sex 男 / 女 / 不详，marriage 已婚 / 未婚 / 离异 / 不详，没给按不详",
                "birthday 写 yyyy-mm-dd；be_main_linker 主要联系人",
                "另有 mobile、office_phone、email、position、memo、self_define1–10 等",
                "被客户档案的主要联系人、收货地址、销售单据或合同引用的不能删除",
            ),
        ),
        section(
            "vendor_contact 供应商联系人",
            (
                "同样由 U8 自动编号，新增的 code 写成 `<供应商编码>:`",
                "新增走 U8 的 EAI 导入；修改、删除由桥直接改表",
                "标签同客户联系人，但没有 position、favorite",
                "每个供应商至多一个主要联系人；已有其他主要联系人时设为主要返回 409",
                "被供应商档案的主要联系人、采购和委外单据、进项发票登记或合同引用的不能删除",
            ),
        ),
    )
)
PARTNER_RO_DESC = (
    "`customer_bank`、`vendor_bank` 客户、供应商银行账户：编码 `<客户或供应商编码>:<银行账号>`，"
    "class_code 是客户或供应商编码",
    "`customer_contact`、`vendor_contact` 客户、供应商联系人：编码 `<客户或供应商编码>:<联系人编码>`",
)


def is_partner(archive: str) -> bool:
    return archive in PARTNER_PAIR


def check_partner_write(archive: str, code: str, fields: dict[str, Any], creating: bool) -> None:
    """标签必须在该档案的固定标签表里；银行账户新增必须给 branch；联系人新增必须给 name。

    与桥 ArcPartnerBank / ArcPartnerContactMap 一致。
    """
    if archive not in PARTNER_PAIR:
        return
    if archive in PARTNER_OPEN:
        _check_open_code(code, creating)
    bank = archive in _BANKS
    allowed = _BANK_TAGS if bank else _VENDOR_CONTACT_TAGS if archive == VENDOR_CONTACT else _CONTACT_TAGS
    lowered = {tag.lower(): value for tag, value in fields.items()}
    unknown = sorted(tag for tag in lowered if tag not in allowed)
    if unknown:
        raise ValueError(f"档案 {archive} 不认识字段 " + ", ".join(unknown))
    if bank:
        _check_bank(lowered, creating)
    else:
        _check_contact(lowered, creating)


def _check_open_code(code: str, creating: bool) -> None:
    empty = code.endswith(":")
    if creating and not empty:
        raise ValueError("联系人编码由 U8 自动编号：新增时 code 写成 <客户或供应商编码>:（冒号后留空）")
    if not creating and empty:
        raise ValueError("code 必须写成 <客户或供应商编码>:<联系人编码>")


def _text(tag: str, value: Any, limit: int, required: bool) -> None:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        tail = "且不能全是空白" if required else ""
        raise ValueError(f"字段 {tag} 必须是不超过 {limit} 个字符的文字{tail}")


def _check_bank(fields: dict[str, Any], creating: bool) -> None:
    if creating and "branch" not in fields:
        raise ValueError("新增银行账户必须给 branch（开户银行）")
    for tag, value in fields.items():
        if tag == "default":
            if value not in _FLAG:
                raise ValueError("default 必须是布尔或 0 / 1")
            continue
        _text(tag, value, _BANK_TEXT[tag], tag == "branch")


def _check_contact(fields: dict[str, Any], creating: bool) -> None:
    if creating and "name" not in fields:
        raise ValueError("新增联系人必须给 name")
    for tag, value in fields.items():
        if tag == "sex" and value not in _SEX:
            raise ValueError("sex 只能是 " + "、".join(_SEX))
        if tag == "marriage" and value not in _MARRIAGE:
            raise ValueError("marriage 只能是 " + "、".join(_MARRIAGE))
        if tag == "be_main_linker" and value not in _FLAG:
            raise ValueError("be_main_linker 必须是布尔或 0 / 1")
        if tag == "birthday" and not _is_date(value):
            raise ValueError("birthday 必须是 yyyy-mm-dd")
        if tag in _CONTACT_TEXT:
            _text(tag, value, _CONTACT_TEXT[tag], tag == "name")
        if tag == "name" and isinstance(value, str) and value.strip() != value:
            raise ValueError("name 前后不能有空格")


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True
