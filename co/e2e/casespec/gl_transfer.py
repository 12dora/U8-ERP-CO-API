"""期间损益结转、自定义转账（gl/transfer/pnl、gl/transfer/custom，第二级写入）的核对。

都在 U8 客户端做过结转的测试账套上跑：U8 自己做的结转凭证当标准答案。
gl-transfer-pnl-parity：挑不晚于今年、本期凭证全部记账的最近一个有 U8 期间损益凭证的期间，读出这些凭证的分录 →
  以该期间最后一天为登录日期预演 gl/transfer/pnl（exclude_existing：余额里去掉已生成的结转凭证）→ 桥算出的分录
  （科目、部门、人员、客户、供应商、项目、借方、贷方）与 U8 的逐行比对（多重集合，金额为 0 的行不计）。
  桥 403 test_account_only / feature_disabled（账套不在 testAccounts 里或总开关未打开）时改跑不经桥的 SQL 口径（gl_transfer_sql.pnl_oracle_sql），
  一致记 N/A（说明里写明桥未核对），不一致 FAIL。
gl-transfer-custom-parity：同样挑有 U8 自定义转账凭证的期间，预演 gl/transfer/custom（全部定义、exclude_existing），
  按摘要把桥算出的凭证与 U8 的对上，逐行比对；U8 有、桥没算出的 FAIL（说明带桥的跳过原因）；
  桥算出、U8 当期没做的定义只记在说明里。403 记 N/A。用到按年取数（QM(…,年,…)）的定义桥不生成（口径未经实证），
  挑期间和比对都去掉这些定义的凭证；U8 做过的自定义转账全是这类时记 N/A。
gl-transfer-pnl-regen：今年总账未结账、U8 期间损益凭证未记账未审核、本期再没有别的未记账凭证的期间 →
  先同上预演比对 → 作废、删除 U8 的期间损益凭证 → 经桥正式生成 → 新凭证的分录与原凭证逐行一致，标记照 U8
  （coutsign、idoc=-1、coutno_id GL+13 位、coutsysname 为空、未记账）→ 再生成 409。不补偿，留给快照还原。
  挑不到、403、U8 不许作废他人凭证（第一张作废被拒）记 N/A。
gl-transfer-test-only：非测试账套上预演本月期间损益结转应 403 test_account_only 或 feature_disabled；本账套是测试账套（不是 403）时 N/A。
步骤 kind 为 "custom"，GL_TRANSFER_OPS 并入 ops_run。
"""

from __future__ import annotations

import calendar
from collections import Counter
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from co.e2e.casespec import gl_transfer_sql as q
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.gl_voucher_sql import gl_key
from co.e2e.casespec.tier2 import TEST_ONLY, tier2_refused

PNL = "/v1/gl/transfer/pnl"
CUSTOM = "/v1/gl/transfer/custom"
VOID = "/v1/gl/vouchers/void"
DELETE = "/v1/gl/vouchers/delete"
_LINE_KEYS = ("dept", "person", "customer", "supplier", "item")
Line = tuple[str, ...]


def last_day(year: int, period: int) -> str:
    return f"{year:04d}-{period:02d}-{calendar.monthrange(year, period)[1]:02d}"


