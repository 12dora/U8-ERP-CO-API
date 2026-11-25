-- copy-settle-accounts.sql — 把源账套的结算方式科目（Ap_SStyleCode）补到测试账套的每个年度。
--
-- 用途：结算方式科目按 iyear 存，并且要求结算方式已在目标账套里（SettleStyle）。空账套的结算方式要经 API 造数才有，
--       所以这一项不能和 copy-year-config.sql 一起在造数之前做。造数（`python -m co.e2e.seed`）并 `close --keep`
--       之后跑本脚本；之后再造了新的结算方式或建了新年度，重跑即可。
-- 前提：
--   * 源账套 `RefDb`（例如 UFDATA_998_2024）和测试账套 `TargetDb`（例如 UFDATA_801_2024）在同一个 SQL 实例上。
--   * 没有打开的调试会话（会话结束时的快照还原会把写入还原掉）。
--   * 造数之后目标已有业务单据，这时要把 `Force` 设为 1 才执行（确属测试账套才设）。`Force` = 0 时有单据就拒绝。
-- 年度：对 `FirstYear`..`LastYear` 的每个年度 Y，源账套 GL_mend 里有 Y 就取源账套 Y 年的行，
--       否则取源账套 `RefYear` 年的行，并把 iyear 改成 Y（与 copy-year-config.sql 相同）。
-- 备份：执行前请 DBA 对 `TargetDb` 做一份 COPY_ONLY 完整备份。
-- 写什么：只往 `TargetDb`.dbo.Ap_SStyleCode 插行：只取没有银行账号的通用行，结算方式在目标里存在，
--   且目标同年度、同方向（cFlag）、同结算方式、同币种还没有行。不删、不改任何已有行（包括在 U8 里手工设的）。
--   只读 `RefDb`。
-- 还原：恢复备份；或者删除本次新增的行（执行前先记下目标 Ap_SStyleCode 的行数和内容）。
--
-- 用法：改下面的占位值（:setvar 优先于 sqlcmd -v），然后
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i copy-settle-accounts.sql
-- 可以重跑：已有的行跳过。

:on error exit
:setvar RefDb     "UFDATA_<源账套>_<年度>"
:setvar TargetDb  "UFDATA_<测试账套>_<年度>"
:setvar RefYear   "<源账套缺年度时用的来源年度>"
:setvar FirstYear "<测试账套的启用年度>"
:setvar LastYear  "<测试账套的最后一个年度>"
:setvar Force     "0"

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- 1. 参数检查（只读）
DECLARE @ref sysname = N'$(RefDb)', @dst sysname = N'$(TargetDb)';
IF @ref NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
   OR @dst NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'RefDb 和 TargetDb 都必须是 UFDATA_<账套>_<年度>。', 16, 1); RETURN; END;
IF SUBSTRING(@ref, 8, 3) = SUBSTRING(@dst, 8, 3)
BEGIN RAISERROR(N'目标账套和源账套不能是同一个账套。', 16, 1); RETURN; END;
IF '$(RefYear)' NOT LIKE '[12][0-9][0-9][0-9]' OR '$(FirstYear)' NOT LIKE '[12][0-9][0-9][0-9]'
   OR '$(LastYear)' NOT LIKE '[12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'RefYear / FirstYear / LastYear 必须是四位年度。', 16, 1); RETURN; END;
IF NOT (CAST('$(FirstYear)' AS int) <= CAST('$(LastYear)' AS int)
        AND CAST('$(LastYear)' AS int) - CAST('$(FirstYear)' AS int) <= 10)
BEGIN RAISERROR(N'要求 FirstYear <= LastYear，跨度不超过 10 年。', 16, 1); RETURN; END;
IF '$(Force)' NOT IN ('0', '1')
BEGIN RAISERROR(N'Force 只能是 0 或 1。', 16, 1); RETURN; END;
IF DB_ID(@ref) IS NULL OR DB_ID(@dst) IS NULL
BEGIN RAISERROR(N'RefDb 或 TargetDb 不存在。', 16, 1); RETURN; END;
GO

-- 2. 业务数据检查（造数之后要 Force = 1）
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
BEGIN RAISERROR(N'目标账套已有业务单据。请在 U8 应收应付的结算方式科目设置里补；确属测试账套才把 Force 设为 1。', 16, 1); RETURN; END;
GO

-- 3. 补结算方式科目（单个事务，只插不删）
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ry int = $(RefYear), @first int = $(FirstYear), @last int = $(LastYear);
DECLARE @y int = @first;
BEGIN TRANSACTION;

CREATE TABLE #ymap (y int PRIMARY KEY, src int NOT NULL);
WHILE @y <= @last
BEGIN
    INSERT INTO #ymap (y, src)
    SELECT @y, CASE WHEN EXISTS (SELECT 1 FROM [$(RefDb)].dbo.GL_mend WHERE iyear = @y) THEN @y ELSE @ry END;
    SET @y += 1;
END;

INSERT INTO [$(TargetDb)].dbo.Ap_SStyleCode (cSettleStyle, cexch_name, cCode, cFlag, cBAccount, iyear)
SELECT s.cSettleStyle, s.cexch_name, s.cCode, s.cFlag, s.cBAccount, m.y
FROM [$(RefDb)].dbo.Ap_SStyleCode AS s
JOIN #ymap AS m ON s.iyear = m.src
WHERE (s.cBAccount IS NULL OR LTRIM(RTRIM(s.cBAccount)) = N'')
  AND EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.SettleStyle AS ss WHERE ss.cSSCode = s.cSettleStyle)
  AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_SStyleCode AS t WITH (UPDLOCK, HOLDLOCK)
                  WHERE t.iyear = m.y AND t.cFlag = s.cFlag AND t.cSettleStyle = s.cSettleStyle
                    AND ISNULL(t.cexch_name, N'') = ISNULL(s.cexch_name, N''));

SELECT 'inserted' AS t, @@ROWCOUNT AS n;
SELECT 'year_map' AS t, y, src FROM #ymap ORDER BY y;
COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：每个年度的结算方式科目行数；源账套有、目标缺结算方式（SettleStyle）而没补上的，
--    在 missing_style 里列出：先经 API 或在 U8 里建好这些结算方式，再重跑。引用了目标里不存在的科目的行数 n_missing_code 应为 0。
SELECT y.iyear,
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.Ap_SStyleCode WHERE iyear = y.iyear) AS settle_code
FROM (SELECT DISTINCT iyear FROM [$(TargetDb)].dbo.GL_mend WHERE iyear BETWEEN $(FirstYear) AND $(LastYear)) AS y
ORDER BY y.iyear;
SELECT DISTINCT 'missing_style' AS t, s.cSettleStyle
FROM [$(RefDb)].dbo.Ap_SStyleCode AS s
WHERE (s.cBAccount IS NULL OR LTRIM(RTRIM(s.cBAccount)) = N'')
  AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.SettleStyle AS ss WHERE ss.cSSCode = s.cSettleStyle);
SELECT 'settle_code' AS t, COUNT(*) AS n_missing_code
FROM [$(TargetDb)].dbo.Ap_SStyleCode AS a
WHERE a.iyear BETWEEN $(FirstYear) AND $(LastYear)
  AND ISNULL(a.cCode, N'') <> N''
  AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.code AS c WHERE c.iyear = a.iyear AND c.ccode = a.cCode);
GO
