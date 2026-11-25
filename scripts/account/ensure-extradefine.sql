-- ensure-extradefine.sql — 给缺表的测试账套补建供应商扩展自定义项表 Vendor_extradefine（空表）。
--
-- 用途：有些建账模板建出的账套没有 Vendor_extradefine（后来在 U8 里设置过供应商扩展自定义项的账套才有），
--       新增供应商时 U8 要往这张表写一行，表不存在就失败；账套体检 `vendor_extradefine` 失败。
--       在 U8「基础档案 → 自定义项」里给供应商加一个扩展自定义项也会建出这张表；本脚本只建最小的空表。
-- 前提：目标 `TargetDb`（例如 UFDATA_801_2024）是测试账套。有业务单据时拒绝（`Force` = 1 可放行）。
-- 备份：执行前请 DBA 对 `TargetDb` 做一份 COPY_ONLY 完整备份。
-- 写什么：只在 `TargetDb` 里、表不存在时 CREATE TABLE dbo.Vendor_extradefine (cVenCode nvarchar(20) 主键)。
--   列的排序规则用目标库的缺省排序规则。表已存在时什么都不做。不写别的库。
-- 还原：表是空的、没有别的对象引用它时，DROP TABLE [TargetDb].dbo.Vendor_extradefine；或恢复备份。
--
-- 用法：改下面的占位值（:setvar 优先于 sqlcmd -v），然后
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i ensure-extradefine.sql
-- 可以重跑。

:on error exit
:setvar TargetDb "UFDATA_<测试账套>_<年度>"
:setvar Force    "0"

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- 1. 参数检查（只读）
IF N'$(TargetDb)' NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'TargetDb 必须是 UFDATA_<账套>_<年度>。', 16, 1); RETURN; END;
IF DB_ID(N'$(TargetDb)') IS NULL
BEGIN RAISERROR(N'TargetDb 不存在。', 16, 1); RETURN; END;
GO

-- 2. 业务数据检查
IF '$(Force)' <> '1' AND (
       EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.GL_accvouch)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.mom_order)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.QMCHECKVOUCHERS)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.PO_Pomain)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.SO_SOMain)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord01)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord32)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_Vouch)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_CloseBill))
BEGIN RAISERROR(N'目标账套已有业务单据。请在 U8 自定义项里处理；确属测试账套才把 Force 设为 1。', 16, 1); RETURN; END;
GO

-- 3. 建表（单个事务）
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRANSACTION;
IF OBJECT_ID(N'[$(TargetDb)].dbo.Vendor_extradefine', N'U') IS NULL
    CREATE TABLE [$(TargetDb)].dbo.Vendor_extradefine (
        cVenCode nvarchar(20) NOT NULL,
        CONSTRAINT PK_Vendor_extradefine PRIMARY KEY CLUSTERED (cVenCode)
    );
COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：应有一行；cVenCode 的排序规则应与 Vendor.cVenCode 相同。
SELECT t.name, CONVERT(varchar(19), t.create_date, 120) AS created, c.collation_name AS extradefine_collation,
       (SELECT vc.collation_name FROM [$(TargetDb)].sys.columns AS vc
        WHERE vc.object_id = OBJECT_ID(N'[$(TargetDb)].dbo.Vendor') AND vc.name = N'cVenCode') AS vendor_collation
FROM [$(TargetDb)].sys.tables AS t
JOIN [$(TargetDb)].sys.columns AS c ON c.object_id = t.object_id AND c.name = N'cVenCode'
WHERE t.name = N'Vendor_extradefine';
GO
