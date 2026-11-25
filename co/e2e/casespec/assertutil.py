"""响应和回读的纯判断。不连接服务器。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_STATUSES = frozenset({"not_submitted", "in_approval", "approved", "not_approved", "returned", "not_controlled"})
_HISTORY = "0,1,7,6,8,1,1,7,2"


def long_opinion() -> str:
    return "意" * 501


def sql_cells(text: str) -> list[str]:
    found = []
    for line in text.splitlines():
        raw = line.strip().lstrip("\ufeff")
        if raw.startswith("ROW:"):
            found.append(raw[4:].strip())
    return found


def first_cell(text: str) -> str:
    cells = sql_cells(text)
    if not cells:
        return ""
    return cells[0]


def dig(obj: object, path: str) -> object:
    # 点分路径；列表用 0 起的下标（lines.0.substitutes）。
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def history_actions(payload: dict) -> str:
    rows = payload.get("history")
    if not isinstance(rows, list):
        return ""
    nums = []
    for row in rows:
        if isinstance(row, dict):
            nums.append(str(row.get("action")))
    return ",".join(nums)


def task_hit(payload: dict, doc_id: str, task_type: str) -> bool:
    rows = payload.get("tasks")
    if not isinstance(rows, list) or doc_id == "":
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        same_id = str(row.get("id")) == doc_id
        same_type = str(row.get("task_type")) == task_type
        if same_id and same_type:
            return True
    return False


def instance_off(payload: dict) -> bool:
    wf = payload.get("wf")
    if not isinstance(wf, dict):
        return False
    inst = wf.get("instance")
    if not inst:
        return True
    if isinstance(inst, dict) and inst.get("running") is False:
        return True
    return False


def multiple_qty(rate_text: str) -> str:
    text = (rate_text or "").strip()
    if text == "":
        return "1"
    try:
        rate = Decimal(text)
    except InvalidOperation:
        return "1"
    if rate <= 0:
        return "1"
    return format(rate.normalize(), "f")


def as_num(text: str) -> int | float:
    qty = Decimal((text or "").strip() or "0")
    if qty == qty.to_integral():
        return int(qty)
    return float(qty)


def code_matches(code: str, packed: str, day: str) -> bool:
    if not packed.startswith("P:"):
        return False
    head = packed[2:] + day.replace("-", "")
    return bool(code) and bool(head) and code.startswith(head)


def judge_cell(cell: str, want: str) -> tuple[str, str]:
    if want == "$gt0":
        if cell.isdigit() and int(cell) > 0:
            return "PASS", cell
        return "FAIL", "计数 " + (cell or "空")
    if cell == want:
        return "PASS", cell or "空"
    return "FAIL", "回读 " + (cell or "空") + " 期望 " + want


def judge_verifier(cell: str, mode: str, name: str) -> tuple[str, str]:
    left, _, right = cell.partition("|")
    who = left.strip()
    when = right.strip()
    if mode == "cleared":
        if who == "" and when == "":
            return "PASS", "已清空"
        return "FAIL", "弃审后审核人仍有值"
    if mode == "verified_any":
        if who and when:
            return "PASS", who
        return "FAIL", "审核状态变了"
    if who == name.strip() and name.strip() and when:
        return "PASS", who
    return "FAIL", "回读的审核人不是操作员姓名"


EITHER = -1


def status_problem(status: int, payload: dict, expect: int, code: str) -> str:
    if expect == EITHER:
        return _either_problem(status, payload, code)
    if status != expect:
        return f"{status} {payload.get('code')} {payload.get('message')}"
    got = str(payload.get("code") or "")
    if code and got != code:
        return f"{status} {got}"
    return ""


# expect 为 EITHER 时，code 写成 "200|409:state_mismatch"：任一选项命中即通过。
def _either_problem(status: int, payload: dict, code: str) -> str:
    got = str(payload.get("code") or "")
    for option in code.split("|"):
        want, _, want_code = option.partition(":")
        if want.strip().isdigit() and status == int(want) and (not want_code or got == want_code):
            return ""
    return f"{status} {got} {payload.get('message')}"


def expected_history() -> str:
    return _HISTORY


def status_known(text: str) -> bool:
    return text in _STATUSES
