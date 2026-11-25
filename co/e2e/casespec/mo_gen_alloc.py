"""生产订单参照销售订单生成（ATO，mo_gen_ato / mo_gen_bad）的纯函数：固定数据、请求正文、U8 的子件展开算法和需求跟踪键。

子件展开照 U8 生成生产订单时的两条路（Usp_MO_GenMoFromSo 511–515、772–791）：
- 销售订单行没有选配（iCusBomId = 0，固定数据里是普通 ATO 件那一行）→ Usp_MO_GenAllocate 按标准物料清单展开：第一层只取不选配的子件
  （coalesce(OptionalFlag, 0) = 0，过程 209、267 行）；虚拟件（WIPType 4）再往下展开它的物料清单，同样只取不选配的子件
  （572、653 行），虚拟件本身不进子件表（773 行 WIPType <> 4）。（ATO 模型件的行必须选配才能审核，走不到这条路；
  算法对模型 = 必选子件 + 选项类也成立：选项类下的选项都选配，一个也不带。）
- 有选配 → Usp_BO_CustBomComponentIns 读 bom_cbomcomponent：pSequence = 0 的行、WIPType 不是 4 的进子件表
  （直接供应 WIPType 5 的下层这里不处理，固定数据没有）。
用量 = 订单行数量 × 分子 / 分母（固定数据的子件都是变动用量、没有损耗率）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.testdata import DataMissing, value

KIND = "production_order"
SOURCE = "sale_order"
WIP_VIRTUAL = "4"
_EPS = Decimal("0.000001")
_DEPTH = 8
_FIELDS = ("so_id", "line_plain", "line_cbom", "cbom_id", "model", "std", "option", "base", "mo_type", "dept")
ATO_KEYS = tuple("ato." + name for name in _FIELDS)


@dataclass(frozen=True)
class AtoFixture:
    so: int
    plain: int
    cbom_line: int
    cbom: int
    model: str
    std: str
    option: str
    base: str
    mo_type: str
    dept: str


@dataclass(frozen=True)
class BomRow:
    parent: str
    comp: str
    wip: str
    optional: bool
    qty_n: Decimal
    qty_d: Decimal


def load_fixture() -> AtoFixture | None:
    """测试数据里的 ato.*（造数步骤 ato 写的）；缺任何一个返回 None（用例记 N/A）。"""
    try:
        raw = {name: value("ato." + name) for name in _FIELDS}
    except DataMissing:
        return None
    ids = [int(safe_id(raw[name])) for name in _FIELDS[:4]]
    codes = [safe_lot(raw[name]) for name in _FIELDS[4:]]
    return AtoFixture(*ids, *codes)


def dec(text: str) -> Decimal:
    try:
        return Decimal((text or "").strip())
    except InvalidOperation as exc:
        raise ValueError("回读不是数：" + (text or "空")) from exc


def gen_body(fx: AtoFixture, lines: list[tuple[int, object]], dry: bool = False, so: int = 0) -> dict:
    """vouchers/generate 正文：全部行生成一张生产订单，每行带生产部门。"""
    rows = [{"source_line_id": line, "quantity": qty, "dept_code": fx.dept} for line, qty in lines]
    body: dict = {"type": KIND, "source_type": SOURCE, "id": so or fx.so, "head": {"mo_type": fx.mo_type}, "lines": rows}
    if dry:
        body["dry_run"] = True
    return body


def parse_bom(cells: list[str]) -> list[BomRow]:
    """std_bom_sql 的行：母件|子件|WIPType|选配|分子|分母。"""
    rows = []
    for cell in cells:
        parts = cell.split("|")
        if len(parts) != 6:
            raise ValueError("物料清单回读格式不对：" + cell[:80])
        rows.append(BomRow(parts[0], parts[1], parts[2], parts[3] == "1", dec(parts[4]), dec(parts[5])))
    return rows


def _add(out: dict[str, Decimal], code: str, qty: Decimal) -> None:
    out[code] = out.get(code, Decimal(0)) + qty


def _walk(rows: list[BomRow], parent: str, qty: Decimal, out: dict[str, Decimal], depth: int) -> None:
    if depth > _DEPTH:
        raise ValueError("物料清单层数过多：" + parent)
    for row in rows:
        if row.parent != parent or row.optional:
            continue
        need = qty * row.qty_n / row.qty_d
        if row.wip == WIP_VIRTUAL:
            _walk(rows, row.comp, need, out, depth + 1)
        else:
            _add(out, row.comp, need)


def std_explosion(rows: list[BomRow], root: str, qty: Decimal) -> dict[str, Decimal]:
    """没有选配时 U8 按标准物料清单展开出的子件：{存货: 数量}。"""
    out: dict[str, Decimal] = {}
    _walk(rows, root, qty, out, 0)
    return out


def cbom_explosion(cells: list[str], qty: Decimal) -> dict[str, Decimal]:
    """有选配时的子件。cells 是 cbom_rows_sql 的行：子件|WIPType|分子|分母|pSequence。"""
    out: dict[str, Decimal] = {}
    for cell in cells:
        parts = cell.split("|")
        if len(parts) != 5:
            raise ValueError("选配回读格式不对：" + cell[:80])
        if parts[4] != "0" or parts[1] == WIP_VIRTUAL:
            continue
        _add(out, parts[0], qty * dec(parts[2]) / dec(parts[3]))
    return out


def alloc_problem(want: dict[str, Decimal], got: dict[str, Decimal]) -> str:
    """子件的存货和数量是否与算法一致；一致返回空串。"""
    if sorted(want) != sorted(got):
        return f"子件应为 {_brief(want)}，实际 {_brief(got)}"
    bad = [code for code in want if abs(want[code] - got[code]) > _EPS]
    return f"子件数量应为 {_brief(want)}，实际 {_brief(got)}" if bad else ""


def _brief(items: dict[str, Decimal]) -> str:
    return "、".join(f"{code}×{qty.normalize()}" for code, qty in sorted(items.items())) or "（无）"


def so_keys(view: dict[str, str], cbom: bool) -> tuple[str, str, str, str]:
    """生产订单行的 SoType|SoDId|SoCode|SoSeq：有选配时是这一行销售订单（1、iSOsID、单号、行号），
    否则照销售订单行的需求跟踪方式（view 取自 V_SA_GenOrdData_ForMO，与 Usp_MO_GenMoFromSo 的写法相同）。"""
    if cbom:
        return "1", view["did"], view["code"], view["seq"]
    return view["so_type"], view["so_did"], view["so_code"], view["so_seq"]
