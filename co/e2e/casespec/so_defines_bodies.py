"""销售订单表头自定义项写入（`voucher-so-defines`）用例的请求正文、响应断言、列目录和快照比对。不连接服务器。

快照沿用 head_fix_bodies 的格式 ``{标签: {行键: {列(小写): 值}}}``（标签见 so_defines_sql.snapshot_sql），NULL 记 ``<nil>``。
比对时行版本列（ufts、pubufts）不算变化；写入后允许变的只有请求的 SO 表头列，以及桥盖的修改人、修改日期、修改时间列
（cModifier、dModDate、dmodifysystime）；其余列（含订单行、下游发货单和发票）逐字比较，必须一模一样。
值的口径按路由：NULL、空串、空白和 empty_values 里的值（本用例是「无」）都是空、彼此等价；其余去首尾空白后逐字比较（区分大小写）。
"""

from __future__ import annotations

from co.client.u8co_client import U8CoError
from co.e2e.casespec.arc_cus_bodies import new_key
from co.e2e.casespec.head_fix_bodies import Cell, Snap, changes
from co.e2e.casespec.env import Env
from co.e2e.casespec.arc_cus_person_sql import NIL
from co.e2e.casespec.so_defines_sql import STAMPS
from co.e2e.casespec.steps import auth_fields

ROUTE = "/v1/vouchers/so_defines"
NONE_MARK = "无"
# 调用方另按空值处理的取值（本用例的测试输入）。
EMPTY_VALUES = (NONE_MARK,)
# 两列一起写的列（调用方把两列一起写），单列写的列。
PAIR = ("cdefine1", "cdefine2")
SINGLE = "cdefine3"
# 列目录：列名(小写) → (是文本列, 长度上限；0 为不限)。
Cols = dict[str, tuple[bool, int]]


def sod_body(so_id: str, fields: dict, expect: dict, cascade: bool = False, dry: bool = False) -> dict:
    """请求正文：empty_values 固定为 ["无"]；真写带新的幂等键，预演不带。"""
    body: dict = {
        "type": "sale_order",
        "id": int(so_id),
        "fields": dict(fields),
        "expect": dict(expect),
        "empty_values": list(EMPTY_VALUES),
    }
    if cascade:
        body["cascade"] = True
    if dry:
        body["dry_run"] = True
    else:
        body["idempotency_key"] = new_key("sodefines")
    return body


def send_sod(env: Env, body: dict) -> tuple[int, dict]:
    """成功回 (200, 响应)；失败回 (状态, {ok, code, message, field})，桥给了 detail 时另带 detail。"""
    fields = auth_fields(env)
    fields.update(body)
    try:
        return 200, env.client.call(ROUTE, fields)
    except U8CoError as exc:
        payload: dict = {"ok": False, "code": exc.code, "message": exc.message, "field": exc.field}
        if exc.detail:
            payload["detail"] = exc.detail
        return exc.status, payload


# ---- 值的口径 ----


def blank(value: object) -> bool:
    text = "" if value is None else str(value).strip()
    return text == "" or text in EMPTY_VALUES


def same_value(one: object, two: object) -> bool:
    """路由的口径：空的各种写法彼此相等；其余去首尾空白后逐字相等（区分大小写）。"""
    if blank(one) or blank(two):
        return blank(one) and blank(two)
    return str(one).strip() == str(two).strip()


def raw_of(snap: Snap, so_id: str, col: str) -> str | None:
    """SO 表头某列的原值：NULL 记 None。"""
    value = snap["SO"][so_id].get(col, NIL)
    return None if value == NIL else value


def values_of(snap: Snap, so_id: str, keys: tuple[str, ...] = PAIR) -> dict[str, str | None]:
    return {key: raw_of(snap, so_id, key) for key in keys}


def expect_of(snap: Snap, so_id: str, swap: bool = False) -> dict:
    """请求的 expect：两列的当前原值。swap 为真时把空值换成另一种写法（NULL / 空串 ↔ 「无」），验证空的等价。"""
    out: dict = {}
    for key, raw in values_of(snap, so_id).items():
        if swap and blank(raw):
            raw = NONE_MARK if raw != NONE_MARK else None
        out[key] = raw
    return out


def ufts_of(snap: Snap, so_id: str) -> str:
    return snap["SO"][so_id].get("ufts", "")


def parse_cols(cells: list[str]) -> Cols:
    """columns_sql 的输出 → 列目录；长度上限与桥相同：列宽和自定义项长度取小，0 为不限。"""
    out: Cols = {}
    for cell in cells:
        parts = cell.split("\t")
        if len(parts) != 4 or not parts[2].strip().isdigit() or not parts[3].strip().isdigit():
            continue
        col, own = int(parts[2]), int(parts[3])
        limit = own if col <= 0 else (own if 0 < own < col else col)
        out[parts[0].strip().lower()] = (parts[1].strip() == "1", limit)
    return out


