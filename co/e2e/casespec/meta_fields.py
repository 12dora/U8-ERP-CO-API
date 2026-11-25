"""字段标签（meta/fields）用例。只读，不改账套。标签文字来自账套模板，只核对结构，不写死任何文字。

meta-fields-voucher：销售订单新建的字段 = meta kinds[].writable.create 展开后的 head / lines（含 cdefine、cfree 区间），
  不多不少；每行结构对（name、label、type、required、enum）、至少一个字段有标签、meta 的必填都标 required；
  有 enum 类型的字段时取值非空。未知类型 400 field=type；meta 里不支持修改的类型带 op=update 400 field=op。
meta-fields-archive：客户档案的字段 = meta archives[].writable，至少一个有标签；未知档案 400 field=archive。
meta-fields-gl：总账凭证的字段 = meta gl 的 head、line、cash_flow，全部有标签（固定标签表），meta 的必填都标 required。
"""

from __future__ import annotations

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import status_problem
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.meta_batch_judge import (
    archive_writable,
    expand_side,
    fields_problem,
    gl_names,
    gl_required,
    kind_writable,
    labelled,
    rows_of,
    unsupported_op,
)

ROUTE = "/v1/meta/fields"
_META = "/v1/meta"
_KIND = "sale_order"
_ARCHIVE = "customer"


def _meta(env: Env) -> dict:
    try:
        payload = env.client.meta()
    except U8CoError as exc:
        raise RuntimeError(f"meta {exc.status} {exc.code}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("meta 没有返回 ok")
    return payload


def _ask(env: Env, body: dict) -> dict:
    status, payload = send(env, ROUTE, body)
    if status != 200:
        raise ValueError(f"meta/fields {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _refused(env: Env, body: dict, field: str) -> str:
    status, payload = send(env, ROUTE, body)
    bad = status_problem(status, payload, 400, "bad_request")
    if bad:
        return f"{body} 期望 400：{bad}"[:200]
    return "" if payload.get("field") == field else f"{body} field={payload.get('field')!r}，期望 {field}"


def _side_problem(payload: dict, spec: dict, side: str) -> str:
    required = {str(name).lower() for name in (spec.get("required") or {}).get(side) or []}
    return fields_problem(payload, expand_side(spec.get(side)), required, (side,))


def voucher(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    spec = kind_writable(_meta(env), _KIND)
    if not spec:
        return "FAIL", "meta 里没有销售订单新建的可写字段"
    payload = _ask(env, {"type": _KIND})
    if (payload.get("type"), payload.get("op")) != (_KIND, "create"):
        return "FAIL", f"type={payload.get('type')} op={payload.get('op')}，期望 {_KIND} create"
    vt_id = payload.get("vt_id")  # 找不到模板时为 null（那样所有标签都是 null，下面「至少一个有标签」会报）
    if vt_id is not None and (not isinstance(vt_id, int) or isinstance(vt_id, bool) or vt_id <= 0):
        return "FAIL", f"vt_id={vt_id!r} 不是正整数"
    problem = _side_problem(payload, spec, "head") or _side_problem(payload, spec, "lines")
    if problem:
        return "FAIL", problem[:240]
    rows = rows_of(payload, ("head", "lines"))
    if labelled(rows) == 0:
        return "FAIL", "没有一个字段有标签（模板行没对上）"
    enums = sum(1 for row in rows if row.get("type") == "enum")
    return "PASS", f"{len(rows)} 个字段，{labelled(rows)} 个有标签，{enums} 个枚举"


def voucher_refused(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    problem = _refused(env, {"type": "no_such_kind"}, "type")
    kind, op = unsupported_op(_meta(env))
    if not problem and kind:
        problem = _refused(env, {"type": kind, "op": op}, "op")
    if problem:
        return "FAIL", problem
    return "PASS", "400 type" + (f"，{kind} op={op} 400 op" if kind else "（没有不支持修改的类型，op 未测）")


def archive(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    want = archive_writable(_meta(env), _ARCHIVE)
    if not want:
        return "FAIL", "meta 里没有客户档案的可写标签"
    payload = _ask(env, {"archive": _ARCHIVE})
    if payload.get("archive") != _ARCHIVE:
        return "FAIL", f"archive={payload.get('archive')}，期望 {_ARCHIVE}"
    problem = fields_problem(payload, want, set())
    rows = rows_of(payload)
    if not problem and labelled(rows) == 0:
        problem = "没有一个标签对上字段字典"
    problem = problem or _refused(env, {"archive": "no_such_archive"}, "archive")
    return ("FAIL", problem[:240]) if problem else ("PASS", f"{len(rows)} 个标签，{labelled(rows)} 个有标签")


def gl(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    meta = _meta(env)
    want = gl_names(meta)
    if not want:
        return "FAIL", "meta 里没有总账凭证字段"
    payload = _ask(env, {"gl": True})
    problem = fields_problem(payload, want, gl_required(meta))
    rows = rows_of(payload)
    if not problem and labelled(rows) != len(rows):
        problem = f"总账凭证字段有 {len(rows) - labelled(rows)} 个没有标签"
    return ("FAIL", problem[:240]) if problem else ("PASS", f"{len(rows)} 个字段")


MB_META_OPS = {
    "mb_mf_voucher": voucher,
    "mb_mf_refused": voucher_refused,
    "mb_mf_archive": archive,
    "mb_mf_gl": gl,
}


def _case(name: str, title: str, ops: tuple[str, ...], tag: str) -> CaseSpec:
    steps = tuple(Step("custom", ROUTE, sql_name=op, id_from="none") for op in ops)
    return CaseSpec(name, title, (), steps, True, tags=frozenset((tag,)), routes=(ROUTE, _META))


def meta_fields_cases() -> list[CaseSpec]:
    return [
        _case("meta-fields-voucher", "字段标签：销售订单新建的字段与 meta 可写字段一致，结构、必填、枚举对，未知类型和不支持的操作 400",
              ("mb_mf_voucher", "mb_mf_refused"), "opt:voucher_template"),
        _case("meta-fields-archive", "字段标签：客户档案的字段与 meta 可写标签一致，有标签，未知档案 400", ("mb_mf_archive",),
              "arc-read:" + _ARCHIVE),
        _case("meta-fields-gl", "字段标签：总账凭证字段与 meta 一致、都有标签、必填标 required", ("mb_mf_gl",), "meta"),
    ]
