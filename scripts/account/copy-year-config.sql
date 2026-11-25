-- copy-year-config.sql — 把源账套按年度存放的应收应付科目和现金流量取数设置复制到空测试账套的每个年度。
--
-- 用途：基本科目、对方科目、受控科目、现金流量取数都按 iyear 存。建立年度账不复制它们，
--       所以新年度是空的，账套体检 `yearly_config` 失败，应收应付制单和现金流量取不到科目。
--       在 copy-config.sql、建年度（create-years.sql 或 U8 建立年度账）之后、copy-qm-workflow.sql 之前跑；
--       造数之前建了新年度就再跑一次。造数之后（目标已有单据）`Force` = 0 时拒绝执行。
--       跨年（已造数并保留的账套到了新年度）：先 create-years.sql（Force = 1）建新年度，再用本脚本 Force = 1 补新年度。
--       结算方式科目（Ap_SStyleCode）不在这里：结算方式要经 API 造好，造数之后用 copy-settle-accounts.sql 补。
-- 前提：
--   * 源账套 `RefDb`（例如 UFDATA_998_2024）和空测试账套 `TargetDb`（例如 UFDATA_801_2024）在同一个 SQL 实例上。
--   * copy-config.sql 已跑过（科目已在目标账套里）。
--   * Force = 0（缺省）：目标没有任何业务单据（总账凭证、生产订单、检验单、采购订单、销售订单、采购入库、销售出库、
--     应收应付单、收付款单），有单据就拒绝。
--   * Force = 1：不查单据（确属测试账套才设），但只补「空的」——某年度在某张表里一行都没有，才把这一年复制进这张表；
--     已有行的（年度，表）一行不动，不删任何行。用于跨年补新年度。
-- 年度：对 `FirstYear`..`LastYear` 的每个年度 Y，源账套 GL_mend 里有 Y 就复制源账套 Y 年的行，
--       否则复制源账套 `RefYear` 年的行，并把 iyear 改成 Y。
-- 备份：执行前请 DBA 对 `TargetDb` 做一份 COPY_ONLY 完整备份。
-- 写什么（Force = 0）：只写 `TargetDb` 这些年度的 GL_CashItemDataSource、Ap_InputCode（先删后插），
--   以及 AP_OppCodeSet、AP_CtrlCodeSet 中不指向具体客户、供应商、存货的通用行（只删、只插这些行）。
--   Force = 1 时同样的行，只插进空的（年度，表），不删。
--   指向具体档案的行既不复制也不删：它们依赖档案，档案经 API 造好之后再在 U8 里设。只读 `RefDb`。
-- 还原：恢复备份。
--
-- 用法：改下面的占位值（:setvar 优先于 sqlcmd -v），然后
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i copy-year-config.sql
-- 可以重跑。Force = 1 重跑时已补过的（年度，表）不再变。

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

-- 2. 空账套检查（Force = 1 时跳过；那时只补空的（年度，表），不删）
IF '$(Force)' <> '1' AND (EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.QMCHECKVOUCHERS) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.mom_order)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.GL_accvouch)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.PO_Pomain) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.SO_SOMain)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord01) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord32)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_Vouch) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_CloseBill))
BEGIN RAISERROR(N'目标账套已有业务数据；配置复制只用于空账套。跨年补新年度才把 Force 设为 1（只补空的年度，不删）。', 16, 1); RETURN; END;
GO

-- 3. 复制（单个事务）。@force = 1：不执行 DELETE，INSERT 只取目标这张表里该年度一行都没有的年度
--    （NOT EXISTS 按语句开始前的目标判断，同一条 INSERT 插进的行不影响它）。
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ry int = $(RefYear), @first int = $(FirstYear), @last int = $(LastYear), @force bit = $(Force);
DECLARE @y int = @first;
BEGIN TRANSACTION;

CREATE TABLE #ymap (y int PRIMARY KEY, src int NOT NULL);
WHILE @y <= @last
BEGIN
    INSERT INTO #ymap (y, src)
    SELECT @y, CASE WHEN EXISTS (SELECT 1 FROM [$(RefDb)].dbo.GL_mend WHERE iyear = @y) THEN @y ELSE @ry END;
    SET @y += 1;
END;

-- 3.1 现金流量取数
IF @force = 0 DELETE FROM [$(TargetDb)].dbo.GL_CashItemDataSource WHERE iyear BETWEEN @first AND @last;
INSERT INTO [$(TargetDb)].dbo.GL_CashItemDataSource
    (cItemCode, bDataStyle, cDataSource, bDir, bRelation, dStartDate, dEndDate, iyear)
SELECT s.cItemCode, s.bDataStyle, s.cDataSource, s.bDir, s.bRelation, s.dStartDate, s.dEndDate, m.y
FROM [$(RefDb)].dbo.GL_CashItemDataSource AS s
JOIN #ymap AS m ON s.iyear = m.src
WHERE @force = 0 OR NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.GL_CashItemDataSource AS t WHERE t.iyear = m.y);

-- 3.2 应收应付基本科目
IF @force = 0 DELETE FROM [$(TargetDb)].dbo.Ap_InputCode WHERE iyear BETWEEN @first AND @last;
INSERT INTO [$(TargetDb)].dbo.Ap_InputCode
    (cNum, cNote, cNote_f, cFlag, cArCode, cArCodeName, cApCode, cApCodeName, iyear)