def fits(cols: Cols, key: str, width: int) -> bool:
    """key 是文本列，且长度上限容得下 width 个字符。"""
    text, limit = cols.get(key, (False, 0))
    return text and (limit == 0 or limit >= width)


def non_text(cols: Cols) -> str:
    """列目录里第一个非文本的 cdefineN（例如日期型），没有为空串。"""
    for index in range(1, 17):
        key = f"cdefine{index}"
        if key in cols and not cols[key][0]:
            return key
    return ""


# ---- 响应断言 ----


def expect_status(status: int, payload: dict, want: tuple[int, str], label: str) -> str:
    code = str(payload.get("code") or "")
    if status != want[0] or (want[1] and code != want[1]):
        return f"{label}应 {want[0]} {want[1]}，实际 {status} {code}"[:200]
    return ""


def current_problem(cur: object, want: dict, label: str) -> str:
    """current 的键恰好是请求的列，值按路由口径与 want 相等。"""
    if not isinstance(cur, dict) or set(cur) != set(want):
        keys = sorted(cur) if isinstance(cur, dict) else cur
        return f"{label}的 current 键 {keys!r} 期望 {sorted(want)}"[:200]
    for key, expected in want.items():
        if not same_value(cur.get(key), expected):
            return f"{label}的 current.{key}={cur.get(key)!r} 期望 {expected!r}"[:200]
    return ""


def reply_write(payload: dict, flag: bool, now: dict) -> str:
    """真写（含已是目标值）的响应：changed 与 flag 一致，current 是写后（changed=false 时是当前）的请求列，带 ufts。"""
    if payload.get("changed") is not flag:
        return f"响应 changed={payload.get('changed')!r} 期望 {flag!r}"
    if "ufts" not in payload:
        return "响应缺 ufts"
    return current_problem(payload.get("current"), now, "响应")


def reply_dry(payload: dict, changed: bool, now: dict) -> str:
    """预演（validate）的响应：changed 是「真写会不会改」，current 是库里当前的请求列（预演不写）。"""
    if payload.get("dry_run") is not True:
        return f"预演应 dry_run=true，实际 {payload.get('dry_run')!r}"
    mode = payload.get("mode")
    if mode not in (None, "validate"):
        return f"预演 mode={mode!r} 应为 validate"
    return reply_write(payload, changed, now)


def mismatch_problem(payload: dict, now: dict) -> str:
    """409 state_mismatch 的 detail.current：键恰好是请求的列，值是库里当前值。"""
    detail = payload.get("detail")
    return current_problem(detail.get("current") if isinstance(detail, dict) else None, now, "409 的 detail")


# ---- 快照比对 ----


def hits_of(before: Snap, after: Snap) -> dict[Cell, tuple[str, str]]:
    return changes(before, after, False)


def is_stamp(col: str) -> bool:
    """桥写入时盖的修改人、修改日期、修改时间（不分大小写）。"""
    return col.lower() in {name.lower() for name in STAMPS}


def untouched_problem(before: Snap, after: Snap, label: str) -> str:
    """整份快照（行版本列也算）和原来一模一样。"""
    hits = hits_of(before, after)
    if hits:
        return f"{label}后库里有变化 {sorted(hits)[:3]}"[:200]
    for key in after.get("SO", {}):
        if ufts_of(before, key) != ufts_of(after, key):
            return f"{label}后 SO ufts 变了"
    return ""


def write_problem(before: Snap, after: Snap, wants: dict[str, str]) -> tuple[str, list[str]]:
    """真写之后：只有 wants 里的 SO 表头列（列名小写 → 新值）和修改人 / 日期列变了，且新值对；返回 (问题, 变了的修改人类列名)。"""
    hits = hits_of(before, after)
    stamps: list[str] = []
    for (tag, key, col), (old, new) in sorted(hits.items()):
        if tag == "SO" and col in wants:
            if new.strip() != wants[col]:
                return f"{col} 写后 {new!r} 期望 {wants[col]!r}"[:200], stamps
        elif tag == "SO" and is_stamp(col):
            stamps.append(col)
        else:
            return f"不该变的格子变了 {(tag, key, col)} {old!r}→{new!r}"[:200], stamps
    changed = {col for (tag, _key, col) in hits if tag == "SO"}
    missing = sorted(set(wants) - changed)
    if missing:
        return f"{missing} 没有变化"[:200], stamps
    keys = list(before.get("SO", {}))
    if keys and ufts_of(before, keys[0]) == ufts_of(after, keys[0]):
        return "写后 SO ufts 没变", stamps
    return "", stamps
