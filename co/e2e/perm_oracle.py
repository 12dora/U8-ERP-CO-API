"""权限核对的纯计算：由账套库 SQL 的结果独立算出一个操作员「应有」的数据权限，与桥 perm/evaluate、perm/snapshot
的响应逐项比对。不连服务器，不引用桥的代码。

口径（与 U8 客户端、桥的 PermLoad 一致，按设计文档独立再写一遍）：
- 受控对象：AA_BusObject_base 打开数据权限（bAuthControl=1、iAuthType=0）、读取会用到（USED）、操作员不是账套主管、
  不是该对象的数据权限管理员（AA_holdBusobject iAdmin=1），科目（code）另要总账选项 bQryCtlSubj 打开。
- 受控对象的可查询编码：AA_HoldAuth 里 cFuncId 含 R、编码非空、属于本人或所属角色的行；人员（person）下没有行时取
  hr_hi_person。项目（fitem）按「大类 + 编码」成对。受控而没有编码 = 一行都看不到（不是全部）。
- 不受控：{"all": true}。
- 字段权限（columns）：AA_ColumnAuth 里字段权限开关打开（iAuthType=1、bAuthControl=1）的对象，本人 ∪ 所属角色的
  cFuncID 含 N 的行是不可见字段；本人对同一字段有非 N 的行时角色的 N 不算；账套主管为 {}。字段名不是敏感数据，
  照原样比集合（不分大小写、去「B;」前缀）。

编码清单不落盘、不进报告：SQL 端按「去空格、转大写、去重」后逐个取 SHA-256 前 7 字节相加（digest），只输出个数和这个和；
响应端按同一规则算，两边比个数与和。报告里只出现对象名、个数，不出现编码。
功能权限（UA_HoldAuth）、账套主管在 UFSYSTEM，回归账户按设计读不到（sqlguard、预检），这里只核对不变式。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

# 读取会用到的数据权限对象（与桥 PermObj.Used 同一份名单）。
USED = (
    "customer",
    "vendor",
    "department",
    "person",
    "warehouse",
    "inventory",
    "code",
    "fitem",
    "saletype",
    "purchasetype",
    "rd_style",
    "dsign",
)
PERSON = "person"
HR_PERSON = "hr_hi_person"
ITEM = "fitem"
ACCOUNT = "code"
# 编码要从 AA_HoldAuth 取的对象（人员另取 hr_hi_person 兜底）。
CODE_SOURCES = (*USED, HR_PERSON)
PAIR_SEP = "\x1f"
# 字段权限（AA_ColumnAuth）对照 SQL 的行标记。
COLUMN_TAG = "F"
# 受控对象 → 档案列表（archives/list）的档案名；列表按该对象过滤（PermRegistryMore 的档案规则）。科目档案不按科目过滤，不列。
ARCHIVE_OF = {
    "customer": "customer",
    "vendor": "vendor",
    "inventory": "inventory",
    "warehouse": "warehouse",
    "department": "department",
    "person": "person",
    "fitem": "project",
    "saletype": "sale_type",
    "purchasetype": "purchase_type",
    "rd_style": "rd_style",
    "dsign": "voucher_sign",
}
_SHOWN = 3


@dataclass(frozen=True)
class Digest:
    """一组编码的个数和摘要和（顺序无关）。"""

    count: int
    total: int


EMPTY = Digest(0, 0)
ALL = "all"
MISSING = "missing"


@dataclass(frozen=True)
class LaneFacts:
    """账套级事实：打开数据权限的对象（小写）、总账 bQryCtlSubj。"""

    on: frozenset[str]
    gl_ctl: bool


@dataclass(frozen=True)
class OpFacts:
    """一个操作员的账套库事实：数据权限管理员对象、各对象编码摘要（键是小写对象名）。"""

    admin: frozenset[str]
    sums: dict[str, Digest]


def norm_obj(text: object) -> str:
    return str(text or "").strip().lower()


def code_key(code: object, cls: object = None) -> str:
    """与 SQL 端同一规则：去空格、转大写；项目是「大类 \\x1f 编码」。"""
    key = str(code or "").strip().upper()
    if cls is None:
        return key
    return str(cls or "").strip().upper() + PAIR_SEP + key


def key_hash(key: str) -> int:
    # SQL：CONVERT(bigint, SUBSTRING(HASHBYTES('SHA2_256', <nvarchar>), 1, 7))，nvarchar 是 UTF-16LE。
    return int.from_bytes(hashlib.sha256(key.encode("utf-16-le")).digest()[:7], "big")


def digest(keys: Iterable[str]) -> Digest:
    uniq = {key for key in keys if key and key.split(PAIR_SEP)[-1]}
    return Digest(len(uniq), sum(key_hash(key) for key in uniq))


def flag(text: object) -> bool:
    return str(text or "").strip().lower() in ("1", "true")


def parse_lane(cells: list[list[str]]) -> LaneFacts:
    """账套级 SQL 的行：S|<对象>（打开数据权限的对象）、G|<bQryCtlSubj 原值>。"""
    on = frozenset(norm_obj(row[1]) for row in cells if len(row) >= 2 and row[0] == "S" and norm_obj(row[1]))
    gl = [row[1] for row in cells if len(row) >= 2 and row[0] == "G"]
    return LaneFacts(on, flag(gl[0]) if gl else False)


def parse_op(cells: list[list[str]]) -> OpFacts:
    """操作员 SQL 的行：A|<对象>（数据权限管理员）、C|<对象>|<个数>|<摘要和>。"""
    admin = frozenset(norm_obj(row[1]) for row in cells if len(row) >= 2 and row[0] == "A" and norm_obj(row[1]))
    sums: dict[str, Digest] = {}
    for row in cells:
        if len(row) >= 4 and row[0] == "C":
            sums[norm_obj(row[1])] = Digest(int(row[2]), int(row[3].split(".")[0] or "0"))
    return OpFacts(admin, sums)


def controlled(lane: LaneFacts, admin: frozenset[str], supervisor: bool) -> frozenset[str]:
    if supervisor:
        return frozenset()
    return frozenset(obj for obj in USED if obj in lane.on and obj not in admin and (obj != ACCOUNT or lane.gl_ctl))


def expected(lane: LaneFacts, facts: OpFacts, supervisor: bool) -> dict[str, object]:
    """对象 → ALL 或 Digest（受控时的应有编码）。"""
    ctl = controlled(lane, facts.admin, supervisor)
    found: dict[str, object] = {}
    for obj in USED:
        if obj not in ctl:
            found[obj] = ALL
            continue
        got = facts.sums.get(obj, EMPTY)
        if obj == PERSON and got.count == 0:
            got = facts.sums.get(HR_PERSON, EMPTY)
        found[obj] = got
    return found


def _pairs(items: object) -> Digest | str:
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return "pairs 不是对象数组"
    return digest(code_key(item.get("code"), item.get("class")) for item in items)


def _codes(obj: str, items: object) -> Digest | str:
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        return "codes 不是字符串数组"
    if obj == ITEM:
        return digest(item_key(item) for item in items)
    return digest(code_key(item) for item in items)


def item_key(text: str) -> str:
    cls, _sep, code = text.rpartition(PAIR_SEP)
    return code_key(code, cls)


def shape_of(obj: str, value: object) -> object:
    """响应里 data[obj] 的形状：ALL、Digest、MISSING，或说明形状不对的字符串。"""
    if value is None:
        return MISSING
    if not isinstance(value, dict):
        return "不是对象"
    if value.get("all") is True:
        return ALL
    if "pairs" in value:
        return _pairs(value.get("pairs"))
    if "codes" in value:
        return _codes(obj, value.get("codes"))
    return "既没有 all 也没有 codes"


def actual(payload: dict) -> dict[str, object]:
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    return {obj: shape_of(obj, data.get(obj)) for obj in USED}


def one_problem(obj: str, want: object, got: object) -> str:
    if isinstance(got, str) and got not in (ALL, MISSING):
        return f"{obj} 形状不对（{got}）"
    if not isinstance(want, Digest):
        return f"{obj} 应不受控，响应给了 {got.count} 个编码" if isinstance(got, Digest) else ""
    if got == MISSING:
        return f"{obj} 受控却没有给出"
    if not isinstance(got, Digest):
        return f"{obj} 受控（应 {want.count} 个编码），响应却是全部"
    if got != want:
        return f"{obj} 编码不一致：应 {want.count} 个，响应 {got.count} 个"
    return ""


def compare_data(want: dict[str, object], got: dict[str, object]) -> list[str]:
    return [text for text in (one_problem(obj, want[obj], got.get(obj, MISSING)) for obj in USED) if text]


def _str_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return value


def functions_of(payload: dict) -> list[str] | None:
    return _str_list(payload.get("functions"))


def flag_problems(payload: dict, lane: LaneFacts, facts: OpFacts | None, listed: bool) -> list[str]:
    """功能权限读不到 UFSYSTEM，只核对不变式；非主管再核 objects_on、data_admin 与账套库一致。"""
    problems: list[str] = []
    supervisor = payload.get("supervisor")
    funcs = functions_of(payload)
    if not isinstance(supervisor, bool):
        return ["supervisor 不是布尔值"]
    if funcs is None:
        return ["functions 不是字符串数组"]
    if len({item.upper() for item in funcs}) != len(funcs):
        problems.append("functions 有重复")
    if listed and not funcs:
        problems.append("本账套有授权的操作员 functions 为空")
    if supervisor or facts is None:
        return problems
    on = _str_list(payload.get("objects_on"))
    if on is None or {norm_obj(item) for item in on} != set(lane.on):
        problems.append("objects_on 与账套库数据权限开关不一致")
    admin = _str_list(payload.get("data_admin"))
    want_admin = set(facts.admin) if lane.on else set()
    if admin is not None and {norm_obj(item) for item in admin} != want_admin:
        problems.append("data_admin 与 AA_holdBusobject 不一致")
    return problems


def judge(payload: dict, lane: LaneFacts, facts: OpFacts | None, listed: bool) -> list[str]:
    """一个操作员的全部问题（空表示一致）。facts 为 None 只在账套主管时允许（数据全部不受控）。"""
    problems = flag_problems(payload, lane, facts, listed)
    if problems[:1] and problems[0].startswith(("supervisor", "functions 不是")):
        return problems
    supervisor = payload.get("supervisor") is True
    want = expected(lane, facts or OpFacts(frozenset(), {}), supervisor)
    return problems + compare_data(want, actual(payload))


def norm_col_key(text: object) -> str:
    return str(text or "").strip().upper()


def norm_fld(text: object) -> str:
    """字段名比较规则（同桥）：去空格；带「B;」「T;」这类前缀时取最后一个分号之后；不分大小写（转大写）。"""
    return str(text or "").strip().rpartition(";")[2].strip().upper()


def _col_rows(cells: list[list[str]]) -> tuple[list[tuple[bool, str, str]], set[tuple[str, str]]]:
    """(不可见行 (是否角色, 对象, 原字段), 本人可见行 {(对象, 原字段)})；对象、原字段已去空格转大写。"""
    hidden: list[tuple[bool, str, str]] = []
    shown: set[tuple[str, str]] = set()
    for row in cells:
        if len(row) < 5 or row[0] != COLUMN_TAG:
            continue
        group, key, raw = flag(row[1]), norm_col_key(row[2]), str(row[3] or "").strip().upper()
        if not key or not raw:
            continue
        if "N" in str(row[4] or "").upper():
            hidden.append((group, key, raw))
        elif not group:
            shown.add((key, raw))
    return hidden, shown


def expected_columns(cells: list[list[str]], supervisor: bool) -> dict[str, frozenset[str]]:
    """字段权限 SQL 的行 F|<isUserGroup>|<cKey>|<cFld>|<cFuncID>（只含字段权限开关打开的对象）→ 对象 → 不可见字段。

    本人行 ∪ 所属角色行；本人对同一对象、同一字段有非 N 的行时，角色的 N 不算；本人的 N 角色抵消不了。账套主管为空。
    """
    if supervisor:
        return {}
    hidden, shown = _col_rows(cells)
    found: dict[str, set[str]] = {}
    for group, key, raw in hidden:
        if group and (key, raw) in shown:
            continue
        fld = norm_fld(raw)
        if fld:
            found.setdefault(key, set()).add(fld)
    return {key: frozenset(flds) for key, flds in found.items()}


def actual_columns(payload: dict) -> dict[str, frozenset[str]] | str:
    """响应的 columns（对象 → 不可见字段数组）按同一规则归一；形状不对时返回说明。"""
    cols = payload.get("columns")
    if not isinstance(cols, dict):
        return "columns 缺失或不是对象"
    found: dict[str, set[str]] = {}
    for key, flds in cols.items():
        if not isinstance(flds, list) or not flds or not all(isinstance(f, str) for f in flds):
            return f"columns.{key} 不是非空字符串数组"
        found.setdefault(norm_col_key(key), set()).update(norm_fld(f) for f in flds)
    return {key: frozenset(flds) for key, flds in found.items()}


def compare_columns(want: dict[str, frozenset[str]], payload: dict) -> list[str]:
    """字段权限集合相等；说明里只有对象号和字段名。"""
    got = actual_columns(payload)
    if isinstance(got, str):
        return [got]
    diffs: list[str] = []
    for key in sorted(set(want) | set(got)):
        mine, theirs = want.get(key, frozenset()), got.get(key, frozenset())
        more, less = sorted(theirs - mine), sorted(mine - theirs)
        if more or less:
            diffs.append(f"{key} 多 {more or '无'}、少 {less or '无'}")
    if not diffs:
        return []
    tail = f" 等 {len(diffs)} 个对象" if len(diffs) > _SHOWN else ""
    return ["字段权限 columns 不一致：" + "；".join(diffs[:_SHOWN]) + tail]


def compare_self(snap: dict, ev: dict) -> list[str]:
    """同一操作员的 snapshot 与 evaluate：主管标志、功能集合、各对象形状、指纹都相同。"""
    problems: list[str] = []
    if snap.get("supervisor") != ev.get("supervisor"):
        problems.append("supervisor 不同")
    left, right = functions_of(snap), functions_of(ev)
    if left is None or right is None or {x.upper() for x in left} != {x.upper() for x in right}:
        problems.append("functions 不同")
    mine, theirs = actual(snap), actual(ev)
    for obj in USED:
        a, b = mine[obj], theirs[obj]
        if {a, b} <= {ALL, MISSING}:
            continue
        if a != b:
            problems.append(f"{obj} 不同")
    if actual_columns(snap) != actual_columns(ev):
        problems.append("columns 不同")
    if not snap.get("fingerprint") or snap.get("fingerprint") != ev.get("fingerprint"):
        problems.append("fingerprint 不同")
    return problems


def roles_of(payload: dict, listed_roles: list[str] | None) -> tuple[list[str], str]:
    """AA_HoldAuth 角色行用哪份角色：响应的 roles（桥的 UA_Role 全部角色），没有时用操作员档案的 roles（只含本账套有授权的角色）。"""
    own = _str_list(payload.get("roles"))
    if own is not None:
        return [item.strip() for item in own if item.strip()], ""
    if listed_roles is not None:
        return [item.strip() for item in listed_roles if item.strip()], "角色取自操作员档案"
    return [], "取不到角色"


def roles_problem(payload: dict, listed_roles: list[str] | None) -> str:
    """操作员档案里的角色都要在响应 roles 里（只对非主管）。桥对账套主管不读角色（roles 为 []），主管只核
    supervisor、数据全部、columns 为 {}，不比角色。"""
    if payload.get("supervisor") is True:
        return ""
    own = _str_list(payload.get("roles"))
    if own is None or listed_roles is None:
        return ""
    missing = {r.strip().upper() for r in listed_roles} - {r.strip().upper() for r in own}
    return f"操作员档案里的 {len(missing)} 个角色不在响应 roles 里" if missing else ""


def summary(bad: dict[str, list[str]], total: int) -> str:
    """不一致的操作员数和前几条问题（只有操作员编码、对象名和个数）。"""
    if not bad:
        return ""
    shown = [f"{op}：{'；'.join(problems[:2])}" for op, problems in list(bad.items())[:_SHOWN]]
    return f"{len(bad)}/{total} 个操作员不一致　" + "　".join(shown)
