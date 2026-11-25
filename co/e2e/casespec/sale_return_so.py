"""退货单参照销售订单（只挂订单行，不挂发货行）。

步骤 kind 为 "custom"，sql_name 是操作名（SALE_RETURN_SO_OPS 并入 ops_run）。
挑已审核、未关闭的普通销售订单里累计发货数量（iFHQuantity，已扣过以前的退货）>= 1 的行，
退 1 个：新单是红字、行挂订单行（iSOsID）、不挂发货行（iCorID=0）、数量 −1；订单行 fretquantity +1、iFHQuantity −1；
删除后两者回到原值。回读先记下，删除之后再判，断言失败也不会把退货单留在库里。挑不到记 N/A。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.srcless import _drop, _made_id, _pick, _send
from co.e2e.casespec.srcless_sql import _PLAIN_INV
from co.e2e.casespec.sqlexec import run_guarded

_GEN = "/v1/vouchers/generate"
_LOAD = "/v1/vouchers/load"
_DELETE = "/v1/vouchers/delete"
MEMO = "U8CO-SRSO"
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _code(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) {_SAFE} AND LTRIM(RTRIM(ISNULL({col}, N''))) <> N''"


def pick_sql() -> str:
    # 订单 ID|订单行 iSOsID|仓库（非货位仓，优先存货默认仓库：订单行没有仓库列，参照视图按存货默认仓库带出）。
    # 存货无批次、保质期、自由项、浮动换算。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), h.ID) + N'|' + CONVERT(varchar(20), d.iSOsID) + N'|'
  + LTRIM(RTRIM(w.cWhCode)) AS v
FROM dbo.SO_SOMain AS h
JOIN dbo.SO_SODetails AS d ON d.ID = h.ID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
CROSS APPLY (
  SELECT TOP (1) x.cWhCode FROM dbo.Warehouse AS x
  WHERE ISNULL(x.bWhPos, 0) = 0 AND {_code("x.cWhCode")}
  ORDER BY CASE WHEN x.cWhCode = i.cDefWareHouse THEN 0 ELSE 1 END, x.cWhCode
) AS w
WHERE ISNULL(h.cVerifier, N'') <> N'' AND ISNULL(h.cCloser, N'') = N'' AND ISNULL(d.cSCloser, N'') = N''
  AND h.cBusType = N'普通销售' AND ISNULL(h.cexch_name, N'人民币') = N'人民币' AND d.iQuantity > 0
  AND ISNULL(d.iFHQuantity, 0) >= 1
  AND {_PLAIN_INV}
ORDER BY h.ID DESC, d.iSOsID;
"""


def so_qty_sql(line: int) -> str:
    # 订单行 fretquantity|iFHQuantity。
    return (
        "SET NOCOUNT ON; SELECT N'ROW:' + CONVERT(varchar(40), CONVERT(decimal(28, 6), ISNULL(d.fretquantity, 0))) + N'|'"
        " + CONVERT(varchar(40), CONVERT(decimal(28, 6), ISNULL(d.iFHQuantity, 0))) AS v"
        f" FROM dbo.SO_SODetails AS d WHERE d.iSOsID = {int(line)};"
    )


def shape_sql(made: int, line: int) -> str:
    # 新单的行数（红字、挂该订单行、不挂发货行、数量 −1）。
    return (
        "SET NOCOUNT ON; SELECT N'ROW:' + CONVERT(varchar(20), COUNT(*)) AS v FROM dbo.DispatchLists AS d"
        " JOIN dbo.DispatchList AS h ON h.DLID = d.DLID"
        f" WHERE h.DLID = {int(made)} AND h.bReturnFlag = 1 AND d.iSOsID = {int(line)}"
        " AND ISNULL(d.iCorID, 0) = 0 AND d.iQuantity = -1;"
    )


def _so_qty(env: Env, spec: CaseSpec, line: int) -> tuple[Decimal, Decimal]:
    cells = _pick(env, spec, so_qty_sql(line))
    try:
        return Decimal(cells[0]), Decimal(cells[1])
    except (IndexError, InvalidOperation) as exc:
        raise ValueError("订单行回读 " + "|".join(cells)) from exc


def _body(cells: list[str], today: str) -> dict:
    lines = [{"source_line_id": int(cells[1]), "quantity": 1, "cwhcode": cells[2]}]
    head = {"ddate": today, "cmemo": MEMO}
    return {"type": "sale_return", "id": int(cells[0]), "source_type": "sale_order", "head": head, "lines": lines}


def _judge(before: tuple, after: tuple, back: tuple, shape: str) -> str:
    if shape != "1":
        return f"新单行形态不符（红字、挂订单行、iCorID=0、数量 −1 的行数 {shape or '空'}）"
    if after[0] != before[0] + 1:
        return f"订单行 fretquantity {before[0]} → {after[0]}，期望 +1"
    if after[1] != before[1] - 1:
        return f"订单行 iFHQuantity {before[1]} → {after[1]}，期望 −1"
    return "" if back == before else f"删除后订单行 {back}，期望 {before}"


def sale_return_from_so(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    cells = _pick(env, spec, pick_sql())
    if not cells:
        return "N/A", "挑不到累计发货数量 >= 1 的已审核销售订单行"
    line = int(cells[1])
    before = _so_qty(env, spec, line)
    status, payload = _send(env, _GEN, _body(cells, env.today))
    made = _made_id(payload) if status == 200 else 0
    if not made:
        return "FAIL", f"生单 {status} {payload.get('code')} {payload.get('message')}"[:240]
    try:
        after = _so_qty(env, spec, line)
        shape = first_cell(run_guarded(env, shape_sql(made, line), spec.name))
        loaded, _payload = _send(env, _LOAD, {"type": "sale_return", "id": made})
    finally:
        gone = _drop(env, "sale_return", made)
    back = _so_qty(env, spec, line)
    problem = ("" if loaded == 200 else f"新建后读取 {loaded}") or _judge(before, after, back, shape) or gone
    return ("FAIL", problem) if problem else ("PASS", f"订单行 {line} 退 1 并删除")


SALE_RETURN_SO_OPS = {"sale_return_from_so": sale_return_from_so}


def sale_return_so_cases() -> list[CaseSpec]:
    step = Step("custom", _GEN, sql_name="sale_return_from_so", id_from="none")
    tags = frozenset(("new:sale_return", "new:dispatch", "w:sale_return", "w:sale_order"))
    title = "销售订单生成退货单（只挂订单行）：订单行 fretquantity +1、iFHQuantity −1，删除后回退"
    return [
        CaseSpec(
            "sale-return-from-so", title, (), (step,), True, optional=True, tags=tags, routes=(_GEN, _LOAD, _DELETE)
        )
    ]
