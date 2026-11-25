"""ATO 固定数据（steps_docs_ato.py）的查询和销售订单行选配结果的 SQL。全是纯函数。

选配结果按 U8 生成生产订单时实际读的最少几行写（Usp_MO_GenMoFromSo → Usp_BO_CustBomComponentIns）：
bom_cbom 一行（模型的 PartId），bom_cbomcomponent 每个选中的末级子件一行（pSequence = 0、WIPType 3，
Sequence 从 1 起；U8 按 max(Sequence) 给子件分号），再把 SO_SODetails.iCusBomID 指向它、busecusbom = 1。
选配界面用的树（bom_cbomparent / bom_cbomcomp / bom_cbomcompopt）生成生产订单时不读，不写。

主键用 U8 自己的 sp_GetID 取（计数在 UFSystem..UA_Identity，会话 SQL 碰不到，也不许 EXEC）：经 comprobe 探针
登录 U8，在 U8 自己的连接上执行 id_sql（远程号照 UFU8MBomISrv：AccInformation AA.cID，空为 00；账套号取自
库名）。U8 的选配保存（UFU8MBomISrv.clsBom.ATOCBomSave）按表名 bom_cbom、bom_cbomcomponent 取号，但父、子计数
各给哪张表没有实测，所以两张表各取 n 个，再把落后的那个子计数补齐，子件主键取两边都已发出的最高 n 个：
不管 U8 之后从哪个计数给子件分号，都不会撞上这次写的行。拿到主键后再用会话 SQL 写行（cbom_sql）。
标识符都是常量；拼进 SQL 的只有正整数主键（safe_id）和经 quoted() 检查过的编码。
"""

from __future__ import annotations

from dataclasses import dataclass

from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedError
from co.e2e.seed.steps_docs_base import marker, safe_id

CBOM_KEY = "ato"
CBOM_CODE = "SEEDATO1"
_FREE = " AND ".join(f"ISNULL(Free{n}, N'') = N''" for n in range(1, 11))

PART_SQL = "SELECT TOP (1) PartId AS id FROM bas_part WHERE InvCode = {code} AND " + _FREE + " ORDER BY PartId"
BOM_OF_SQL = (
    "SELECT b.BomId AS id FROM bom_bom AS b JOIN bom_parent AS p ON p.BomId = b.BomId"
    " JOIN bas_part AS bp ON bp.PartId = p.ParentId"
    " WHERE b.BomType = 1 AND bp.InvCode = {code} ORDER BY b.BomId"
)
# 物料清单里某个子件的 OpComponentId（选配子件行带回它，U8 按它找替代料）。
OPCOMP_SQL = (
    "SELECT TOP (1) c.OpComponentId AS id FROM bom_opcomponent AS c JOIN bas_part AS bp ON bp.PartId = c.ComponentId"
    " WHERE c.BomId = {bom} AND bp.InvCode = {code} ORDER BY c.SortSeq"
)
# 物料清单里打了选配标志的子件行数（选配标志真的写进了 bom_opcomponentopt）。
OPTIONAL_SQL = (
    "SELECT COUNT(*) AS n FROM bom_opcomponent AS c JOIN bom_opcomponentopt AS o ON o.OptionsId = c.OptionsId"
    " WHERE c.BomId = {bom} AND ISNULL(o.OptionalFlag, 0) = 1"
)
CBOM_OF_LINE_SQL = (
    "SELECT ISNULL(d.iCusBomID, 0) AS id, (SELECT COUNT(*) FROM bom_cbomcomponent AS c"
    " WHERE c.CustBomId = ISNULL(d.iCusBomID, 0)) AS n FROM SO_SODetails AS d WHERE d.iSOsID = {line}"
)


@dataclass(frozen=True)
class Stamp:
    # 选配表头的建立人、建立日期（ctx.operator、ctx.day）。
    user: str
    day: str


@dataclass(frozen=True)
class CbomIds:
    # sp_GetID 发出的选配表头主键和子件主键（已含 1000000000 起点）。
    head: int
    comps: tuple[int, ...]


@dataclass(frozen=True)
class CbomPart:
    part_id: int
    op_component_id: int
    wh: str


def _component_rows(parts: list[CbomPart], comps: tuple[int, ...]) -> str:
    rows = []
    for n, (part, comp) in enumerate(zip(parts, comps, strict=True), start=1):
        rows.append(
            f"(@cbom, {safe_id(comp)}, {n * 10}, N'0000', {safe_id(part.part_id)}, 1, 1, 1, 0, 0, 1, 0, 3,"
            f" {quoted([part.wh])}, 0, 1, {safe_id(part.op_component_id)}, 1, 0, {n}, 1, 1, 0, 0, 0)"
        )
    return ",\n        ".join(rows)


ID_PROBE_SUB = "SA"