def _money(value: object) -> str:
    try:
        return str(Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01")))
    except InvalidOperation as exc:
        raise _Fail(f"金额无法识别：{value}") from exc


def bridge_line(raw: dict) -> Line:
    """桥预演的一行分录 → 比较键。"""
    aux = tuple(str(raw.get(key) or "") for key in _LINE_KEYS)
    return (str(raw.get("account") or ""), *aux, _money(raw.get("debit")), _money(raw.get("credit")))


def u8_line(cell: str) -> Line:
    """lines_sql 的一行 → 比较键。"""
    parts = cell.split("|")
    if len(parts) != 8:
        raise _Fail("凭证分录回读不对：" + cell[:60])
    return (*parts[:6], _money(parts[6]), _money(parts[7]))


def _nonzero(lines: list[Line]) -> list[Line]:
    return [line for line in lines if Decimal(line[-2]) != 0 or Decimal(line[-1]) != 0]


def diff_lines(u8: list[Line], bridge: list[Line]) -> str:
    """两边分录的多重集合差；一致返回空串。"""
    left, right = Counter(_nonzero(u8)), Counter(_nonzero(bridge))
    if left == right:
        return ""
    gone = list((left - right).elements())
    extra = list((right - left).elements())
    head = f"U8 {sum(left.values())} 行、桥 {sum(right.values())} 行"
    return f"{head}；U8 有桥没有 {len(gone)} 行 {['|'.join(x) for x in gone[:2]]}；桥多 {len(extra)} 行 {['|'.join(x) for x in extra[:2]]}"


def bridge_vouchers(payload: dict) -> list[dict]:
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    transfer = detail.get("transfer") if isinstance(detail.get("transfer"), dict) else {}
    found = transfer.get("vouchers")
    if not isinstance(found, list):
        raise _Fail("预演响应没有 detail.transfer.vouchers")
    return [item for item in found if isinstance(item, dict)]


def _lines_of(voucher: dict) -> list[Line]:
    raw = voucher.get("lines")
    return [bridge_line(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _skipped(payload: dict) -> str:
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    transfer = detail.get("transfer") if isinstance(detail.get("transfer"), dict) else {}
    items = transfer.get("skipped") if isinstance(transfer.get("skipped"), list) else []
    return "；".join(f"{item.get('tran_id', '')} {item.get('reason', '')}" for item in items[:3] if isinstance(item, dict))


def _dry(run: _Run, route: str, year: int, period: int, login: str) -> tuple[int, dict]:
    body = {"fiscal_year": year, "period": period, "dry_run": True, "exclude_existing": True, "date": login}
    return run.send(route, body)


def _month(run: _Run, kind: str) -> tuple[int, int]:
    row = run.pick(q.month_sql(run.env.today[:4], kind), q.MONTH_COLS)
    if row is None:
        raise _NotApplicable(f"没有本期凭证全部记账、又有 U8「{kind}」结转凭证的期间")
    return int(row[0]), int(row[1])


def _u8_vouchers(run: _Run, year: int, period: int, kind: str) -> list[list[str]]:
    rows = [cell.split("|") for cell in run.cells(q.vouchers_sql(year, period, kind))]
    if not rows or any(len(row) != 7 for row in rows):
        raise _Fail(f"{year}年{period}期 U8 结转凭证回读不对")
    return rows


def _u8_lines(run: _Run, year: int, period: int, rows: list[list[str]]) -> list[Line]:
    lines: list[Line] = []
    for row in rows:
        lines += [u8_line(cell) for cell in run.cells(q.lines_sql(year, gl_key(period, row[0], row[1])))]
    return lines


def _refused(status: int, payload: dict, what: str) -> None:
    if tier2_refused(status, payload):
        raise _NotApplicable(f"桥没开第二级写入或账套不在 testAccounts 里（403 {payload.get('code')}），{what}未经桥核对")
    if status != 200 or payload.get("dry_run") is not True:
        raise _Fail(f"预演{what}：{status} {payload.get('code')} {payload.get('message')}"[:240])


def _oracle(run: _Run, year: int, period: int) -> None:
    cell = run.cell(q.pnl_oracle_sql(year, period))
    parts = cell.split("|", 2)
    if len(parts) != 3:
        raise _Fail("SQL 口径回读不对：" + cell[:60])
    total, bad, first = parts
    if bad != "0":
        raise _Fail(f"SQL 口径与 U8 {year}年{period}期期间损益凭证不一致 {bad}/{total} 项，首项 {first}"[:240])
    raise _NotApplicable(
        f"桥没开第二级写入或账套不在 testAccounts 里（403），桥未核对；不经桥的 SQL 口径与 U8 {year}年{period}期期间损益凭证 {total} 项一致"
    )


def pnl_parity_body(run: _Run) -> tuple[str, str]:
    year, period = _month(run, q.PNL_SIGN)
    rows = _u8_vouchers(run, year, period, q.PNL_SIGN)
    u8 = _u8_lines(run, year, period, rows)
    status, payload = _dry(run, PNL, year, period, last_day(year, period))
    if tier2_refused(status, payload):
        _oracle(run, year, period)
    _refused(status, payload, "期间损益结转")
    made = bridge_vouchers(payload)
    bridge = [line for voucher in made for line in _lines_of(voucher)]
    problem = diff_lines(u8, bridge)
    if problem:
        raise _Fail(f"{year}年{period}期期间损益分录不一致：{problem}"[:240])
    return "PASS", f"{year}年{period}期：桥算出 {len(made)} 张 {len(bridge)} 行，与 U8 的 {len(rows)} 张期间损益凭证逐行一致"


def _custom_month(run: _Run) -> tuple[int, int]:
    """挑有不按年取数的 U8 自定义转账凭证的期间；只有按年取数的记 N/A。"""
    row = run.pick(q.month_sql(run.env.today[:4], q.CUSTOM_SIGN, skip_year=True), q.MONTH_COLS)
    if row is not None:
        return int(row[0]), int(row[1])
    _month(run, q.CUSTOM_SIGN)
    raise _NotApplicable("U8 生成过的自定义转账都用到按年取数（QM(…,年,…)），桥暂不支持（请在 U8 客户端生成），未核对")


def custom_parity_body(run: _Run) -> tuple[str, str]:
    year, period = _custom_month(run)
    yearly = set(run.cells(q.year_digests_sql()))
    rows = [row for row in _u8_vouchers(run, year, period, q.CUSTOM_SIGN) if row[2] not in yearly]
    if not rows:
        raise _NotApplicable(f"{year}年{period}期 U8 自定义转账凭证都属于按年取数的定义，桥暂不支持，未核对")
    status, payload = _dry(run, CUSTOM, year, period, last_day(year, period))
    _refused(status, payload, "自定义转账")
    made = {str(voucher.get("digest") or ""): voucher for voucher in bridge_vouchers(payload)}
    checked = []
    for digest in sorted({row[2] for row in rows}):
        voucher = made.get(digest)
        if voucher is None:
            raise _Fail(f"{year}年{period}期 U8 有自定义转账「{digest}」，桥没算出（跳过：{_skipped(payload)}）"[:240])
        mine = [row for row in rows if row[2] == digest]
        problem = diff_lines(_u8_lines(run, year, period, mine), _lines_of(voucher))
        if problem:
            raise _Fail(f"{year}年{period}期自定义转账「{digest}」分录不一致：{problem}"[:240])
        checked.append(digest)
    extra = sorted(set(made) - set(checked))
    tail = f"；桥另算出 U8 当期没做的 {extra[:3]}" if extra else ""
    tail += f"；按年取数的定义 {len(yearly)} 个未核对" if yearly else ""
    return "PASS", f"{year}年{period}期：{len(checked)} 个自定义转账与 U8 凭证逐行一致{tail}"[:240]


def _drop_u8(run: _Run, period: int, rows: list[list[str]]) -> None:
    for index, row in enumerate(rows):
        ident = {"period": period, "sign": row[0], "no": int(row[1])}
        status, payload = run.send(VOID, ident)
        if status == 409 and index == 0:
            raise _NotApplicable(f"作废 U8 期间损益凭证 {row[0]}-{row[1]} 被拒：{payload.get('message')}"[:240])
        if status != 200:
            raise _Fail(f"作废 {row[0]}-{row[1]}：{status} {payload.get('code')} {payload.get('message')}（留给快照还原）"[:240])
        run.ok(DELETE, ident, f"删除 {row[0]}-{row[1]}")


def _check_made(run: _Run, year: int, payload: dict) -> list[Line]:
    raw = payload.get("vouchers")
    if not isinstance(raw, list) or not raw:
        raise _Fail("生成的响应没有 vouchers")
    lines: list[Line] = []
    for item in raw:
        key = gl_key(item.get("period"), item.get("sign"), item.get("no"))
        marks = run.cell(q.marks_sql(year, key)).split("|")
        n = marks[0] if marks else "0"
        if n == "0" or marks != [n, n, n, n, n, "1", n]:
            raise _Fail(f"{key.sign}-{key.no} 的标记不像 U8 的结转凭证：{'|'.join(marks)}")
        lines += [u8_line(cell) for cell in run.cells(q.lines_sql(year, key))]
    return lines


def regen_body(run: _Run) -> tuple[str, str]:
    year = int(run.env.today[:4])
    row = run.pick(q.regen_month_sql(year), 2)
    if row is None:
        raise _NotApplicable("今年没有总账未结账、U8 期间损益凭证未记账未审核、又没有别的未记账凭证的期间")
    period = int(row[0])
    rows = _u8_vouchers(run, year, period, q.PNL_SIGN)
    u8 = _u8_lines(run, year, period, rows)
    status, payload = _dry(run, PNL, year, period, run.env.today)
    _refused(status, payload, "期间损益结转")
    problem = diff_lines(u8, [line for voucher in bridge_vouchers(payload) for line in _lines_of(voucher)])
    if problem:
        raise _Fail(f"{year}年{period}期预演与 U8 不一致（未改账套）：{problem}"[:240])
    _drop_u8(run, period, rows)
    made = run.ok(PNL, {"fiscal_year": year, "period": period}, "经桥生成期间损益结转")
    problem = diff_lines(u8, _check_made(run, year, made))
    if problem:
        raise _Fail(f"重新生成的凭证与原凭证不一致（留给快照还原）：{problem}"[:240])
    status, again = run.send(PNL, {"fiscal_year": year, "period": period})
    if (status, again.get("code")) != (409, "state_mismatch"):
        raise _Fail(f"再生成应 409 state_mismatch，实际 {status} {again.get('code')}（留给快照还原）")
    return "PASS", f"{year}年{period}期：删掉 U8 的 {len(rows)} 张期间损益凭证后经桥重新生成，分录与标记一致，再生成 409"


def test_only_body(run: _Run) -> tuple[str, str]:
    year, period = int(run.env.today[:4]), int(run.env.today[5:7])
    status, payload = run.send(PNL, {"fiscal_year": year, "period": period, "dry_run": True})
    if tier2_refused(status, payload):
        return "PASS", f"预演期间损益结转 403 {payload.get('code')}（第二级写入只对测试账套开放）"
    raise _NotApplicable(f"本账套是测试账套（预演 {status} {payload.get('code')}），不核对第二级闸门")


def _op(body: Callable[[_Run], tuple[str, str]]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def run(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
        return _guard(env, spec, body)

    return run


GL_TRANSFER_OPS = {
    "gl_transfer_pnl_parity": _op(pnl_parity_body),
    "gl_transfer_custom_parity": _op(custom_parity_body),
    "gl_transfer_pnl_regen": _op(regen_body),
    "gl_transfer_test_only": _op(test_only_body),
}


def gl_transfer_cases() -> list[CaseSpec]:
    # 预演读科目总账、辅助总账：与总账写入互斥（gl）。重新生成另作废删除凭证、取新凭证号、读结账状态（new:gl:转、period:close）。
    read = frozenset(("gl",))
    write = frozenset(("gl", "new:gl:转", "period:close"))
    specs = (
        ("gl-transfer-pnl-parity", "gl_transfer_pnl_parity", "期间损益结转预演（去掉已生成的）与 U8 的期间损益凭证逐行一致", read,
         (PNL,)),
        ("gl-transfer-custom-parity", "gl_transfer_custom_parity", "自定义转账预演（去掉已生成的）与 U8 的凭证按摘要逐行一致", read,
         (CUSTOM,)),
        ("gl-transfer-pnl-regen", "gl_transfer_pnl_regen", "删掉 U8 未记账的期间损益凭证后经桥重新生成，分录、标记一致，再生成 409",
         write, (PNL, VOID, DELETE)),
        ("gl-transfer-test-only", "gl_transfer_test_only", "非测试账套上期间损益结转预演 403 test_account_only / feature_disabled", read, (PNL,)),
    )
    return [
        CaseSpec(name, title, (), (Step("custom", sql_name=op, id_from="none"),), True, optional=True,
                 tags=tags, routes=routes)
        for name, op, title, tags, routes in specs
    ]
