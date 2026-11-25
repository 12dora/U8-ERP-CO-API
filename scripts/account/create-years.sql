-- create-years.sql — 给一个空测试账套补建后续年度（照 U8 系统管理「年度账 → 建立」在同一个库里加年度的写法）。
--
-- 用途：账套只有启用年度时，用今天的日期登录 U8 会报「不存在的年度」，账套体检 `years` 失败。
--       优先在 U8 系统管理里逐年「建立年度账」；没有界面可用时再用本脚本。
-- 前提：
--   * 账套是「一个库放所有年度」的布局（UFSystem..UA_AccountDatabase.iEndYear 为空），不支持每年一个库的账套。
--   * 起始年度 `SrcYear` 已在 UA_Period 里，有 12 个月。
--   * copy-config.sql 已跑过：本脚本从起始年度复制科目、科目编码方案和年度选项，起始年度要先对齐源账套。
--   * 测试账套没有业务单据（有单据时拒绝；确属测试账套、要强行补年度才把 `Force` 设为 1）。
-- 备份：执行前请 DBA 对 `DataDb` 和 UFSystem 各做一份 COPY_ONLY 完整备份。
-- 写什么：
--   * UFSystem（只写 cAcc_Id = `Acc` 的行）：UA_Period（每年 12 个月，按起始年度平移）、
--     UA_Account_sub（复制起始年度的模块行，启用日期留空，启用日期仍看 iYear=9999）、
--     UA_HoldAuth（只复制起始年度的 Admin 行，和界面建立一样）。
--   * `DataDb`：GL_mend（U8 自带的 GL_p_glmend / p_pu_glmend / p_om_glmend）、
--     code、GradeDef_Base（KEYWORD=code）、AccInformation_Year，都从起始年度复制，已有的行跳过。
--   * 不结转、不写余额、不改 UA_AccountDatabase.iEndYear、不改 AccInformation.iSystemYear。
-- 不写别的库：除了上面两个库，脚本只读 sys.databases。
-- 还原：恢复备份；或者手工删除
--   UFSystem 里 cAcc_Id = `Acc` 且 iYear 在 `FirstYear`..`LastYear` 的 UA_Period、UA_Account_sub、UA_HoldAuth 行，
--   以及 `DataDb` 里这些年度的 GL_mend、code、GradeDef_Base(code)、AccInformation_Year 行。
--
-- 用法（sysadmin 或对两个库有写权限的登录；SSMS 里要打开 SQLCMD 模式）：
--   1. 把下面的占位值换成你的值，例如 Acc=801、DataDb=UFDATA_801_2024、SrcYear=2024、FirstYear=2025、LastYear=2026。
--      注意 :setvar 优先于 sqlcmd -v，所以要改这里，不要只在命令行传。
--   2. sqlcmd -S <SQL 实例> -E -b -f 65001 -i create-years.sql
--   3. 看末尾的核对结果，再用 login-check 和账套体检 reports/account_readiness 复查。
-- 可以重跑：已存在的年度整年跳过，已有的科目、编码规则、选项行跳过。

:on error exit
:setvar Acc       "<账套号，三位数字>"
:setvar DataDb    "UFDATA_<账套号>_<起始年度>"
:setvar SrcYear   "<起始年度>"
:setvar FirstYear "<第一个要建的年度>"
:setvar LastYear  "<最后一个要建的年度>"
:setvar Force     "0"

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- 1. 参数和目标检查（只读）
DECLARE @acc nvarchar(10) = N'$(Acc)', @db sysname = N'$(DataDb)';
DECLARE @srcTxt varchar(20) = '$(SrcYear)', @firstTxt varchar(20) = '$(FirstYear)', @lastTxt varchar(20) = '$(LastYear)';
IF @acc NOT LIKE N'[0-9][0-9][0-9]'
BEGIN RAISERROR(N'Acc 必须是三位账套号。', 16, 1); RETURN; END;
IF @db NOT LIKE N'UFDATA[_]' + @acc + N'[_][12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'DataDb 必须是 UFDATA_<Acc>_<年度>，且账套号与 Acc 一致。', 16, 1); RETURN; END;
IF @srcTxt NOT LIKE '[12][0-9][0-9][0-9]' OR @firstTxt NOT LIKE '[12][0-9][0-9][0-9]' OR @lastTxt NOT LIKE '[12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'SrcYear / FirstYear / LastYear 必须是四位年度。', 16, 1); RETURN; END;
IF NOT (CAST(@srcTxt AS int) < CAST(@firstTxt AS int) AND CAST(@firstTxt AS int) <= CAST(@lastTxt AS int)
        AND CAST(@lastTxt AS int) - CAST(@srcTxt AS int) <= 10)
BEGIN RAISERROR(N'要求 SrcYear < FirstYear <= LastYear，且最多比起始年度晚 10 年。', 16, 1); RETURN; END;
IF DB_ID(@db) IS NULL
BEGIN RAISERROR(N'DataDb 不存在。', 16, 1); RETURN; END;
IF (SELECT COUNT(*) FROM UFSystem.dbo.UA_Account WHERE cAcc_Id = @acc) <> 1
BEGIN RAISERROR(N'UFSystem..UA_Account 里没有这个账套。', 16, 1); RETURN; END;
IF NOT EXISTS (SELECT 1 FROM UFSystem.dbo.UA_AccountDatabase
               WHERE cAcc_Id = @acc AND cDatabase = @db AND iEndYear IS NULL)