def id_sql(count: int) -> str:
    """在 U8 登录的连接上用 sp_GetID 给 bom_cbom、bom_cbomcomponent 各取 count 个号，补齐落后的子计数，返回一行。"""
    if count < 1 or count > 100:
        raise SeedError("选配子件数不合法：" + str(count))
    get = "EXEC @{r} = sp_GetID @rid, @acc, N'{table}', {n}, @{f} OUTPUT, @{c} OUTPUT;"
    return "\n".join((
        "SET NOCOUNT ON;",
        "DECLARE @acc nvarchar(3), @rid nvarchar(2), @r1 int, @r2 int, @r3 int, @head int, @c1 int, @f2 int, @c2 int,",
        "    @x int, @gap int;",
        "SELECT @acc = SUBSTRING(DB_NAME(), 8, 3), @r1 = -9, @r2 = -9, @r3 = 0;",
        "SELECT @rid = LTRIM(RTRIM(ISNULL(MAX(cValue), N''))) FROM AccInformation WHERE cSysID = N'AA' AND cName = N'cID';",
        "IF @rid = N'' SET @rid = N'00';",
        "IF DB_NAME() LIKE N'UFDATA[_][0-9][0-9][0-9][_]%'",
        "BEGIN",
        "    " + get.format(r="r1", table="bom_cbom", n=count, f="head", c="c1"),
        "    " + get.format(r="r2", table="bom_cbomcomponent", n=count, f="f2", c="c2"),
        "    SET @gap = ABS(@c1 - @c2);",
        "    IF @gap > 0 AND @c1 < @c2 " + get.format(r="r3", table="bom_cbom", n="@gap", f="x", c="c1"),
        "    IF @gap > 0 AND @c2 < @c1 " + get.format(r="r3", table="bom_cbomcomponent", n="@gap", f="x", c="c2"),
        "END",
        "SELECT @r1 AS r1, @r2 AS r2, @r3 AS r3, @head AS head, @c1 AS c1, @c2 AS c2;",
    ))


def id_steps(count: int) -> list[dict]:
    return [{"op": "login", "sub": ID_PROBE_SUB}, {"op": "sql", "query": id_sql(count), "max": 1}]


def parse_ids(lines: list[dict], count: int) -> CbomIds:
    """探针输出里 sql 那一步的一行：三次调用都返回 0、两个子计数相等时，表头取 head，子件取最高的 count 个。"""
    rows = [row for line in lines if line.get("op") == "sql" for row in (line.get("rows") or [])]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise SeedError("sp_GetID 没有返回一行：" + str(rows)[:200])
    row = {key.lower(): str(value if value is not None else "").strip() for key, value in rows[0].items()}
    if (row.get("r1"), row.get("r2"), row.get("r3")) != ("0", "0", "0") or row.get("c1") != row.get("c2"):
        raise SeedError("sp_GetID 取号失败：" + str(row)[:200])
    top = int(safe_id(row.get("c1", "")))
    comps = tuple(range(top - count + 1, top + 1))
    return CbomIds(int(safe_id(row.get("head", ""))), tuple(int(safe_id(comp)) for comp in comps))


def repair_sql(line: int) -> str:
    """上次半途留下的状态：行指着一张没有子件的选配就清掉指向；没有子件、也没有行指着的造数选配表头删掉。"""
    line_id = safe_id(line)
    mark = quoted([marker(CBOM_KEY)])
    return (
        f"UPDATE SO_SODetails SET iCusBomID = NULL, busecusbom = 0 WHERE iSOsID = {line_id}"
        " AND ISNULL(iCusBomID, 0) <> 0"
        " AND NOT EXISTS (SELECT 1 FROM bom_cbomcomponent AS c WHERE c.CustBomId = SO_SODetails.iCusBomID);\n"
        f"DELETE FROM bom_cbom WHERE Description = {mark}"
        " AND NOT EXISTS (SELECT 1 FROM bom_cbomcomponent AS c WHERE c.CustBomId = bom_cbom.CustBomId)"
        " AND NOT EXISTS (SELECT 1 FROM SO_SODetails AS d WHERE d.iCusBomID = bom_cbom.CustBomId);"
    )


def cbom_sql(line: int, model_part: int, parts: list[CbomPart], ids: CbomIds, stamp: Stamp) -> str:
    """销售订单行 line 还没有选配结果时，一个事务里用 sp_GetID 发出的主键写 bom_cbom、bom_cbomcomponent，行指向新选配。"""
    if not parts or len(parts) != len(ids.comps):
        raise ValueError("选配子件和主键数不符")
    line_id = safe_id(line)
    cbom = safe_id(ids.head)
    created = f"CONVERT(datetime, {quoted([stamp.day])}, 120)"
    return f"""SET XACT_ABORT ON;
BEGIN TRANSACTION;
IF EXISTS (SELECT 1 FROM SO_SODetails WITH (UPDLOCK) WHERE iSOsID = {line_id} AND ISNULL(iCusBomID, 0) = 0)
BEGIN
    DECLARE @cbom int;
    SET @cbom = {cbom};
    INSERT INTO bom_cbom (CustBomId, ParentId, Code, Description, CreateDate, CreateUser, CreateTime, UpdCount)
    VALUES (@cbom, {safe_id(model_part)}, {quoted([CBOM_CODE])}, {quoted([marker(CBOM_KEY)])}, {created}, {quoted([stamp.user])},
            GETDATE(), 0);
    INSERT INTO bom_cbomcomponent (CustBomId, CustOpComponentId, SortSeq, OpSeq, ComponentId, BaseQtyN, BaseQtyD,
        FVFlag, ParentScrap, CompScrap, Qty, Offset, WIPType, WhCode, ByProductFlag, AccuCostFlag, OpComponentId,
        ProductType, PSequence, Sequence, RelBaseQtyN, RelBaseQtyD, RelParentScrap, RelCompScrap, CostWIPRel)
    VALUES {_component_rows(parts, ids.comps)};
    UPDATE SO_SODetails SET iCusBomID = @cbom, busecusbom = 1 WHERE iSOsID = {line_id};
END
COMMIT TRANSACTION;"""
