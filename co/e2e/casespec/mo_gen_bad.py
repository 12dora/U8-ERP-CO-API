"""生产订单参照销售订单生成（ATO）的拒绝路径 mo-generate-bad，以及两条用例的登记（mo_gen_cases、MO_GEN_OPS）。

mo-generate-bad 用 ato.* 的销售订单（没有时 N/A），逐条发错误请求，每条都不能建出单据：
- 登录前 400（只要固定数据）：缺 head.mo_type、行里有未知字段、来源行重复、来源行不在这张销售订单（别的订单的一行，
  桥 400「明细行不存在」）；
- 另一张已审核销售订单里非 ATO 模型的未关闭行 → 409「不是 ATO 模型」；
- 未审核的销售订单（ATO 模型的行优先）→ 409「销售订单未审核」。桥先查表头审核（MoGenSql.CheckHead），再逐行查存货，
  所以挑到的行不是 ATO 模型也只认审核这条消息。
后两条各自挑不到数据时只略过那一条（说明里写明），其余照跑。最后最大 MoId、MoDId 不变。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.mo_gen_alloc import KIND, AtoFixture, gen_body
from co.e2e.casespec.mo_gen_ato import DELETE, GEN, _marks, fixture, mo_generate_ato
from co.e2e.casespec.mo_gen_ato_sql import open_so_sql, other_line_sql, plain_line_sql
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _Run

# 别的订单上一行都没有时（空账套不会出现）用一个不存在的行号，桥同样 400「明细行不存在」。
NO_LINE = 2147483647

Bad = tuple[str, dict, int, tuple[str, ...]]


def offline_bodies(fx: AtoFixture, foreign: int) -> list[Bad]:
    """登录前（及来源行核对）的四条 400：（说明, 正文, 期望状态码, 消息里应含的任一段）。foreign 是别的订单上的 iSOsID。"""
    no_type = gen_body(fx, [(fx.plain, 1)])
    no_type["head"] = {}
    unknown = gen_body(fx, [(fx.plain, 1)])
    unknown["lines"][0]["cfree1"] = "x"
    return [
        ("缺 mo_type", no_type, 400, ("缺少字段 mo_type",)),
        ("未知字段", unknown, 400, ("不能设置字段 cfree1",)),
        ("来源行重复", gen_body(fx, [(fx.plain, 1), (fx.plain, 1)]), 400, ("来源明细重复",)),
        ("来源行不在订单上", gen_body(fx, [(foreign, 1)]), 400, ("明细行不存在",)),
    ]


def refusal_bodies(fx: AtoFixture, plain: tuple[int, int] | None, unaudited: tuple[int, int] | None) -> tuple[list[Bad], list[str]]:
    """两条 409（各自挑不到数据就略过）和略过的说明。plain、unaudited 是（销售订单 ID, iSOsID）。"""
    bads: list[Bad] = []
    skipped = []
    if plain:
        bads.append(("非 ATO 模型", gen_body(fx, [(plain[1], 1)], so=plain[0]), 409, ("不是 ATO 模型",)))
    else:
        skipped.append("非 ATO 模型：挑不到别的已审核订单里非 ATO 模型的行，略过")
    if unaudited:
        bads.append(("未审核", gen_body(fx, [(unaudited[1], 1)], so=unaudited[0]), 409, ("销售订单未审核",)))
    else:
        skipped.append("未审核：挑不到未审核的销售订单，略过")
    return bads, skipped


def _pair(run: _Run, query: str) -> tuple[int, int] | None:
    parts = run.pick(query, 2)
    return (int(parts[0]), int(parts[1])) if parts else None


def _one(run: _Run, bad: Bad) -> str:
    label, body, status, texts = bad
    got, payload = run.send(GEN, body)
    if got == 200 and type(payload.get("id")) is int:
        run.drop(KIND, payload["id"])
    message = str(payload.get("message") or "")
    if got != status or not any(text in message for text in texts):
        raise _Fail(f"{label}：应 {status}「{texts[0]}」，实际 {got} {payload.get('code')} {message[:80]}")
    return f"{label} {got}"


def _flow(run: _Run) -> tuple[str, str]:
    fx = fixture()
    marks = _marks(run)
    foreign = run.cell(other_line_sql(fx.so))
    seen = [_one(run, bad) for bad in offline_bodies(fx, int(foreign) if foreign.isdigit() else NO_LINE)]
    bads, skipped = refusal_bodies(fx, _pair(run, plain_line_sql(fx.so)), _pair(run, open_so_sql()))
    seen += [_one(run, bad) for bad in bads]
    if _marks(run) != marks:
        raise _Fail("被拒的请求后最大 MoId / MoDId 变了")
    return "PASS", "；".join(seen + skipped)


def mo_generate_bad(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _flow)


MO_GEN_OPS = {"mo_gen_ato": mo_generate_ato, "mo_gen_bad": mo_generate_bad}

# 按 BOM 展开新建生产订单（bom），回写销售订单已下达数量（w:sale_order）；两条用例共用 ato 固定数据，不同时跑。
_TAGS = frozenset(("bom", "new:production_order", "w:production_order", "w:sale_order", "need:ato_fixture"))


def _case(name: str, title: str, op: str, routes: tuple[str, ...], observe: bool) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=_TAGS, routes=routes, observe=observe)


def mo_gen_cases() -> list[CaseSpec]:
    return [
        _case("mo-generate-ato",
              "生产订单参照销售订单生成（ATO）：预演不写库，两行生成一张订单（不选配按标准物料清单、选配按选配结果展开子件），"
              "回写已下达数量，超量 409，随即删除",
              "mo_gen_ato", (GEN, DELETE), True),
        _case("mo-generate-bad",
              "生产订单参照销售订单生成的拒绝路径：缺类别、未知字段、来源行重复或不在订单上 400，非 ATO 模型、未审核 409",
              "mo_gen_bad", (GEN,), False),
    ]
