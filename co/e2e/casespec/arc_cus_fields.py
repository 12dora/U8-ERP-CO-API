"""客户字段 `archive-customer-fields`：现有客户整行快照 → customer_fields 逐项改简称、地区、停用日期、收付款协议、
名称、税号，再改联系人 + 开户银行（一次两键）、所属行业（联动行业名称）、自定义项 1（arc_cus_fields_more）
→ 每步只允许被改的列加修改人 / 日期 / 行版本变化，响应的 before / after 只含请求的键 → 用路由改回原值，整行与快照一致。

否定路径同用例：预演不改行；no-op 返回 changed:false 且整行（含行版本）不变；expect 不符 409 state_mismatch
（名称不符时核对 detail.current 给的名称、税号当前值）；简称与别的客户重复 409 abbrname_taken；
地区不存在 409 district_invalid；收付款协议不存在 / 已停用 409 sa_protocol_invalid；协议小写重发是 no-op；
小写写入存档案编码；客户不存在 404；未知字段、早于建档日的停用日期 400。
账套没有末级地区分类时，用 archives/create 建一个一级地区，用例结束前删掉（路由删不了就在测试账套上用 SQL 删）。
账套没有可用的收付款协议、或客户现有协议写不回去时，只跳过协议步骤并在结论里注明（不新建协议档案），其余键照测；
没有可换的末级所属行业、自定义项 1 取自其他档案或现值非空时同样只跳过对应步骤；
客户名称为空时同样只跳过名称步骤（名称不能写空，改了还原不回去）；没有别的客户有简称时跳过简称重复一步。
步骤 kind 为 core_cusfields，挂在 arc_write.CORE_HANDLERS。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.arc_cus_bodies import (
    field_view,
    fields_body,
    load_row,
    merged,
    new_key,
    other_date,
    other_text,
    reply_fields,
    reply_fields_dry,
    row_problem,
    stamp_problem,
)
from co.e2e.casespec.arc_cus_person_bodies import changed, expect, send, volatile_set
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_cus_fields_more import Picks, Write, pick_more
from co.e2e.casespec.arc_write_more_sql import arc_more_pick_sql, safe_part
from co.e2e.casespec.arc_cus_sql import (
    agreement_count_sql,
    cus_row_sql,
    customer_created_sql,
    other_abbr_sql,
    district_count_sql,
    district_delete_sql,
    district_leaf_sql,
    district_pick_sql,
    pick_protocols,
)
from co.e2e.casespec.arc_cus_person_sql import volatile_sql
from co.e2e.casespec.gl_voucher_sql import safe_code
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.testdata import DataMissing
from co.e2e.testdata import value as td

_NAME = "archive-customer-fields"
_DATA = ("archives.customer.existing",)
_ROUTES = ("/v1/archives/customer_fields", "/v1/archives/create", "/v1/archives/delete")
_OP = "customer_fields"
_MISSING = "ZZNOCUS01"
_NO_DISTRICT = "ZZNODC9"
_NO_PROTOCOL = "ZZNOPROT"
_OK = (200, "")
_COLUMN = {
    "abbrname": "cCusAbbName",
    "district": "cDCCode",
    "end_date": "dEndDate",
    "sa_protocol": "cCusSAProtocol",
    "name": "cCusName",
    "tax_no": "cCusRegCode",
    "contact": "cCusPerson",
    "bank_open": "cCusBank",
    "ccustradeccode": "cCusTradeCCode",
    "self_define1": "cCusDefine1",
}
_ABBR_LIMIT = 60
_NAME_LIMIT = 98
_TAX_LIMIT = 50


class _NotApplicable(Exception):
    """账套里没有这个用例需要的数据：记 N/A，不是失败。"""


@dataclass
class _State:
    code: str
    snap: dict[str, str] = field(default_factory=dict)
    cur: dict[str, str] = field(default_factory=dict)
    volatile: frozenset[str] = field(default_factory=frozenset)
    orig: dict[str, str] = field(default_factory=dict)
    created: str = ""
    district: str = ""
    made_district: str = ""
    protocol: str = ""
    stopped: str = ""
    protocol_note: str = ""
    new_abbr: str = ""
    new_date: str = ""
    picks: Picks = field(default_factory=Picks)
    dirty: bool = False


def _sql(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _row(env: Env, spec: CaseSpec, code: str) -> dict[str, str]:
    return load_row(sql_cells(run_guarded(env, cus_row_sql(code), spec.name)))


def _post(env: Env, body: dict, want: tuple[int, str], label: str) -> tuple[str, dict]:
    status, payload = send(env, _OP, body)
    return expect(status, payload, want, label), payload


def _plan(label: str, name: str, new: str) -> Write:
    """把字段 name 改成 new：预期行改对应的列。"""
    return Write(label, {name: new}, {_COLUMN[name]: new})


def _view(row: dict[str, str], keys: Iterable[str]) -> dict[str, str]:
    """响应 before / after 只含请求的键。"""
    view = field_view(row)
    return {key: view[key] for key in keys}


def _load(env: Env, spec: CaseSpec, state: _State) -> str:
    names = [item.strip() for item in sql_cells(run_guarded(env, volatile_sql(), spec.name))]
    state.volatile = volatile_set(names)
    state.snap = _row(env, spec, state.code)
    if not state.snap:
        return f"没有客户 {state.code}"
    state.cur = dict(state.snap)
    state.orig = field_view(state.snap)
    if not state.orig["abbrname"]:
        raise _NotApplicable("客户简称为空，customer_fields 不能写空简称，无法还原")
    created = _sql(env, spec, customer_created_sql(state.code))
    state.created = "" if created in ("", "-") else created
    bad = _orig_district(env, spec, state)
    if bad:
        raise _NotApplicable(bad)
    state.new_abbr = other_text(state.orig["abbrname"], "E2E简称", _ABBR_LIMIT)
    state.new_date = other_date(state.orig["end_date"], env.today, state.created)
    return ""


def _orig_district(env: Env, spec: CaseSpec, state: _State) -> str:
    """客户现有地区必须是末级：改走再改回要用路由，路由只收末级。"""
    now = state.orig["district"]
    try:
        leaf = _sql(env, spec, district_leaf_sql(now)) if now else ""
    except ValueError:
        leaf = ""
    return "" if not now or leaf == now else "客户现有的地区分类不是末级，路由写不回去"


def _make_district(env: Env, spec: CaseSpec, state: _State) -> str:
    free = _sql(env, spec, arc_more_pick_sql("district_class"))
    if not free:
        raise _NotApplicable("账套没有末级地区分类，也挑不到空闲的地区编码")
    code = safe_part(free)
    body = {
        "archive": "district_class",
        "code": code,
        "fields": {"name": "u8co e2e 地区"},
        "idempotency_key": new_key("cusfields"),
    }
    status, payload = send(env, "create", body)
    if status == 403:
        raise _NotApplicable("账套不放行 archives/create 地区分类，也没有现成的末级地区")
    state.made_district = code if status == 200 else ""
    bad = expect(status, payload, _OK, "新建地区分类")
    if bad:
        return bad
    if _sql(env, spec, district_leaf_sql(code)) != code:
        return "新建的地区分类不是末级"
    state.district = code
    return ""


def _pick_district(env: Env, spec: CaseSpec, state: _State) -> str:
    skip = state.orig["district"]
    try:
        picked = safe_code(_sql(env, spec, district_pick_sql(skip)))
    except ValueError:
        return _make_district(env, spec, state)
    state.district = picked
    return ""


def _pick_protocol(env: Env, spec: CaseSpec, state: _State) -> str:
    """协议步骤要用的码；挑不到或现值写不回去只记说明、跳过协议步骤（在任何写入、新建地区之前跑）。"""
    picked = pick_protocols(lambda query: _sql(env, spec, query), state.orig["sa_protocol"], env.today[:10])
    state.protocol, state.stopped, state.protocol_note = picked
    return ""


def _pick_more(env: Env, spec: CaseSpec, state: _State) -> str:
    """扩展键要用的数据（在任何写入之前跑）。"""
    state.picks = pick_more(lambda query: _sql(env, spec, query), state.orig, state.snap)
    return ""


def _unchanged(env: Env, spec: CaseSpec, state: _State, label: str) -> str:
    hits = changed(state.cur, _row(env, spec, state.code), frozenset())
    return f"{label}不应改行 {hits}"[:200] if hits else ""


def _dry_run(env: Env, spec: CaseSpec, state: _State) -> str:
    body = fields_body(state.code, {"abbrname": state.new_abbr}, {"abbrname": state.orig["abbrname"]}, True)
    bad, payload = _post(env, body, _OK, "预演改简称")
    before = {"abbrname": state.orig["abbrname"]}
    return bad or reply_fields_dry(payload, state.code, before) or _unchanged(env, spec, state, "预演")


def _apply(env: Env, spec: CaseSpec, state: _State, plan: Write) -> str:
    """真写：expect 是请求键的当前值；响应、整行和审计列都按预期核对。"""
    state.dirty = True
    expected = _view(state.cur, plan.fields)
    bad, payload = _post(env, fields_body(state.code, plan.fields, expected), _OK, plan.label)
    if bad:
        return bad
    before = _view(state.cur, plan.fields)
    after = _view(merged(state.cur, plan.edits), plan.fields)
    bad = reply_fields(payload, state.code, (before, after), True)
    row = _row(env, spec, state.code)
    bad = bad or row_problem(state.cur, plan.edits, row, state.volatile)
    bad = bad or stamp_problem(state.cur, row, str(getattr(env, "operator_name", "")))
    state.cur = row
    return f"{plan.label}：{bad}"[:200] if bad else ""


def _noop(env: Env, spec: CaseSpec, state: _State) -> str:
    now = state.new_abbr
    body = fields_body(state.code, {"abbrname": now}, {"abbrname": now})
    bad, payload = _post(env, body, _OK, "no-op")
    view = _view(state.cur, ("abbrname",))
    return bad or reply_fields(payload, state.code, (view, view), False) or _unchanged(env, spec, state, "no-op")


def _reject(env: Env, spec: CaseSpec, state: _State, plan: tuple[dict, dict, tuple[int, str], str]) -> str:
    fields, expected, want, label = plan
    bad, _payload = _post(env, fields_body(state.code, fields, expected), want, label)
    return bad or _unchanged(env, spec, state, label)


def _rejects(env: Env, spec: CaseSpec, state: _State) -> str:
    view = field_view(state.cur)
    bad = _reject(env, spec, state, ({"abbrname": "E2E不符"}, {"abbrname": "ZZ预期不符"}, (409, "state_mismatch"), "expect 不符"))
    if bad or _sql(env, spec, district_count_sql(_NO_DISTRICT)) != "0":
        return bad
    bad = _reject(env, spec, state, ({"district": _NO_DISTRICT}, {"district": view["district"]}, (409, "district_invalid"), "不存在的地区"))
    return bad or _reject(env, spec, state, ({"nickname": "x"}, {}, (400, ""), "未知字段"))


def _bad_protocols(env: Env, spec: CaseSpec, state: _State) -> str:
    """已停用、不存在的协议都 409 sa_protocol_invalid 且行不变（当前值为空时跑，不会撞上「与现值相同不查」）。"""
    now = field_view(state.cur)["sa_protocol"]
    want = (409, "sa_protocol_invalid")
    if state.stopped:
        bad = _reject(env, spec, state, ({"sa_protocol": state.stopped}, {"sa_protocol": now}, want, "已停用的收付款协议"))
        if bad:
            return bad
    if _sql(env, spec, agreement_count_sql(_NO_PROTOCOL)) != "0":
        return ""
    return _reject(env, spec, state, ({"sa_protocol": _NO_PROTOCOL}, {"sa_protocol": now}, want, "不存在的收付款协议"))


def _case_noop(env: Env, spec: CaseSpec, state: _State) -> str:
    """当前协议的小写重发：不分大小写等于现值，应 changed:false，整行（含行版本）不变。"""
    label = "小写重发收付款协议"
    now = field_view(state.cur)["sa_protocol"]
    view = {"sa_protocol": now}
    bad, payload = _post(env, fields_body(state.code, {"sa_protocol": now.lower()}, {"sa_protocol": now}), _OK, label)
    bad = bad or reply_fields(payload, state.code, (view, view), False)
    return f"{label}：{bad}"[:200] if bad else _unchanged(env, spec, state, label)


def _lower_set(env: Env, spec: CaseSpec, state: _State) -> str:
    """从另一个值用小写编码写入：库里应存档案里的编码。编码没有字母时跳过。"""
    code = state.protocol
    if code.lower() == code:
        return ""
    return _apply(env, spec, state, Write("小写设收付款协议", {"sa_protocol": code.lower()}, {_COLUMN["sa_protocol"]: code}))


def _protocol_steps(env: Env, spec: CaseSpec, state: _State) -> str:
    """设 → 小写重发 no-op → 清空 → 已停用 / 不存在 409 → 小写设；原值由 _restore 走路由写回。"""
    if not state.protocol:
        return ""
    code = state.protocol
    return _first_bad((
        lambda: _apply(env, spec, state, _plan("设收付款协议", "sa_protocol", code)),
        lambda: _case_noop(env, spec, state),
        lambda: _apply(env, spec, state, _plan("清收付款协议", "sa_protocol", "")),
        lambda: _bad_protocols(env, spec, state),
        lambda: _lower_set(env, spec, state),
    ))


def _current_409(env: Env, spec: CaseSpec, state: _State) -> str:
    """名称 expect 不符：409 state_mismatch，detail.current 是 fields 各键（名称、税号）的当前值，行不变。"""
    label = "名称 expect 不符"
    view = field_view(state.cur)
    fields = {"name": "E2E不符", "tax_no": view["tax_no"]}
    bad, payload = _post(env, fields_body(state.code, fields, {"name": "ZZ预期不符"}), (409, "state_mismatch"), label)
    if bad:
        return bad
    detail = payload.get("detail")
    got = detail.get("current") if isinstance(detail, dict) else None
    want = {"name": view["name"], "tax_no": view["tax_no"]}
    if got != want:
        return f"{label}：detail.current={got!r} 期望 {want!r}"[:200]
    return _unchanged(env, spec, state, label)


def _name_tax_steps(env: Env, spec: CaseSpec, state: _State) -> str:
    """名称：改 → expect 不符 409 → 改回（原名称为空时跳过）；税号：设 → 改回（原值为空即清空）。"""
    orig = state.orig
    steps: tuple[Callable[[], str], ...] = ()
    if orig["name"]:
        new_name = other_text(orig["name"], "E2E名称", _NAME_LIMIT)
        steps = (
            lambda: _apply(env, spec, state, _plan("改名称", "name", new_name)),
            lambda: _current_409(env, spec, state),
            lambda: _apply(env, spec, state, _plan("名称改回", "name", orig["name"])),
        )
    new_tax = other_text(orig["tax_no"], "E2ETAX", _TAX_LIMIT)
    return _first_bad((
        *steps,
        lambda: _apply(env, spec, state, _plan("设税号", "tax_no", new_tax)),
        lambda: _apply(env, spec, state, _plan("税号改回", "tax_no", orig["tax_no"])),
    ))


def _more_steps(env: Env, spec: CaseSpec, state: _State) -> str:
    """联系人 + 开户银行、所属行业、自定义项 1：写入（改完改回）和被拒绝的请求。"""
    picks = state.picks
    writes = tuple(lambda extra=extra: _apply(env, spec, state, extra) for extra in picks.steps)
    rejects = tuple(
        lambda item=item: _reject(
            env, spec, state, (item.fields, _view(state.cur, item.fields), item.want, item.label)
        )
        for item in picks.rejects
    )
    return _first_bad((*writes, *rejects))


def _abbr_taken(env: Env, spec: CaseSpec, state: _State) -> str:
    """改成别的客户已用的简称：409 abbrname_taken，行不变。没有别的客户有简称时跳过。"""
    try:
        other = _sql(env, spec, other_abbr_sql(state.code))
    except ValueError:
        other = ""
    now = field_view(state.cur)["abbrname"]
    if other in ("", "-") or other.casefold() == now.casefold():
        return ""
    return _reject(env, spec, state, ({"abbrname": other}, {"abbrname": now}, (409, "abbrname_taken"), "简称与别的客户重复"))


def _first_bad(steps: tuple[Callable[[], str], ...]) -> str:
    for run in steps:
        bad = run()
        if bad:
            return bad
    return ""


def _early_date(env: Env, spec: CaseSpec, state: _State) -> str:
    if not state.created:
        return ""
    plan = ({"end_date": "1900-01-01"}, {"end_date": field_view(state.cur)["end_date"]}, (400, ""), "早于建档日的停用日期")
    return _reject(env, spec, state, plan)


def _missing(env: Env) -> str:
    bad, _payload = _post(env, fields_body(_MISSING, {"abbrname": "E2E"}), (404, ""), "不存在的客户")
    return bad


def _cycle(env: Env, spec: CaseSpec, state: _State) -> str:
    return _first_bad((
        lambda: _load(env, spec, state),
        lambda: _pick_protocol(env, spec, state),
        lambda: _pick_district(env, spec, state),
        lambda: _pick_more(env, spec, state),
        lambda: _dry_run(env, spec, state),
        lambda: _apply(env, spec, state, _plan("改简称", "abbrname", state.new_abbr)),
        lambda: _noop(env, spec, state),
        lambda: _apply(env, spec, state, _plan("设地区", "district", state.district)),
        lambda: _apply(env, spec, state, _plan("地区改回", "district", state.orig["district"])),
        lambda: _apply(env, spec, state, _plan("设停用日期", "end_date", state.new_date)),
        lambda: _apply(env, spec, state, _plan("清停用日期", "end_date", "")),
        lambda: _protocol_steps(env, spec, state),
        lambda: _name_tax_steps(env, spec, state),
        lambda: _more_steps(env, spec, state),
        lambda: _rejects(env, spec, state),
        lambda: _abbr_taken(env, spec, state),
        lambda: _early_date(env, spec, state),
        lambda: _missing(env),
    ))


def _restore(env: Env, spec: CaseSpec, state: _State) -> str:
    """用路由把当前值改回快照里的原值，再核对整行（审计列除外）与快照一致。"""
    if not state.snap or not state.dirty:
        return ""
    view = field_view(_row(env, spec, state.code))
    diff = [name for name in _COLUMN if view[name] != state.orig[name]]
    if diff:
        body = fields_body(state.code, {n: state.orig[n] for n in diff}, {n: view[n] for n in diff})
        bad, _payload = _post(env, body, _OK, "还原客户字段")
        if bad:
            return bad
    hits = changed(state.snap, _row(env, spec, state.code), state.volatile)
    return f"还原后仍有变化 {hits}"[:200] if hits else ""


def _drop_district(env: Env, spec: CaseSpec, state: _State) -> str:
    code = state.made_district
    if not code:
        return ""
    status, _payload = send(env, "delete", {"archive": "district_class", "code": code, "idempotency_key": new_key("cusfields")})
    if status != 200:
        run_guarded(env, district_delete_sql(code), spec.name)  # 只读账套只放行 create：测试账套上用 SQL 删
    left = _sql(env, spec, district_count_sql(code))
    return "" if left == "0" else f"本用例新建的地区分类 {code} 没删掉"


def _guarded(step: Callable[[], str]) -> str:
    try:
        return step()
    except (RuntimeError, ValueError) as exc:
        return str(exc)[:200]


def _undo(env: Env, spec: CaseSpec, state: _State) -> str:
    items = (_guarded(lambda: _restore(env, spec, state)), _guarded(lambda: _drop_district(env, spec, state)))
    return "；".join(item for item in items if item)


def _verdict(problem: str, undo: str, note: str, state: _State) -> tuple[str, str]:
    if problem or undo:
        return "FAIL", "；".join(item for item in (undo, problem) if item)[:240]
    if note:
        return "N/A", note
    notes = (state.protocol_note, "" if state.orig.get("name") else "客户名称为空，跳过名称步骤", *state.picks.notes)
    skipped = "".join(f"；{item}" for item in notes if item)
    return "PASS", f"customer {state.code} fields{skipped}"[:240]


def step_cusfields(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    try:
        state = _State(code=safe_code(td("archives.customer.existing")))
    except (DataMissing, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    note = ""
    try:
        problem = _cycle(env, spec, state)
    except _NotApplicable as exc:
        problem, note = "", str(exc)
    except (RuntimeError, ValueError) as exc:
        problem = str(exc)[:200]
    return _verdict(problem, _undo(env, spec, state), note, state)


CUSFIELDS_HANDLERS = {"core_cusfields": step_cusfields}


def cusfields_cases() -> list[CaseSpec]:
    step = Step("core_cusfields", id_from="none")
    title = "客户简称、地区、停用日期、收付款协议、名称、税号、联系人、开户银行、所属行业、自定义项按白名单只改请求的列并还原"
    tags = frozenset({"arc:customer", "arc:district_class", "arc:write"})
    return [CaseSpec(_NAME, title, (), (step,), True, data=_DATA, tags=tags, routes=_ROUTES)]
