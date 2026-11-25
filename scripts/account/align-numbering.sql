-- align-numbering.sql — 让测试账套的单据 / 档案编号规则与源账套一致。
--
-- 用途：回归和造数按源账套的编号习惯（前缀、流水号长度、是否允许手工改号）写单据；新账套的编号规则是建账缺省，
--       单号长度、前缀不同时生单和挑单会对不上。账套体检 `defaults` 在缺编号规则时 warn。
--       优先在 U8「基础设置 → 单据设置 → 单据编号设置」里逐张改；本脚本一次对齐所有两边都有的卡片。
-- 前提：源账套 `RefDb`（例如 UFDATA_998_2024）和测试账套 `TargetDb`（例如 UFDATA_801_2024）在同一个 SQL 实例上。
--   目标有业务单据时拒绝（`Force` = 1 可放行：只改规则，已用的号不变，但新单号可能与旧单号格式不同）。
-- 备份：执行前请 DBA 对 `TargetDb` 做一份 COPY_ONLY 完整备份。
-- 写什么：只 UPDATE `TargetDb`.dbo.VoucherNumber 里与源账套同 CardNumber 的行（规则列）。
--   不新增卡片，不碰 VoucherHistory（流水号的当前值），不写别的库。只读 `RefDb`。
-- 还原：恢复备份；或者在执行前先导出目标 VoucherNumber，用它 UPDATE 回去。
--
-- 用法：改下面的占位值（:setvar 优先于 sqlcmd -v），然后
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i align-numbering.sql
-- 可以重跑。

:on error exit
:setvar RefDb    "UFDATA_<源账套>_<年度>"
:setvar TargetDb "UFDATA_<测试账套>_<年度>"
:setvar Force    "0"

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- 1. 参数检查（只读）
DECLARE @ref sysname = N'$(RefDb)', @dst sysname = N'$(TargetDb)';
IF @ref NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
   OR @dst NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'RefDb 和 TargetDb 都必须是 UFDATA_<账套>_<年度>。', 16, 1); RETURN; END;
IF SUBSTRING(@ref, 8, 3) = SUBSTRING(@dst, 8, 3)
BEGIN RAISERROR(N'目标账套和源账套不能是同一个账套。', 16, 1); RETURN; END;
IF DB_ID(@ref) IS NULL OR DB_ID(@dst) IS NULL
BEGIN RAISERROR(N'RefDb 或 TargetDb 不存在。', 16, 1); RETURN; END;
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
BEGIN RAISERROR(N'目标账套已有业务单据。请在 U8 单据编号设置里改；确属测试账套才把 Force 设为 1。', 16, 1); RETURN; END;
GO

-- 3. 对齐规则（单个事务）
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRANSACTION;
UPDATE t SET
    t.AppName = s.AppName, t.CardName = s.CardName, t.iSize = s.iSize, t.dStartDate = s.dStartDate,
    t.bAllowHandWork = s.bAllowHandWork, t.bRepeatReDo = s.bRepeatReDo, t.bRdFlag = s.bRdFlag,
    t.iStartNumber = s.iStartNumber, t.iIsRdVoucher = s.iIsRdVoucher,
    t.Prefix1 = s.Prefix1, t.Prefix1Len = s.Prefix1Len, t.Prefix1Rule = s.Prefix1Rule,
    t.Prefix2 = s.Prefix2, t.Prefix2Len = s.Prefix2Len, t.Prefix2Rule = s.Prefix2Rule,
    t.Prefix3 = s.Prefix3, t.Prefix3Len = s.Prefix3Len, t.Prefix3Rule = s.Prefix3Rule,
    t.Glide = s.Glide, t.GlideLen = s.GlideLen, t.GlideRule = s.GlideRule, t.cSub_id = s.cSub_id,
    t.bGlideByContraCode = s.bGlideByContraCode, t.iGlideStep = s.iGlideStep, t.bUnfixLen = s.bUnfixLen
FROM [$(TargetDb)].dbo.VoucherNumber AS t
JOIN [$(RefDb)].dbo.VoucherNumber AS s ON s.CardNumber = t.CardNumber;
SELECT 'updated' AS t, @@ROWCOUNT AS n;
COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：diff 应为 0；only_ref 是源账套有、目标没有的卡片（本脚本不新增，需要时在 U8 里设）。
SELECT 'diff' AS t, COUNT(*) AS n
FROM [$(TargetDb)].dbo.VoucherNumber AS a
JOIN [$(RefDb)].dbo.VoucherNumber AS b ON a.CardNumber = b.CardNumber
WHERE ISNULL(a.Prefix1, N'') <> ISNULL(b.Prefix1, N'') OR ISNULL(a.Prefix1Rule, N'') <> ISNULL(b.Prefix1Rule, N'')
   OR ISNULL(a.Prefix2, N'') <> ISNULL(b.Prefix2, N'') OR ISNULL(a.GlideLen, -1) <> ISNULL(b.GlideLen, -1)
   OR ISNULL(a.bAllowHandWork, 0) <> ISNULL(b.bAllowHandWork, 0);
SELECT 'only_ref' AS t, b.CardNumber, b.CardName
FROM [$(RefDb)].dbo.VoucherNumber AS b
WHERE NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.VoucherNumber AS a WHERE a.CardNumber = b.CardNumber);
GO
