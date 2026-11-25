"""操作员读取的附加核对，接在 archive-read-operator 之后作为第二步（sql_name 为 operator_probe）。

U8 系统库 UFSystem 被 SQL 守卫拒绝（sqlguard，回归账户也没有 UFSystem 的访问权限），nState、iUserType 不能直接对库。
这里核对桥返回的口径和账套库里能对上的部分：
- 列出本账套有授权的操作员（每页 100 行，至多 5 页），每一项 state 是整数、disabled 等于 state 不是 0；
- 每一项的 person_code 等于账套库 UserHrPersonContro 里该操作员编码最小的人员（没有对照为 null）；
- 登录操作员能登录，所以若在列表范围内，读取应是 state 0、disabled false。
detail 记下各 state 取值的个数，供实测 nState 非 0 的具体取值。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.doc_list import items_of
from co.e2e.casespec.arc_probe_sql import person_sql, safe_user
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

_PAGE = 100
_PAGES = 5
_ARCHIVE = "operator"


def _call(env: Env, route: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)


def _list_all(env: Env) -> list[dict]:
    rows: list[dict] = []
    after = None
    for _page in range(_PAGES):
        extra: dict = {"archive": _ARCHIVE, "limit": _PAGE}
        if after is not None:
            extra["after"] = after
        status, payload = _call(env, "/v1/archives/list", extra)
        if status != 200:
            raise RuntimeError(f"操作员列表 {status} {payload.get('code')}")
        rows.extend(items_of(payload))
        after = payload.get("next")
        if after in (None, ""):
            break
    return rows


def _state_problem(row: dict) -> str:
    state, disabled = row.get("state"), row.get("disabled")
    if type(state) is not int:
        return f"操作员 {row.get('code')} 的 state 不是整数：{state!r}"
    if disabled is not (state != 0):
        return f"操作员 {row.get('code')} 的 disabled={disabled!r} 与 state={state} 不符"
    return ""


def _person_problem(env: Env, spec: CaseSpec, rows: list[dict]) -> str:
    """可安全拼进 SQL 的操作员编码，逐个比对人员关联；编码含其他字符的跳过。"""
    by_code = {}
    for row in rows:
        code = str(row.get("code") or "")
        try:
            by_code[safe_user(code)] = row
        except ValueError:
            continue
    if not by_code:
        return ""
    want = {}
    for cell in sql_cells(run_guarded(env, person_sql(sorted(by_code)), spec.name)):
        code, _sep, person = cell.partition("|")
        want[code.strip()] = person.strip()
    for code, row in by_code.items():
        got = str(row.get("person_code") or "")
        if code not in want:
            return f"人员对照回读缺操作员 {code}"
        if got != want[code]:
            return f"操作员 {code} 的 person_code={got or 'null'}，账套库人员对照为 {want[code] or '无'}"
    return ""


def _self_note(env: Env) -> str:
    code = env.operator.code
    status, got = _call(env, "/v1/archives/get", {"archive": _ARCHIVE, "code": code})
    if status == 404:
        return "登录操作员不在列表范围"
    if status != 200:
        raise RuntimeError(f"读取登录操作员 {status} {got.get('code')}")
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    if fields.get("state") != 0 or fields.get("disabled") is not False:
        raise RuntimeError(f"登录操作员 state={fields.get('state')!r} disabled={fields.get('disabled')!r}，应为 0、false")
    return "登录操作员 state 0"


def ua_probe(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _list_all(env)
    if not rows:
        return "PASS", "列表为空，无从核对 state"
    for row in rows:
        problem = _state_problem(row)
        if problem:
            return "FAIL", problem
    problem = _person_problem(env, spec, rows)
    if problem:
        return "FAIL", problem
    counts: dict[int, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    seen = "、".join(f"{state}×{n}" for state, n in sorted(counts.items()))
    return "PASS", f"{len(rows)} 个操作员，state 取值 {seen}；{_self_note(env)}"


UA_PROBE_OPS = {"operator_probe": ua_probe}


def ua_probe_step() -> Step:
    return Step("custom", sql_name="operator_probe", id_from="none")