SELECT s.cNum, s.cNote, s.cNote_f, s.cFlag, s.cArCode, s.cArCodeName, s.cApCode, s.cApCodeName, m.y
FROM [$(RefDb)].dbo.Ap_InputCode AS s
JOIN #ymap AS m ON s.iyear = m.src
WHERE @force = 0 OR NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_InputCode AS t WHERE t.iyear = m.y);

-- 3.3 对方科目：只删、只复制不指向具体客户、供应商、存货的行
IF @force = 0
    DELETE FROM [$(TargetDb)].dbo.AP_OppCodeSet
    WHERE iyear BETWEEN @first AND @last
      AND (cCusVendCode IS NULL OR LTRIM(RTRIM(cCusVendCode)) = N'')
      AND (cInvCode IS NULL OR LTRIM(RTRIM(cInvCode)) = N'');
INSERT INTO [$(TargetDb)].dbo.AP_OppCodeSet (
    cInvCode, cInvCCode, cCusVendCode, cCusVendCCode, cSaPuTypeCode, cDistrictCode, cexch_name, iTax,
    cSaIncomeCode, cYjzzsCode, cSaRetCode, cCmIncomeCode, cExIncomeCode, cSrIncomeCode,
    cPuCode, cPuTaxCode, cJxszcCode, cExRetCode, cFlag, iyear, cSaIncomeYWCode, cYjzzsYWCode, cContractType)
SELECT
    s.cInvCode, s.cInvCCode, s.cCusVendCode, s.cCusVendCCode, s.cSaPuTypeCode, s.cDistrictCode, s.cexch_name, s.iTax,
    s.cSaIncomeCode, s.cYjzzsCode, s.cSaRetCode, s.cCmIncomeCode, s.cExIncomeCode, s.cSrIncomeCode,
    s.cPuCode, s.cPuTaxCode, s.cJxszcCode, s.cExRetCode, s.cFlag, m.y, s.cSaIncomeYWCode, s.cYjzzsYWCode, s.cContractType
FROM [$(RefDb)].dbo.AP_OppCodeSet AS s
JOIN #ymap AS m ON s.iyear = m.src
WHERE (s.cCusVendCode IS NULL OR LTRIM(RTRIM(s.cCusVendCode)) = N'')
  AND (s.cInvCode IS NULL OR LTRIM(RTRIM(s.cInvCode)) = N'')
  AND (@force = 0 OR NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.AP_OppCodeSet AS t WHERE t.iyear = m.y));

-- 3.4 受控科目：只删、只复制不指向具体客户、供应商、存货的行
IF @force = 0
    DELETE FROM [$(TargetDb)].dbo.AP_CtrlCodeSet
    WHERE iyear BETWEEN @first AND @last
      AND (cCusVendCode IS NULL OR LTRIM(RTRIM(cCusVendCode)) = N'')
      AND (cInvCode IS NULL OR LTRIM(RTRIM(cInvCode)) = N'');
INSERT INTO [$(TargetDb)].dbo.AP_CtrlCodeSet (
    cInvCode, cInvCCode, cCusVendCode, cCusVendCCode, cSaPuTypeCode, cDistrictCode, cexch_name,
    cCtrlCode, cPreCode, cFlag, iyear, cYWCtrlCode, cContractType)
SELECT
    s.cInvCode, s.cInvCCode, s.cCusVendCode, s.cCusVendCCode, s.cSaPuTypeCode, s.cDistrictCode, s.cexch_name,
    s.cCtrlCode, s.cPreCode, s.cFlag, m.y, s.cYWCtrlCode, s.cContractType
FROM [$(RefDb)].dbo.AP_CtrlCodeSet AS s
JOIN #ymap AS m ON s.iyear = m.src
WHERE (s.cCusVendCode IS NULL OR LTRIM(RTRIM(s.cCusVendCode)) = N'')
  AND (s.cInvCode IS NULL OR LTRIM(RTRIM(s.cInvCode)) = N'')
  AND (@force = 0 OR NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.AP_CtrlCodeSet AS t WHERE t.iyear = m.y));

SELECT 'year_map' AS t, y, src FROM #ymap ORDER BY y;
COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：每个年度目标都应有行。
--    引用了目标账套里不存在的科目的行也列出来，n_missing_code 应为 0。
SELECT y.iyear,
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.GL_CashItemDataSource WHERE iyear = y.iyear) AS cash_src,
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.Ap_InputCode WHERE iyear = y.iyear) AS input_code,
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.AP_OppCodeSet WHERE iyear = y.iyear) AS opp_code,
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.AP_CtrlCodeSet WHERE iyear = y.iyear) AS ctrl_code,
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.code WHERE iyear = y.iyear) AS accounts
FROM (SELECT DISTINCT iyear FROM [$(TargetDb)].dbo.GL_mend WHERE iyear BETWEEN $(FirstYear) AND $(LastYear)) AS y
ORDER BY y.iyear;
SELECT 'input_code' AS t, COUNT(*) AS n_missing_code
FROM [$(TargetDb)].dbo.Ap_InputCode AS a
WHERE a.iyear BETWEEN $(FirstYear) AND $(LastYear)
  AND ((ISNULL(a.cArCode, N'') <> N'' AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.code AS c WHERE c.iyear = a.iyear AND c.ccode = a.cArCode))
    OR (ISNULL(a.cApCode, N'') <> N'' AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.code AS c WHERE c.iyear = a.iyear AND c.ccode = a.cApCode)));
GO
