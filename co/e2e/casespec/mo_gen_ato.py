"""生产订单参照销售订单生成（ATO）：mo-generate-ato。固定在 seed 通道，用造数步骤 ato 的固定数据（测试数据 ato.*）。

1. 核对固定数据：两行都在 U8 的生产订单生成列表（V_SA_GenOrdData_ForMO）里，第 1 行（普通 ATO 件 ato.std）
   可生单 >= 3、没有选配，第 2 行（ATO 模型 ato.model）可生单 >= 1、选配主键等于 ato.cbom_id。记下最大 MoId 和两行的已下达数量（iMoQuantity）。
2. 预演（两行 2、1 个）→ 200 dry_run / validate，最大 MoId、MoDId 和两行已下达数量都不变。
3. 生成 → 只新增这一张生产订单；响应 lines 2、details 按请求顺序（source_line_id、cust_bom false / true），
   allocates 等于子件行数；订单行 OrdFlag 1、OrderType 1、OrderDId 是销售订单行、CustBomId 0 / ato.cbom_id、
   需求跟踪键按 Usp_MO_GenMoFromSo（mo_gen_alloc.so_keys）、存货、数量、部门、类别；子件按 U8 的展开算法
   （mo_gen_alloc：第 1 行按普通 ATO 件的标准物料清单，只有必选子件；第 2 行是选配的子件）；两行已下达数量各加 2、1。
4. 第 1 行再生成「剩余 + 1」个：账套不许超计划下达（mom_parameter.RlsOverPlanFlag = 0）时应 409「超过可生单数量」；
   允许（1 或没有设置）时 U8 和桥都放行，只发预演，应 200 validate。两种都核对最大 MoId、MoDId 和已下达数量不变。
   回归不改账套参数。
5. 删除生产订单（vouchers/delete，要 200），记下（不断言）U8 是否把已下达数量退回；其余留给快照还原。
步骤 kind 为 "custom"，sql_name 是操作名（MO_GEN_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.mo_gen_alloc import (
    KIND,
    AtoFixture,
    alloc_problem,
    cbom_explosion,
    dec,
    gen_body,
    load_fixture,
    parse_bom,
    so_keys,
    std_explosion,
)
from co.e2e.casespec.mo_gen_ato_sql import (
    MO_COLS,
    alloc_sql,
    cbom_rows_sql,
    gen_rows_sql,
    imo_sql,
    max_modid_sql,
    over_plan_sql,
    mo_rows_sql,
    new_mo_sql,
    std_bom_sql,
)
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.mo_probe_sql import max_mo_sql

GEN = "/v1/vouchers/generate"
DELETE = "/v1/vouchers/delete"
PLAIN_QTY = 2
CBOM_QTY = 1
_VIEW_KEYS = ("did", "left", "moq", "cbom", "so_type", "so_did", "so_code", "so_seq", "code", "seq")
NO_FIXTURE = "测试数据没有 ato.*（造数步骤 ato 只在 seed 通道建）"


def fixture() -> AtoFixture:
    fx = load_fixture()
    if fx is None:
        raise _NotApplicable(NO_FIXTURE)
    return fx


def wanted(fx: AtoFixture) -> list[tuple[int, int, bool]]:
    """请求的两行：（销售订单行，数量，是否选配）。"""
    return [(fx.plain, PLAIN_QTY, False), (fx.cbom_line, CBOM_QTY, True)]


def _views(run: _Run, fx: AtoFixture) -> dict[int, dict[str, str]]:
    out = {}
    for cell in run.cells(gen_rows_sql(fx.so)):
        parts = cell.split("|")
        if len(parts) != len(_VIEW_KEYS):
            raise _Fail("生产订单生成列表回读格式不对：" + cell[:80])
        out[int(parts[0])] = dict(zip(_VIEW_KEYS, parts, strict=True))
    return out


def ready_problem(fx: AtoFixture, views: dict[int, dict[str, str]]) -> str:
    """固定数据是否还能跑这条用例；能跑返回空串。"""
    missing = [line for line in (fx.plain, fx.cbom_line) if line not in views]
    if missing:
        return f"U8 生产订单生成列表里没有销售订单 {fx.so} 的行 {missing}（重跑造数步骤 ato）"
    if dec(views[fx.plain]["left"]) < PLAIN_QTY + 1 or dec(views[fx.cbom_line]["left"]) < CBOM_QTY:
        return f"固定数据的可生单数量不够（{views[fx.plain]['left']} / {views[fx.cbom_line]['left']}），基线被用掉了"
    # 没有选配的行 iCusBomID 是 NULL（SQL 已 ISNULL 成 0，空串也按 0）。
    if dec(views[fx.plain]["cbom"] or "0") != 0 or dec(views[fx.cbom_line]["cbom"] or "0") != fx.cbom:
        return f"选配主键不符：第 1 行 {views[fx.plain]['cbom']}，第 2 行 {views[fx.cbom_line]['cbom']}，测试数据 {fx.cbom}"
    return ""


def _imo(run: _Run, fx: AtoFixture) -> dict[int, Decimal]:
    out = {}
    for cell in run.cells(imo_sql([fx.plain, fx.cbom_line])):
        line, qty = (cell.split("|") + [""])[:2]
        out[int(line)] = dec(qty)
    if len(out) != 2:
        raise _Fail("回读不到两行销售订单的已下达数量")
    return out


def _marks(run: _Run) -> tuple[str, str]:
    return run.cell(max_mo_sql()), run.cell(max_modid_sql())


def _still(run: _Run, fx: AtoFixture, imo: dict[int, Decimal], marks: tuple[str, str], what: str) -> None:
    """预演、被拒之后：没有新的生产订单和订单行，两行已下达数量不变。"""
    now = _marks(run)
    if now != marks:
        raise _Fail(f"{what}后最大 MoId / MoDId 从 {marks} 变成 {now}")
    after = _imo(run, fx)
    if after != imo:
        raise _Fail(f"{what}后已下达数量从 {imo} 变成 {after}")


def _dry(run: _Run, fx: AtoFixture) -> None:
    body = gen_body(fx, [(line, qty) for line, qty, _cb in wanted(fx)], dry=True)
    status, payload = run.send(GEN, body)
    if status != 200 or payload.get("dry_run") is not True or payload.get("mode") != "validate":
        got = f"{status} {payload.get('code')} {payload.get('mode')} {payload.get('message')}"
        raise _Fail(("预演应 200 dry_run/validate，实际 " + got)[:240])


def details_problem(payload: dict, fx: AtoFixture) -> str:
    """生成响应的 lines、details（按请求顺序的来源行、cust_bom）。"""
    details = payload.get("details")
    if payload.get("lines") != 2 or not isinstance(details, list) or len(details) != 2:
        return f"响应应 lines 2、details 2 行，实际 {payload.get('lines')} / {details!r}"[:200]
    want = [(line, cbom) for line, _qty, cbom in wanted(fx)]
    got = [(row.get("source_line_id"), row.get("cust_bom")) for row in details if isinstance(row, dict)]
    return "" if got == want else f"details 的来源行 / cust_bom 应为 {want}，实际 {got}"


def _generate(run: _Run, fx: AtoFixture, mark: str) -> tuple[int, dict]:
    payload = run.ok(GEN, gen_body(fx, [(line, qty) for line, qty, _cb in wanted(fx)]), "生成生产订单")
    mo = payload.get("id")
    if type(mo) is not int or mo <= 0:
        raise _Fail("生成生产订单：响应没有新单据 id")
    run.drop(KIND, mo)
    fresh = run.cells(new_mo_sql(mark))
    if fresh != [str(mo)]:
        raise _Fail(f"应只新增生产订单 {mo}，实际 {fresh}")
    problem = details_problem(payload, fx)
    if problem:
        raise _Fail(problem)
    return mo, payload


def row_problem(row: dict[str, str], fx: AtoFixture, view: dict[str, str], want: tuple[int, int, bool]) -> str:
    """一行生产订单与请求、U8 写法（Usp_MO_GenMoFromSo）不符的地方；相符返回空串。"""
    line, qty, cbom = want
    expect = {"orderdid": str(line), "order_type": "1", "ord_flag": "1", "cbom": str(fx.cbom if cbom else 0),
              "inv": fx.model if cbom else fx.std, "dept": fx.dept, "mo_type": fx.mo_type}
    bad = [f"{key} {row.get(key)!r}（应 {value}）" for key, value in expect.items() if row.get(key) != value]
    if dec(row.get("qty", "")) != qty:
        bad.append(f"数量 {row.get('qty')}（应 {qty}）")
    got = tuple(row.get(key, "") for key in ("so_type", "so_did", "so_code", "so_seq"))
    if got != so_keys(view, cbom):
        bad.append(f"需求跟踪 {got}（应 {so_keys(view, cbom)}）")
    return f"第 {row.get('seq')} 行：" + "，".join(bad) if bad else ""


def _rows(run: _Run, fx: AtoFixture, mo: int, views: dict[int, dict[str, str]]) -> list[dict[str, str]]:
    rows = []
    for cell in run.cells(mo_rows_sql(mo)):
        parts = cell.split("|")
        if len(parts) != len(MO_COLS):
            raise _Fail("生产订单回读格式不对：" + cell[:80])
        rows.append(dict(zip(MO_COLS, parts, strict=True)))
    if len(rows) != 2:
        raise _Fail(f"生产订单 {mo} 应有 2 行，实际 {len(rows)}")
    for row, want in zip(rows, wanted(fx), strict=True):
        problem = row_problem(row, fx, views[want[0]], want)
        if problem:
            raise _Fail(problem)
    return rows


def expected_allocs(run: _Run, fx: AtoFixture) -> list[dict[str, Decimal]]:
    """两行按 U8 算法应展开的子件；固定数据不对（第 1 行不只必选子件、第 2 行不是必选子件 + 选项）时 FAIL。"""
    plain = std_explosion(parse_bom(run.cells(std_bom_sql(fx.std))), fx.std, Decimal(PLAIN_QTY))
    chosen = cbom_explosion(run.cells(cbom_rows_sql(fx.cbom)), Decimal(CBOM_QTY))
    if sorted(plain) != [fx.base] or sorted(chosen) != sorted((fx.base, fx.option)):
        raise _Fail(f"固定数据的物料清单 / 选配不对：标准展开 {sorted(plain)}，选配 {sorted(chosen)}")
    return [plain, chosen]


def _allocs(run: _Run, fx: AtoFixture, mo: int, rows: list[dict[str, str]], payload: dict) -> None:
    got: dict[str, dict[str, Decimal]] = {}
    count = 0
    for cell in run.cells(alloc_sql(mo)):
        modid, inv, qty = (cell.split("|") + ["", ""])[:3]
        bucket = got.setdefault(modid, {})
        bucket[inv] = bucket.get(inv, Decimal(0)) + dec(qty)
        count += 1
    for row, want in zip(rows, expected_allocs(run, fx), strict=True):
        problem = alloc_problem(want, got.get(row["modid"], {}))
        if problem:
            raise _Fail(f"第 {row['seq']} 行" + problem)
    if payload.get("allocates") != count:
        raise _Fail(f"响应 allocates {payload.get('allocates')}，库里子件 {count} 行")


def _moved(before: dict[int, Decimal], after: dict[int, Decimal], fx: AtoFixture) -> None:
    for line, qty, _cbom in wanted(fx):
        if after[line] - before[line] != qty:
            raise _Fail(f"销售订单行 {line} 已下达数量 {before[line]} → {after[line]}，应加 {qty}")


def over_problem(allowed: bool, status: int, payload: dict) -> str:
    """超量请求的判定：允许超计划下达时是预演，应 200 validate；不允许时应 409「超过可生单数量」。"""
    if allowed:
        if status == 200 and payload.get("dry_run") is True and payload.get("mode") == "validate":
            return ""
        return f"允许超计划下达，超量预演应 200 validate，实际 {status} {payload.get('mode')} {payload.get('message')}"[:200]
    message = str(payload.get("message") or "")
    if status == 409 and "超过可生单数量" in message:
        return ""
    return f"不许超计划下达，超量应 409「超过可生单数量」，实际 {status} {message[:80]}"


def _over(run: _Run, fx: AtoFixture, left: Decimal) -> tuple[Decimal, str]:
    """返回（超量数量, 说明）。允许超计划下达时只发预演，从不真生成。"""
    rest = left - PLAIN_QTY
    over = rest.to_integral_value(rounding=ROUND_FLOOR) + 1
    allowed = run.cell(over_plan_sql()) != "0"
    status, payload = run.send(GEN, gen_body(fx, [(fx.plain, int(over))], dry=allowed))
    if status == 200 and type(payload.get("id")) is int:
        run.drop(KIND, payload["id"])
    problem = over_problem(allowed, status, payload)
    if problem:
        raise _Fail(f"第 1 行生成 {over} 个（剩 {rest.normalize()}）：{problem}")
    return over, ("允许超计划下达，预演 200" if allowed else "409")


def _flow(run: _Run) -> tuple[str, str]:
    fx = fixture()
    views = _views(run, fx)
    problem = ready_problem(fx, views)
    if problem:
        raise _Fail(problem)
    before, marks = _imo(run, fx), _marks(run)
    _dry(run, fx)
    _still(run, fx, before, marks, "预演")
    mo, payload = _generate(run, fx, marks[0])
    rows = _rows(run, fx, mo, views)
    _allocs(run, fx, mo, rows, payload)
    moved = _imo(run, fx)
    _moved(before, moved, fx)
    made = _marks(run)
    over, how = _over(run, fx, dec(views[fx.plain]["left"]))
    _still(run, fx, moved, made, "超量请求")
    run.settle(1)
    after = _imo(run, fx)
    back = "已退回" if after == before else "未退回"
    note = (
        f"生产订单 {mo}（{payload.get('code')}）2 行、子件 {payload.get('allocates')} 行，超量 {over} 个 {how}；"
        f"删除后已下达数量 {after[fx.plain]} / {after[fx.cbom_line]}"
        f"（生成前 {before[fx.plain]} / {before[fx.cbom_line]}，U8 {back}）"
    )
    return "PASS", note


def mo_generate_ato(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _flow)
