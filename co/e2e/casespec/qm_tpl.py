"""质量单据（报检单、检验单、不良品处理单）模板必输的自定义项。

桥在调用 U8 之前按单据模板核对必输项（桥 QmTplReq / QmRejTpl：voucheritems.IsNull=1），缺了 400「缺少必输字段 …（U8 单据
模板设置为必输）」。各账套的模板不一样（例如把扩展自定义项 chdefine15 设为必输），用例生单前先按同一条查询读出必输字段：
请求能带的表头自定义项（报检单 cdefine1–16，检验单、不良品处理单另有 chdefine11–16）按列类型补占位值（文本 E2E、数值 1、
日期取当天），记进 bag，生单正文（qm_write 的构造器、qm_reject.body）合进表头；表体或请求带不了的自定义项设为必输时记 N/A。
其余必输字段（存货、数量、日期等）由桥按来源单据填，不在这里处理。步骤 kind 为 "custom"（QM_TPL_OPS 并入 ops_run）。
"""

from __future__ import annotations

import re

from co.e2e.casespec.assertutil import sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_write_sql import TPL_TABLES, tpl_required_sql
from co.e2e.casespec.sqlexec import run_guarded

OP = "qm_tpl"
PLACEHOLDER = "E2E"
# cdefine、扩展表头 chdefine、扩展表体 cbdefine。
_DEFINE = re.compile(r"^(c|ch|cb)define([1-9][0-9]?)\Z")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,30}\Z")
_NUMERIC = frozenset(("int", "smallint", "tinyint", "bigint", "decimal", "numeric", "float", "real", "money", "smallmoney", "bit"))
_DATES = frozenset(("date", "datetime", "smalldatetime", "datetime2", "datetimeoffset"))
# 报检单（351 / 352）表头只收 cdefine1–16；检验单、不良品处理单另收 chdefine11–16（桥 QmReq.HeadKey、QmRejReq）。
_INSPECT_VTS = (351, 352)


def tpl_step(vt: int, key: str) -> Step:
    if vt not in TPL_TABLES or _KEY.fullmatch(key) is None:
        raise ValueError("模板 VT 或 bag 键不对")
    return Step("custom", fields=(("vt", str(vt)), ("key", key)), sql_name=OP, id_from="none")


def _carried(name: str, section: str, vt: int) -> bool:
    found = _DEFINE.fullmatch(name.lower())
    if found is None or section == "B":
        return False
    number = int(found.group(2))
    if found.group(1) == "c":
        return number <= 16
    return found.group(1) == "ch" and vt not in _INSPECT_VTS and 11 <= number <= 16


def _value(kind: str, today: str) -> str:
    low = kind.lower()
    if low in _NUMERIC:
        return "1"
    if low in _DATES:
        return today
    return PLACEHOLDER


def split_rows(cells: list[str], vt: int, today: str) -> tuple[dict[str, str], list[str]]:
    """模板必输行 → (要补的表头自定义项 {小写字段名: 值}, 请求带不了的自定义项)。非自定义项不管（桥按来源填）。"""
    fill: dict[str, str] = {}
    stuck: list[str] = []
    for cell in cells:
        parts = [part.strip() for part in cell.split("|")]
        if len(parts) != 3 or not parts[0]:
            continue
        name, section, kind = parts
        if _carried(name, section.upper(), vt):
            fill[name.lower()] = _value(kind, today)
        elif _DEFINE.fullmatch(name.lower()) is not None:
            label = name + ("（表体）" if section.upper() == "B" else "")
            if label not in stuck:
                stuck.append(label)
    return fill, stuck


def required_defines(env: Env, spec: CaseSpec, vt: int) -> tuple[dict[str, str], list[str]]:
    cells = sql_cells(run_guarded(env, tpl_required_sql(vt), spec.name))
    return split_rows(cells, vt, str(env.today or ""))


def stuck_text(vt: int, stuck: list[str]) -> str:
    return f"U8 单据模板（VT {vt}）把接口带不了的自定义项设为必输：" + "、".join(stuck)


def pack(fill: dict[str, str]) -> str:
    return ";".join(f"{name}={value}" for name, value in sorted(fill.items()))


def unpack(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in (text or "").split(";"):
        name, sep, value = item.partition("=")
        if sep == "=" and _DEFINE.fullmatch(name) is not None:
            found[name] = value
    return found


def merge_head(head: dict, fill: dict[str, str]) -> dict:
    """把模板必输的自定义项合进表头；表头已有的（不分大小写，例如挑单取到的 chDefine11）不覆盖。"""
    have = {str(key).lower() for key in head}
    for name, value in fill.items():
        if name not in have:
            head[name] = value
    return head


def with_tpl(env: Env, body: dict, key: str) -> dict:
    # 生单正文合进 bag[key] 里记下的模板必输自定义项；没有时原样返回（没跑 qm_tpl 步骤的老路径不变）。
    fill = unpack(env.bag.get(key, ""))
    if fill:
        body["head"] = merge_head(dict(body.get("head") or {}), fill)
    return body


def qm_tpl(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    args = dict(step.fields)
    raw, key = args.get("vt", ""), args.get("key", "")
    if not raw.isdigit() or int(raw) not in TPL_TABLES or _KEY.fullmatch(key) is None:
        return "FAIL", "模板 VT 或 bag 键不对"
    vt = int(raw)
    try:
        fill, stuck = required_defines(env, spec, vt)
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    if stuck:
        return "N/A", stuck_text(vt, stuck)[:240]
    env.bag[key] = pack(fill)
    if not fill:
        return "PASS", f"VT {vt} 模板没有必输的自定义项"
    return "PASS", f"VT {vt} 模板必输自定义项补占位：{pack(fill)}"[:240]


QM_TPL_OPS = {OP: qm_tpl}
