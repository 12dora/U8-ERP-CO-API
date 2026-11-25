"""会话 SQL 的文本守卫。给开发者自己用，不是沙箱。

拒绝 sqlcmd 指令、动态 SQL、备份还原、跨库和三段以上的名称。
绕过这段文本仍然可能碰到服务器上的权限，所以预检还要求运行 e2e 的 Windows 账户
对除测试账套两个库以外的 UFDATA_/UFMeta_/UFSystem/UFModel 没有 HAS_DBACCESS。
"""

from __future__ import annotations

import re

from co.e2e.settings import conf

# 库名出现就只能是配置里测试账套的两个库。
_UFDATA = re.compile(r"UFDATA_[A-Za-z0-9_]+", re.IGNORECASE)
_UFMETA = re.compile(r"UFMETA_[A-Za-z0-9_]+", re.IGNORECASE)
_MASTER = re.compile(r"(^|[^A-Za-z0-9_])master([^A-Za-z0-9_]|$)", re.IGNORECASE)
_UFSYSTEM = re.compile(r"UFSystem", re.IGNORECASE)
_CMD = re.compile(r"(?i)^(!!|:|ed\b|exit\b|quit\b|reset\b)")
_IDENT = r"(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)"
_MULTIPART = re.compile(rf"(?i){_IDENT}\s*\.\s*{_IDENT}\s*\.\s*{_IDENT}")
_WORDS = re.compile(
    r"(?i)(\bexec(ute)?\b|\bsp_executesql\b|\bsp_msforeach|"
    r"\bopenrowset\b|\bopenquery\b|\bopendatasource\b|"
    r"\bbackup\b|\brestore\b|\buse\b|"
    r"\b(alter|drop|create)\s+database\b|\bsynonym\b|\bbulk\b|\bdbcc\b|\bxp_|"
    r"\bshutdown\b|\bkill\b|\bgrant\b|\brevoke\b|\bdeny\b|"
    r"\b(create|alter|drop)\s+(login|user)\b)"
)


def _foreign_catalog(text: str) -> bool:
    cfg = conf()
    for match in _UFDATA.finditer(text):
        if match.group(0).upper() != cfg.data_db.upper():
            return True
    for match in _UFMETA.finditer(text):
        if match.group(0).upper() != cfg.meta_db.upper():
            return True
    return False


def _protected_hit(text: str) -> bool:
    # U8 安装目录（及其所在盘，缺省 D:\）按反斜杠和正斜杠两种写法都拒绝。
    for item in conf().protected:
        top = item.rstrip("\\")
        tail = r"[\\/]" if len(top) == 2 else r"(?![A-Za-z0-9_.-])"
        body = re.escape(top[:2]) + "".join(r"[\\/]" + re.escape(part) for part in top.split("\\")[1:])
        if re.search(body + tail, text, re.IGNORECASE):
            return True
    return False


def _command_line(text: str) -> bool:
    for line in text.splitlines():
        if _CMD.match(line.strip()):
            return True
    return False


def _catalog_reason(text: str) -> str:
    if ".." in text:
        return "拒绝跨库写法 .."
    if _foreign_catalog(text):
        return "只允许 " + " 和 ".join(conf().databases)
    if _MASTER.search(text) or _UFSYSTEM.search(text):
        return "拒绝 master 或 UFSystem"
    if _protected_hit(text):
        return "拒绝 U8 安装目录所在路径"
    return ""


def sql_block_reason(text: str) -> str:
    if text.strip() == "":
        return "SQL 为空"
    if _command_line(text):
        return "拒绝 sqlcmd 指令"
    if "$(" in text:
        return "拒绝 sqlcmd 变量"
    catalog = _catalog_reason(text)
    if catalog:
        return catalog
    if _WORDS.search(text):
        return "拒绝危险语句"
    if _MULTIPART.search(text):
        return "拒绝三段及以上名称"
    return ""