BEGIN RAISERROR(N'UA_AccountDatabase 里没有 Acc + DataDb 且 iEndYear 为空的行；本脚本只支持一个库放所有年度的账套。', 16, 1); RETURN; END;
IF (SELECT COUNT(*) FROM UFSystem.dbo.UA_Period
    WHERE cAcc_Id = @acc AND iYear = CAST(@srcTxt AS int) AND ISNULL(bIsDelete, 0) = 0) <> 12
BEGIN RAISERROR(N'起始年度在 UA_Period 里不是 12 个月。', 16, 1); RETURN; END;
GO

-- 2. 业务数据检查：有单据的账套不是空测试账套
IF '$(Force)' <> '1' AND (
       EXISTS (SELECT 1 FROM [$(DataDb)].dbo.GL_accvouch)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.mom_order)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.QMCHECKVOUCHERS)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.PO_Pomain)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.SO_SOMain)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.RdRecord01)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.RdRecord32)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.Ap_Vouch)
    OR EXISTS (SELECT 1 FROM [$(DataDb)].dbo.Ap_CloseBill))
BEGIN RAISERROR(N'目标账套已有业务单据。请在 U8 系统管理里建立年度账；确属测试账套才把 Force 设为 1。', 16, 1); RETURN; END;
GO

-- 3. 建年度（单个事务）
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @acc nvarchar(10) = N'$(Acc)';
DECLARE @src int = $(SrcYear), @last int = $(LastYear);
DECLARE @y int = $(FirstYear);

BEGIN TRANSACTION;
WHILE @y <= @last
BEGIN
    IF NOT EXISTS (SELECT 1 FROM UFSystem.dbo.UA_Period WHERE cAcc_Id = @acc AND iYear = @y)
    BEGIN
        INSERT INTO UFSystem.dbo.UA_Period (cAcc_Id, iYear, iId, dBegin, dEnd, bIsDelete)
        SELECT cAcc_Id, @y, iId, DATEADD(year, @y - @src, dBegin), DATEADD(year, @y - @src, dEnd), 0
        FROM UFSystem.dbo.UA_Period
        WHERE cAcc_Id = @acc AND iYear = @src AND ISNULL(bIsDelete, 0) = 0;

        INSERT INTO UFSystem.dbo.UA_Account_sub
            (cAcc_Id, iYear, cSub_Id, bIsDelete, bClosing, iModiPeri, dSubSysUsed, cUser_Id, dSubOriDate)
        SELECT cAcc_Id, @y, cSub_Id, bIsDelete, 0, 0, NULL, cUser_Id, NULL
        FROM UFSystem.dbo.UA_Account_sub
        WHERE cAcc_Id = @acc AND iYear = @src;

        INSERT INTO UFSystem.dbo.UA_HoldAuth
            (cAcc_Id, iYear, cUser_Id, cAuth_Id, iIsUser, csub_id, cAuthType, cRepellent, cRepellentModule, cNotRepellent)
        SELECT cAcc_Id, @y, cUser_Id, cAuth_Id, iIsUser, csub_id, cAuthType, cRepellent, cRepellentModule, cNotRepellent
        FROM UFSystem.dbo.UA_HoldAuth
        WHERE cAcc_Id = @acc AND iYear = @src AND cAuth_Id = N'Admin';
    END;

    IF NOT EXISTS (SELECT 1 FROM [$(DataDb)].dbo.GL_mend WHERE iyear = @y)
    BEGIN
        EXEC [$(DataDb)].dbo.GL_p_glmend @y, 1;
        EXEC [$(DataDb)].dbo.p_pu_glmend @y;
        EXEC [$(DataDb)].dbo.p_om_glmend @y;
    END;

    INSERT INTO [$(DataDb)].dbo.GradeDef_Base (KEYWORD, LGRADE, LGRADELEN, CODINGRULE, iSerial, iYear)
    SELECT s.KEYWORD, s.LGRADE, s.LGRADELEN, s.CODINGRULE, s.iSerial, @y
    FROM [$(DataDb)].dbo.GradeDef_Base AS s
    WHERE s.iYear = @src AND s.KEYWORD = N'code'
      AND NOT EXISTS (SELECT 1 FROM [$(DataDb)].dbo.GradeDef_Base AS t WHERE t.iYear = @y AND t.KEYWORD = s.KEYWORD);

    INSERT INTO [$(DataDb)].dbo.AccInformation_Year
        (cSysID, cID, cName, cCaption, cType, iYear, cValue, cDefault, bVisible, bEnable)
    SELECT s.cSysID, s.cID, s.cName, s.cCaption, s.cType, @y, s.cValue, s.cDefault, s.bVisible, s.bEnable
    FROM [$(DataDb)].dbo.AccInformation_Year AS s
    WHERE s.iYear = @src
      AND NOT EXISTS (SELECT 1 FROM [$(DataDb)].dbo.AccInformation_Year AS t
                      WHERE t.iYear = @y AND t.cSysID = s.cSysID AND t.cName = s.cName);

    SET @y += 1;
