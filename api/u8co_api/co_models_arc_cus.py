"""客户主数据专用写入（customer_person / customer_fields / customer_address）共用的校验和说明。

三条路由都走受控 SQL，不走客户 EAI：EAI 的客户 diffedit 即使只发编码和一个字段，也会清空开户银行与银行账号、
改写最后收款日期、补助记码，并拒绝未填所属行业的客户。
"""

from __future__ import annotations

import datetime as dt
import re

# 与档案编码相同：不含控制字符，首尾不能有空白。
CTRL = "\\x00-\\x1f\\x7f\\uFFFE\\uFFFF"
TRIMMED = re.compile(rf"[^\s{CTRL}](?:[^{CTRL}]*[^\s{CTRL}])?")
_CONTROL = re.compile(rf"[{CTRL}]")
_YMD = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# 三条路由的「规则」小节里共用的权限、锁和只读账套说明。
PERMISSION_RULES = (
    "操作员须有客户档案编辑权限（AS011）；记录级客户权限同档案读取",
    "锁键与档案写入相同（arc:customer:<客户编码>、arc:write）",
)


def only_read_only_rule(op: str) -> str:
    """只读账套的说明：写入分类独立于 archives/update，要靠只读例外才穿过闸门。"""
    return (
        f"写入分类 (archives, {op})：放行 archives/update 不连带放行本路由；"
        f"只读账套上仅当只读例外 type=archives、op={op} 且账套、操作员命中才穿过闸门"
    )


def has_control(value: str) -> bool:
    return _CONTROL.search(value) is not None


def check_ymd(value: str, label: str) -> str:
    """yyyy-MM-dd 且是真实存在的日期。"""
    if _YMD.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是 yyyy-MM-dd")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} 必须是 yyyy-MM-dd") from None
    return value


def check_text(value: str, label: str, limit: int, trimmed: bool = True) -> str:
    """文本值：不含控制字符、不超过 limit；trimmed 时首尾不能有空白。"""
    if len(value) > limit:
        raise ValueError(f"{label} 最长 {limit} 个字符")
    if has_control(value):
        raise ValueError(f"{label} 不能含控制字符")
    if trimmed and value != value.strip():
        raise ValueError(f"{label} 首尾不能有空白")
    return value
