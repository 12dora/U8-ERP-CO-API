"""权限核对（perm_check）的读取一致性和无授权操作员挑选。

read_subset：每个操作员用自己的凭据取 snapshot，对受控对象（编码或项目对）翻页读档案列表（每页 500，至多 40 页），
  每一行的编码都要在 snapshot 的编码里。列表 403（没有该档案的查询权限）算拒绝，同样是不越权，记进说明。
  编码只在内存里比较，不写盘，报告只记个数。
stray_codes：账套库里出现过（数据权限行、人员对照）而不在本账套操作员档案里的操作员编码（系统管理员 admin 除外）。
"""

from __future__ import annotations

from co.e2e.cases import Operator
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.flip_util import rows
from co.e2e.casespec.perm_check_io import ARC_LIST, call_as, snapshot
from co.e2e.casespec.arc_probe_sql import safe_user
from co.e2e.casespec.perm_check_sql import stray_users_sql
from co.e2e.perm_oracle import ARCHIVE_OF, ITEM, code_key, item_key

_PAGE = 500
_PAGES = 40
_STRAY = 200


def allowed(obj: str, value: object) -> set[str] | None:
    """snapshot 里 data[obj] 允许的编码键（code_key 规则）；全部或形状不认识时为 None（调用方另判）。"""
    if not isinstance(value, dict) or value.get("all") is True:
        return None
    pairs = value.get("pairs")
    if isinstance(pairs, list):
        return {code_key(p.get("code"), p.get("class")) for p in pairs if isinstance(p, dict)}
    codes = value.get("codes")
    if not isinstance(codes, list):
        return None
    if obj == ITEM:
        return {item_key(str(c)) for c in codes}
    return {code_key(c) for c in codes}


def row_key(obj: str, row: dict) -> str:
    code = str(row.get("code") or "")
    if obj != ITEM:
        return code_key(code)
    cls, _sep, item = code.partition(":")
    return code_key(item, row.get("project_class") or cls)


def _scan(env: Env, op: Operator, obj: str, keys: set[str]) -> tuple[int, int, str]:
    """(行数, 越权行数, 说明)。说明为 forbidden 表示 403，truncated 表示没有翻完。"""
    seen = extra = 0
    after = None
    for _page in range(_PAGES):
        body: dict = {"archive": ARCHIVE_OF[obj], "limit": _PAGE}
        if after not in (None, ""):
            body["after"] = after
        status, payload = call_as(env, op, ARC_LIST, body)
        if status == 403:
            return seen, extra, "forbidden"
        if status != 200:
            raise ValueError(f"{op.code} 读 {ARCHIVE_OF[obj]} 列表得 {status} {payload.get('code')}")
        items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        seen += len(items)
        extra += sum(row_key(obj, item) not in keys for item in items)
        after = payload.get("next")
        if after in (None, ""):
            return seen, extra, ""
    return seen, extra, "truncated"


def _one(env: Env, op: Operator) -> tuple[list[str], list[str]]:
    data = snapshot(env, op).get("data")
    data = data if isinstance(data, dict) else {}
    bad: list[str] = []
    notes: list[str] = []
    for obj in ARCHIVE_OF:
        keys = allowed(obj, data.get(obj))
        if keys is None:
            continue
        seen, extra, note = _scan(env, op, obj, keys)
        if extra:
            bad.append(f"{op.code} 的 {obj} 列表有 {extra}/{seen} 行不在 snapshot 里")
        notes.append(f"{op.code}·{obj} {seen}/{len(keys)}" + ("（403）" if note == "forbidden" else "（未翻完）" if note else ""))
    return bad, notes


def read_subset(env: Env, ops: list[Operator]) -> tuple[str, str]:
    bad: list[str] = []
    notes: list[str] = []
    for op in ops:
        problems, seen = _one(env, op)
        bad += problems
        notes += seen
    if bad:
        return "FAIL", "；".join(bad[:3])
    if not notes:
        return "N/A", f"{len(ops)} 个操作员都没有受控的档案对象（主管或开关关着）"
    return "PASS", "列表行数/允许编码数：" + "、".join(notes[:8])


def _safe(code: str) -> bool:
    try:
        safe_user(code)
    except ValueError:
        return False
    return True


def stray_codes(env: Env, spec: CaseSpec, listed: set[str]) -> tuple[list[str], int]:
    """(候选编码, 因编码含其他字符跳过的个数)。"""
    found: list[str] = []
    skipped = 0
    for row in rows(env, spec, stray_users_sql(_STRAY)):
        code = row[1].strip() if len(row) >= 2 and row[0] == "U" else ""
        if not code or code.upper() in listed or code.upper() == "ADMIN":
            continue
        if _safe(code):
            found.append(code)
        else:
            skipped += 1
    return found, skipped
