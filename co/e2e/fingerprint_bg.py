"""还原比对里 U8 后台表的容忍判别。

还原会话内的指纹在 SINGLE_USER 里取，但 RESTORE 会把库带回快照当时的 MULTI_USER，到重新占回唯一连接之前，
U8 应用服务器（任务服务、报表服务、登录记账，可能是另一台主机上的池连接）可能抢先写入。下面名单里的表只由 U8 后台写，
与被测业务无关；它们的差异只有能归到还原之后的 U8 活动时才容忍：
  * 放开多用户之后的指纹里同一张表仍在变（U8 一直在写它）；
  * 或者是还原之后才出现的 UFTmpTable_<n>_<n>（U8 登录会话建的临时表）。
U8TaskConfig 不在名单里，仍走 taskcfg.classify 的调度记账判别。业务表（SO_、PO_、Rd、AP_、GL_ 等）不在名单里，差异一律失败。
容忍的每一行都写进报告「U8 后台表（已容忍）」，不隐藏。
"""

from __future__ import annotations

import re

from co.e2e.settings import conf

# 账套数据库（UFDATA_）里只由 U8 后台写的表。
DATA_BACKGROUND = frozenset(
    {
        "dbo.U8LoginProcess",
        "dbo.U8TaskLog",
        "dbo.tc_ufrefresh",
        "dbo.MetricProcessEvent",
        "dbo.MetricValues",
        "dbo.UAP_Report_StaticReport",
        "dbo.UA_ToNextYear_base",
    }
)
# 元数据库（UFMeta_）里只由 U8 后台写的表。
META_BACKGROUND = frozenset({"dbo.UAP_ReportStaticRpt"})
_TEMP = re.compile(r"dbo\.UFTmpTable_\d+_\d+")
_ONLY_AFTER = "仅出现在测试后"
_TOTALS = "合计不一致"


def _parse(line: str) -> tuple[str, str, str] | None:
    # 指纹差异行：「<库> <架构>.<表> <说明>」；合计行「<库> 合计不一致」没有表名。
    parts = line.split(" ", 2)
    if len(parts) != 3 or "." not in parts[1]:
        return None
    return parts[0], parts[1], parts[2]


def is_totals(line: str) -> bool:
    return line in {f"{name} {_TOTALS}" for name in conf().databases}


def _listed(database: str, table: str) -> bool:
    cfg = conf()
    if database == cfg.data_db:
        return table in DATA_BACKGROUND
    if database == cfg.meta_db:
        return table in META_BACKGROUND
    return False


def _touched(post: list[str]) -> set[tuple[str, str]]:
    found = set()
    for line in post:
        parsed = _parse(line)
        if parsed is not None:
            found.add((parsed[0], parsed[1]))
    return found


def _evidence(parsed: tuple[str, str, str], touched: set[tuple[str, str]]) -> str:
    database, table, rest = parsed
    if database == conf().data_db and _TEMP.fullmatch(table) and rest == _ONLY_AFTER:
        return "还原之后 U8 新建的临时表"
    if _listed(database, table) and (database, table) in touched:
        return "放开多用户之后仍在变"
    return ""


def split_background(diff: list[str], post: list[str]) -> tuple[list[str], list[str]]:
    """把差异分成（已容忍的 U8 后台表行，其余行）。已容忍的行带上依据；合计行留在其余行里，由调用方决定。"""
    touched = _touched(post)
    tolerated: list[str] = []
    rest: list[str] = []
    for line in diff:
        parsed = _parse(line)
        reason = _evidence(parsed, touched) if parsed is not None else ""
        if reason:
            tolerated.append(f"{line}（{reason}）")
        else:
            rest.append(line)
    return tolerated, rest


def only_totals(lines: list[str]) -> bool:
    return all(is_totals(line) for line in lines)