END;

-- 科目：U8 建立年度账时同样先停触发器 gl_v_code_InsteadTrigger（它会改 iyear）。只停这一个，其余触发器照常。
ALTER TABLE [$(DataDb)].dbo.code DISABLE TRIGGER gl_v_code_InsteadTrigger;
INSERT INTO [$(DataDb)].dbo.code (
    cclass, cclass_engl, cclassany, cclassany_engl, ccode, ccode_name, ccode_engl, igrade, bproperty,
    cbook_type, cbook_type_engl, chelp, cexch_name, cmeasure, bperson, bcus, bsup, bdept, bitem, cass_item,
    br, be, cgather, bend, bexchange, bcash, bbank, bused, bd_c, dbegin, dend, itrans, bclose, cother, iotherused,
    bcDefine1, bcDefine2, bcDefine3, bcDefine4, bcDefine5, bcDefine6, bcDefine7, bcDefine8, bcDefine9, bcDefine10,
    iViewItem, bGCJS, bCashItem, bcDefine11, bcDefine12, bcDefine13, bcDefine14, bcDefine15, bcDefine16,
    bReport, cUserDefineType, iyear, dModifyDate, bparacc, bpardiff)
SELECT
    c.cclass, c.cclass_engl, c.cclassany, c.cclassany_engl, c.ccode, c.ccode_name, c.ccode_engl, c.igrade, c.bproperty,
    c.cbook_type, c.cbook_type_engl, c.chelp, c.cexch_name, c.cmeasure, c.bperson, c.bcus, c.bsup, c.bdept, c.bitem, c.cass_item,
    c.br, c.be, c.cgather, c.bend, c.bexchange, c.bcash, c.bbank, c.bused, c.bd_c, c.dbegin, c.dend, c.itrans, c.bclose, c.cother, c.iotherused,
    c.bcDefine1, c.bcDefine2, c.bcDefine3, c.bcDefine4, c.bcDefine5, c.bcDefine6, c.bcDefine7, c.bcDefine8, c.bcDefine9, c.bcDefine10,
    c.iViewItem, c.bGCJS, c.bCashItem, c.bcDefine11, c.bcDefine12, c.bcDefine13, c.bcDefine14, c.bcDefine15, c.bcDefine16,
    c.bReport, c.cUserDefineType, y.yr, c.dModifyDate, c.bparacc, c.bpardiff
FROM [$(DataDb)].dbo.code AS c
JOIN (SELECT DISTINCT iYear AS yr FROM UFSystem.dbo.UA_Period
      WHERE cAcc_Id = @acc AND iYear BETWEEN $(FirstYear) AND @last) AS y ON 1 = 1
WHERE c.iyear = @src
  AND NOT EXISTS (SELECT 1 FROM [$(DataDb)].dbo.code AS t WHERE t.iyear = y.yr AND t.ccode = c.ccode);
ALTER TABLE [$(DataDb)].dbo.code ENABLE TRIGGER gl_v_code_InsteadTrigger;

COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：新年度应各有 12 个月；accsub 与起始年度条数相同；GL_mend 每年 13 行（第 0 期已结）；
--    code / grade / accy 与起始年度条数相同。
SELECT 'period' AS t, iYear, COUNT(*) AS n, MIN(CONVERT(varchar(10), dBegin, 23)) AS b, MAX(CONVERT(varchar(10), dEnd, 23)) AS e
FROM UFSystem.dbo.UA_Period WHERE cAcc_Id = N'$(Acc)' AND ISNULL(bIsDelete, 0) = 0 GROUP BY iYear ORDER BY iYear;
SELECT 'accsub' AS t, iYear, COUNT(*) AS n FROM UFSystem.dbo.UA_Account_sub WHERE cAcc_Id = N'$(Acc)' GROUP BY iYear ORDER BY iYear;
SELECT 'hold' AS t, iYear, COUNT(*) AS n FROM UFSystem.dbo.UA_HoldAuth WHERE cAcc_Id = N'$(Acc)' GROUP BY iYear ORDER BY iYear;
SELECT 'mend' AS t, iyear, COUNT(*) AS n, SUM(CAST(bflag AS int)) AS closed FROM [$(DataDb)].dbo.GL_mend GROUP BY iyear ORDER BY iyear;
SELECT 'code' AS t, iyear, COUNT(*) AS n FROM [$(DataDb)].dbo.code GROUP BY iyear ORDER BY iyear;
SELECT 'grade' AS t, iYear, COUNT(*) AS n FROM [$(DataDb)].dbo.GradeDef_Base WHERE KEYWORD = N'code' GROUP BY iYear ORDER BY iYear;
SELECT 'accy' AS t, iYear, COUNT(*) AS n FROM [$(DataDb)].dbo.AccInformation_Year GROUP BY iYear ORDER BY iYear;
GO
