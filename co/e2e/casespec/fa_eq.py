"""固定资产卡片新增、撤销本期新增（U8 官方 EAI capitalasserts），另有设备台账与拒绝路径（fa_eq_more）。
变动单不在 API 里：已装的 U8 EAI 没有 capitalvouchers 的导入样式表，变动单、资产减少在 U8 客户端做。

全部在快照内，只动本用例自建的卡片，从不碰账套原有的卡片和设备。账套没启用固定资产、当前期间已结账、挑不到参照卡片时 N/A。
登录日期一律取固定资产当前期间的可写日期（AccInformation FA dWritableDate，桥要求登录日期在固定资产当前未结账期间）；
开始使用日期取该期间的上个月 1 日（EAI 只导入原始卡片，开始使用日期要早于当前期间）。
类别、增加方式、使用状况、折旧方法、使用部门照抄最新一张在役单部门卡片（末级类别、末级部门），币种不传（本位币）。

fa-card-cycle：固定资产当前期间已计提折旧时 N/A（U8 不许撤销已计提期间的新增，桥 409）；否则预演新增（validate，最大 sCardID 不变、资产编号仍不存在）→ 新增（响应 asset_num 是送的资产编号、card_id 比原最大值大）
  → archives/get 按返回的卡片编号读回（资产编号一致、录入日期在当前期间）→ archives/delete（撤销本期新增）→ 读取 404。
  中途失败按返回的卡片编号删除；新增没返回 200 但卡片已落库（例如 504）时按资产编号查到卡片编号再删。
fa-card-period-refuse：登录日期取同年度已结账的最近一期的 1 日新增卡片 → 409，最大 sCardID 不变、没有这个资产编号。
  年度里没有更早的已结账期间时 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（FA_EQ_OPS 并入 ops_run）；SQL 在 fa_eq_sql。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.dry_judge import Want, preview_problem
from co.e2e.casespec.fa_eq_more import MORE_OPS, more_cases
from co.e2e.casespec.fa_eq_sql import fa_depr_sql, fa_mark_sql, fa_ref_sql, fa_state_sql

_CREATE = "/v1/archives/create"
_GET = "/v1/archives/get"
_DELETE = "/v1/archives/delete"
CARD = "fa_card"
_NAME = "E2E回归卡片"
_VALUE = 100


@dataclass(frozen=True)
class _Fa:
    period: int  # 固定资产当前期间
    day: str  # 当前期间的可写日期（登录日期）
    closed: int  # 同年度里更早的已结账期间，没有为 0


def _digits(text: str) -> int:
    return int(text) if text.isdigit() else 0


def _fa_state(run: _Run) -> _Fa:
    parts = run.pick(fa_state_sql(), 4)
    if parts is None or not parts[0] or not parts[1]:
        raise _NotApplicable("账套没有启用固定资产")
    period, day = _digits(parts[0]), parts[1]
    if parts[2] != "0":
        raise _NotApplicable(f"固定资产第 {period} 期已结账或 GL_mend 没有这一期")
    # 账套库年度（请求的 year，如 2024）可以早于固定资产的业务年度（如 2026），不比较；只核对可写日期的月份就是当前期间，
    # 登录日期取可写日期（年份即固定资产当前年度，桥 FaPeriod 同样按登录日期的年月判断）。
    if len(day) < 10 or _digits(day[:4]) == 0 or _digits(day[5:7]) != period:
        raise _NotApplicable(f"固定资产可写日期 {day} 与当前期间 {period} 不一致")
    return _Fa(period, day, _digits(parts[3]))


def _month_before(day: str) -> str:
    first = date.fromisoformat(day).replace(day=1)
    return (first - timedelta(days=1)).replace(day=1).isoformat()


def _fields(run: _Run, day: str) -> dict:
    """新卡片的 fields：照抄参照卡片，开始使用日期取 day 所在月的上个月 1 日。"""
    parts = run.pick(fa_ref_sql(), 6)
    if parts is None:
        raise _NotApplicable("挑不到在役、单部门、末级类别和末级部门的参照卡片")
    type_code, origin, status, method, dept, life = parts
    fields = {
        "name": _NAME, "type_code": type_code, "original_value": _VALUE, "start_date": _month_before(day),
        "origin_code": origin, "status_code": status, "depreciation_method_code": method, "dept_code": dept,
    }
    if _digits(life) > 0:
        fields["useful_life_months"] = _digits(life)
    return fields


def _asset(lead: str) -> str:
    return lead + datetime.now().strftime("%d%H%M%S")


def _mark(run: _Run, asset: str) -> tuple[int, int, str]:
    parts = run.pick(fa_mark_sql(asset), 3)
    if parts is None:
        raise _Fail("回读卡片表失败")
    return _digits(parts[0]), _digits(parts[1]), parts[2]


def _key(code: str, day: str) -> dict:
    return {"archive": CARD, "code": code, "date": day}


def _dry_card(run: _Run, body: dict, before: tuple[int, int, str]) -> None:
    status, payload = run.send(_CREATE, dict(body, dry_run=True))
    if status != 200:
        raise _Fail(f"预演新增卡片：{status} {payload.get('code')} {payload.get('message')}"[:240])
    problem = preview_problem(payload, Want("validate", "any", CARD, 0, True))
    if problem:
        raise _Fail("预演新增卡片：" + problem)
    if _mark(run, body["code"])[:2] != before[:2]:
        raise _Fail("预演新增卡片之后最大 sCardID 或资产编号行数变了")


def _create_card(run: _Run, fa: _Fa, lead: str) -> tuple[str, str]:
    """预演、新增一张卡片并登记撤销；返回（卡片编号, 资产编号）。"""
    fields = _fields(run, fa.day)
    asset = _asset(lead)
    before = _mark(run, asset)
    if before[1] != 0:
        raise _NotApplicable(f"资产编号 {asset} 已存在")
    body = {"archive": CARD, "code": asset, "fields": fields, "date": fa.day}
    _dry_card(run, body, before)
    status, payload = run.send(_CREATE, body)
    if status != 200:
        left = _mark(run, asset)[2]
        if left:
            run.undo.append((_DELETE, _key(left, fa.day), "撤销卡片"))
        raise _Fail(f"新增卡片：{status} {payload.get('code')} {payload.get('message')}"[:240])
    code = str(payload.get("code") or "")
    if code:
        run.undo.append((_DELETE, _key(code, fa.day), "撤销卡片"))
    card_id = payload.get("card_id")
    if not code or payload.get("asset_num") != asset or not isinstance(card_id, int) or card_id <= before[0]:
        raise _Fail(f"新增卡片响应不对：code={code!r} asset_num={payload.get('asset_num')!r} card_id={card_id!r}")
    return code, asset


def _check_card(run: _Run, fa: _Fa, code: str, asset: str) -> None:
    got = run.ok(_GET, _key(code, fa.day), "读取新卡片")
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    if fields.get("asset_num") != asset or fields.get("name") != _NAME:
        raise _Fail(f"读回的资产编号或名称不对：{fields.get('asset_num')!r} {fields.get('name')!r}")
    if not str(fields.get("entry_date") or "").startswith(fa.day[:7]):
        raise _Fail(f"录入日期 {fields.get('entry_date')!r} 不在固定资产当前期间 {fa.day[:7]}")


def _card_cycle(run: _Run) -> tuple[str, str]:
    fa = _fa_state(run)
    if run.cell(fa_depr_sql(_digits(fa.day[:4]), fa.period)) not in ("", "0"):
        raise _NotApplicable(f"固定资产当前期间已计提折旧，不能演示撤销新增（{fa.day[:4]} 年第 {fa.period} 期）")
    code, asset = _create_card(run, fa, "91")
    _check_card(run, fa, code, asset)
    run.settle(1)  # 撤销本期新增
    status, payload = run.send(_GET, _key(code, fa.day))
    if status != 404:
        raise _Fail(f"撤销后读取卡片应 404，实际 {status} {payload.get('code')}")
    if _mark(run, asset)[1] != 0:
        raise _Fail(f"撤销后资产编号 {asset} 仍在卡片表里")
    return "PASS", f"卡片 {code}（资产编号 {asset}，第 {fa.period} 期）：预演、新增、读取、撤销、读取 404"


def _period_refuse(run: _Run) -> tuple[str, str]:
    fa = _fa_state(run)
    if not fa.closed:
        raise _NotApplicable(f"固定资产第 {fa.period} 期之前本年度没有已结账期间")
    day = f"{fa.day[:4]}-{fa.closed:02d}-01"
    asset = _asset("93")
    before = _mark(run, asset)
    body = {"archive": CARD, "code": asset, "fields": _fields(run, day), "date": day}
    status, payload = run.send(_CREATE, body)
    if status == 200:
        run.undo.append((_DELETE, _key(str(payload.get("code") or ""), fa.day), "撤销卡片"))
        raise _Fail(f"登录日期 {day}（已结账）新增卡片应 409，实际 200")
    if _mark(run, asset)[:2] != before[:2]:
        raise _Fail("被拒之后最大 sCardID 或资产编号行数变了")
    if status != 409:
        raise _Fail(f"登录日期 {day}（已结账）新增卡片应 409，实际 {status} {payload.get('code')}")
    return "PASS", f"登录日期 {day}（第 {fa.closed} 期已结账）新增卡片 409 {payload.get('code')}，卡片表不变"


def _op(body):
    def run_op(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
        return _guard(env, spec, body)

    return run_op


FA_EQ_OPS = {
    "fa_card_cycle": _op(_card_cycle),
    "fa_period_refuse": _op(_period_refuse),
    **MORE_OPS,
}


def _case(name: str, title: str, op: str, routes: tuple[str, ...], tags: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, optional=True, tags=frozenset(tags), routes=routes)


def fa_eq_cases() -> list[CaseSpec]:
    # 卡片写都带 arc:fa_card（同表最大 sCardID 断言，彼此互斥，也让 archive-read-fa-card 避开）和 arc:write（桥的全局档案写键）。
    arc = ("arc:" + CARD, "arc:write")
    return [
        _case("fa-card-cycle", "固定资产卡片新增（EAI）：预演、新增、读取、撤销本期新增、读取 404", "fa_card_cycle",
              (_CREATE, _GET, _DELETE), arc),
        _case("fa-card-period-refuse", "固定资产卡片新增：登录日期在已结账期间 409，卡片表不变", "fa_period_refuse",
              (_CREATE, _DELETE), arc),
        *more_cases(),
    ]
