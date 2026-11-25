-- copy-config.sql — 把源账套的账套级配置复制到一个空测试账套（不含业务数据）。
--
-- 用途：新建的空账套只有建账向导预置的科目体系和选项，回归要用的编码方案、模块选项、科目、
--       现金流量项目、人员类别、行业分类、生产订单类别、自定义项设置、凭证类别都和源账套不同。
--       顺序：copy-config.sql（本脚本，账套级 + 起始年度科目）→ 建年度（create-years.sql 或 U8 建立年度账）
--         → copy-year-config.sql（逐年度的应收应付科目、现金流量取数）→ copy-qm-workflow.sql（检验方案、审批流）；
--         造数之后再 copy-settle-accounts.sql（结算方式科目）。
--       本脚本必须在建年度之前跑：建年度从起始年度复制科目、编码方案和年度选项，之后本脚本只改 `TargetYear` 这一年。
--       已经先建了年度的，对每个后续年度再跑一次本脚本，`TargetYear` 设成那一年（`RefYear` 不变）。
-- 前提：
--   * 源账套 `RefDb`（例如 UFDATA_998_2024）配置完整、能跑通回归；目标 `TargetDb`（例如 UFDATA_801_2024）
--     是刚建的空账套，两者在同一个 SQL 实例上，打过同样的补丁（账套体检 `patches` 为 ok）。
--   * `RefYear` 是源账套里要复制的年度，`TargetYear` 是目标账套的启用年度（通常相同）。
--   * 目标没有任何业务单据（总账凭证、生产订单、检验单、采购订单、销售订单、采购入库、销售出库、应收应付单、收付款单）。有单据时一律拒绝，没有放行开关。
-- 备份：执行前请 DBA 对 `TargetDb` 做一份 COPY_ONLY 完整备份。
-- 写什么：只写 `TargetDb`：GradeDef_Base（iYear 0 和 TargetYear）、AccInformation_Year（编码级次、行业）、
--   AccInformation（SA/PU/ST/GL/AR/AP/QM/MO/IA/BO 选项值和 AA.bFrgnCurrencyAcc；不复制账套身份、各种启用日期）、
--   code（TargetYear 科目）、fitemss98、HR_CT000/416/803、TradeClass、mom_motype、UserDef_Base/UserDef_Lang（档案自定义项）、
--   dsign（凭证类别）。只读 `RefDb`。不碰 UFSystem、UFMeta、GL_mend、余额表、权限表、任何单据表。
-- 还原：恢复备份。脚本是 MERGE / 补缺 / 先删后插，没有逐行撤销的办法。
-- 排序规则：第 3 批先 USE `TargetDb`，临时表 #acc_skip 的 COLLATE DATABASE_DEFAULT 因此取目标库的排序规则。
--
-- 用法：改下面的占位值（:setvar 优先于 sqlcmd -v），然后
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i copy-config.sql
-- 可以重跑。

:on error exit
:setvar RefDb      "UFDATA_<源账套>_<年度>"
:setvar TargetDb   "UFDATA_<测试账套>_<年度>"
:setvar RefYear    "<源账套要复制的年度>"
:setvar TargetYear "<测试账套的启用年度>"

SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

-- 1. 参数检查（只读）
DECLARE @ref sysname = N'$(RefDb)', @dst sysname = N'$(TargetDb)';
IF @ref NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
   OR @dst NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'RefDb 和 TargetDb 都必须是 UFDATA_<账套>_<年度>。', 16, 1); RETURN; END;
IF SUBSTRING(@ref, 8, 3) = SUBSTRING(@dst, 8, 3)
BEGIN RAISERROR(N'目标账套和源账套不能是同一个账套。', 16, 1); RETURN; END;
IF '$(RefYear)' NOT LIKE '[12][0-9][0-9][0-9]' OR '$(TargetYear)' NOT LIKE '[12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'RefYear / TargetYear 必须是四位年度。', 16, 1); RETURN; END;
IF DB_ID(@ref) IS NULL OR DB_ID(@dst) IS NULL
BEGIN RAISERROR(N'RefDb 或 TargetDb 不存在。', 16, 1); RETURN; END;
GO

-- 2. 空账套检查（没有放行开关）
IF EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.QMCHECKVOUCHERS) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.mom_order)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.GL_accvouch)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.PO_Pomain) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.SO_SOMain)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord01) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord32)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_Vouch) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_CloseBill)
BEGIN RAISERROR(N'目标账套已有业务数据；配置复制只用于空账套。', 16, 1); RETURN; END;
IF NOT EXISTS (SELECT 1 FROM [$(RefDb)].dbo.code WHERE iyear = $(RefYear))
BEGIN RAISERROR(N'源账套在 RefYear 没有科目。', 16, 1); RETURN; END;
GO

-- 3. 复制（单个事务）
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
-- 下面的临时表用 COLLATE DATABASE_DEFAULT，即当前库的排序规则。切到目标库，让它和目标表的排序规则一致；
-- 不切的话是登录的缺省库（通常 master），排序规则不同时比较会报 468。其余引用都是三段名，不受影响。
USE [$(TargetDb)];
DECLARE @ry int = $(RefYear), @ty int = $(TargetYear);
BEGIN TRANSACTION;

-- 3.0 不复制的 AccInformation 选项：账套身份、单位名称、升级和期初标志、外部地址、口令。
--     另外按名称模式排除所有启用日期（*StartDate* / *FirstDate* / *NatureDate* / *PeriodDate* / cAccArchive*）。
CREATE TABLE #acc_skip (cName nvarchar(60) COLLATE DATABASE_DEFAULT NOT NULL PRIMARY KEY);
INSERT INTO #acc_skip (cName) VALUES
    (N'cID'), (N'bUpgraded'), (N'bFirstTime'), (N'iYearUpgrade'), (N'bAccInfoAvailable'), (N'OpenAccFlag'),
    (N'AccountUnit'), (N'NativeUnit'), (N'cMakeCompany'), (N'cZRDWName'), (N'cZZTZSName'),
    (N'bQCInput'), (N'bAutoAdjustedClosing'), (N'ArchiveDate'),
    (N'bUpdateAPProxyIM'), (N'bUpdateYGZ'), (N'bstupgrade'), (N'PU160PatchDate'), (N'bUpdateCus'), (N'bUpdateSup'),
    (N'bBLCusQC'), (N'bBLSupQC'), (N'cQCVouchMaker'), (N'cQCVouchVerifyer'),
    (N'YYCAPPID'), (N'YYCbaseurl'), (N'MaxCostPSW'), (N'cYbzVoucherURL'), (N'UserAuthCtrl'),
    (N'cGradeLevel'); -- 非年度的 AA.cGradeLevel 由建账决定；年度级的在 3.2 复制

-- 3.1 编码方案 GradeDef_Base（主键 iYear + KEYWORD）：iYear 0 原样，RefYear 改成 TargetYear
MERGE [$(TargetDb)].dbo.GradeDef_Base AS t
USING (SELECT KEYWORD, LGRADE, LGRADELEN, CODINGRULE, iSerial,
              CASE WHEN iYear = 0 THEN 0 ELSE @ty END AS iYear
       FROM [$(RefDb)].dbo.GradeDef_Base WHERE iYear IN (0, @ry)) AS s
   ON t.iYear = s.iYear AND t.KEYWORD = s.KEYWORD
WHEN MATCHED THEN UPDATE SET
    t.LGRADE = s.LGRADE, t.LGRADELEN = s.LGRADELEN, t.CODINGRULE = s.CODINGRULE, t.iSerial = s.iSerial
WHEN NOT MATCHED BY TARGET THEN INSERT (KEYWORD, LGRADE, LGRADELEN, CODINGRULE, iSerial, iYear)
    VALUES (s.KEYWORD, s.LGRADE, s.LGRADELEN, s.CODINGRULE, s.iSerial, s.iYear);

-- 3.2 年度选项：只复制科目编码级次和行业性质（不复制各模块的年度启用日期）
MERGE [$(TargetDb)].dbo.AccInformation_Year AS t
USING (SELECT cSysID, cID, cName, cCaption, cType, @ty AS iYear, cValue, cDefault, bVisible, bEnable
       FROM [$(RefDb)].dbo.AccInformation_Year
       WHERE iYear = @ry AND cName IN (N'cGradeLevel', N'cTradeKind')) AS s
   ON t.cSysID = s.cSysID AND t.cName = s.cName AND t.iYear = s.iYear
WHEN MATCHED THEN UPDATE SET
    t.cValue = s.cValue, t.cCaption = s.cCaption, t.cType = s.cType,
    t.cDefault = s.cDefault, t.bVisible = s.bVisible, t.bEnable = s.bEnable
WHEN NOT MATCHED BY TARGET THEN INSERT (cSysID, cID, cName, cCaption, cType, iYear, cValue, cDefault, bVisible, bEnable)
    VALUES (s.cSysID, s.cID, s.cName, s.cCaption, s.cType, s.iYear, s.cValue, s.cDefault, s.bVisible, s.bEnable);

-- 3.3 模块选项值 AccInformation（主键 cSysID + cName）
UPDATE t
SET t.cValue = s.cValue, t.cCaption = s.cCaption, t.cType = s.cType, t.cDefault = s.cDefault, t.bVisible = s.bVisible
FROM [$(TargetDb)].dbo.AccInformation AS t
JOIN [$(RefDb)].dbo.AccInformation AS s ON t.cSysID = s.cSysID AND t.cName = s.cName
WHERE t.cSysID IN (N'SA', N'PU', N'ST', N'GL', N'AR', N'AP', N'QM', N'MO', N'IA', N'BO')
  AND t.cName NOT IN (SELECT cName FROM #acc_skip)
  AND t.cName NOT LIKE N'%StartDate%' AND t.cName NOT LIKE N'%FirstDate%'
  AND t.cName NOT LIKE N'%NatureDate%' AND t.cName NOT LIKE N'%PeriodDate%'
  AND t.cName NOT LIKE N'%NatStartDate%' AND t.cName NOT LIKE N'cAccArchive%';

-- 外币核算开关（外币用例要用）
UPDATE t SET t.cValue = s.cValue
FROM [$(TargetDb)].dbo.AccInformation AS t
JOIN [$(RefDb)].dbo.AccInformation AS s ON t.cSysID = s.cSysID AND t.cName = s.cName
WHERE s.cSysID = N'AA' AND s.cName = N'bFrgnCurrencyAcc';

INSERT INTO [$(TargetDb)].dbo.AccInformation (cSysID, cID, cName, cCaption, cType, cValue, cDefault, bVisible, bEnable)
SELECT s.cSysID, s.cID, s.cName, s.cCaption, s.cType, s.cValue, s.cDefault, s.bVisible, s.bEnable
FROM [$(RefDb)].dbo.AccInformation AS s
WHERE s.cSysID IN (N'SA', N'PU', N'ST', N'GL', N'AR', N'AP', N'QM', N'MO', N'IA', N'BO')
  AND s.cName NOT IN (SELECT cName FROM #acc_skip)
  AND s.cName NOT LIKE N'%StartDate%' AND s.cName NOT LIKE N'%FirstDate%'
  AND s.cName NOT LIKE N'%NatureDate%' AND s.cName NOT LIKE N'%PeriodDate%'
  AND s.cName NOT LIKE N'%NatStartDate%' AND s.cName NOT LIKE N'cAccArchive%'
  AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.AccInformation AS t WHERE t.cSysID = s.cSysID AND t.cName = s.cName);

-- 3.4 科目 code（主键 iyear + ccode；不写自增列 i_id；触发器只改 iyear = 0 的行，这里总是写 TargetYear）
UPDATE t SET
    t.cclass = s.cclass, t.cclass_engl = s.cclass_engl, t.cclassany = s.cclassany, t.cclassany_engl = s.cclassany_engl,
    t.ccode_name = s.ccode_name, t.ccode_engl = s.ccode_engl, t.igrade = s.igrade, t.bproperty = s.bproperty,
    t.cbook_type = s.cbook_type, t.cbook_type_engl = s.cbook_type_engl, t.chelp = s.chelp,
    t.cexch_name = s.cexch_name, t.cmeasure = s.cmeasure, t.bperson = s.bperson, t.bcus = s.bcus, t.bsup = s.bsup,
    t.bdept = s.bdept, t.bitem = s.bitem, t.cass_item = s.cass_item, t.br = s.br, t.be = s.be, t.cgather = s.cgather,
    t.bend = s.bend, t.bexchange = s.bexchange, t.bcash = s.bcash, t.bbank = s.bbank, t.bused = s.bused, t.bd_c = s.bd_c,
    t.dbegin = s.dbegin, t.dend = s.dend, t.itrans = s.itrans, t.bclose = s.bclose, t.cother = s.cother,
    t.iotherused = s.iotherused,
    t.bcDefine1 = s.bcDefine1, t.bcDefine2 = s.bcDefine2, t.bcDefine3 = s.bcDefine3, t.bcDefine4 = s.bcDefine4,
    t.bcDefine5 = s.bcDefine5, t.bcDefine6 = s.bcDefine6, t.bcDefine7 = s.bcDefine7, t.bcDefine8 = s.bcDefine8,
    t.bcDefine9 = s.bcDefine9, t.bcDefine10 = s.bcDefine10, t.iViewItem = s.iViewItem, t.bGCJS = s.bGCJS,
    t.bCashItem = s.bCashItem, t.bcDefine11 = s.bcDefine11, t.bcDefine12 = s.bcDefine12, t.bcDefine13 = s.bcDefine13,
    t.bcDefine14 = s.bcDefine14, t.bcDefine15 = s.bcDefine15, t.bcDefine16 = s.bcDefine16,
    t.bReport = s.bReport, t.cUserDefineType = s.cUserDefineType, t.dModifyDate = s.dModifyDate,
    t.bparacc = s.bparacc, t.bpardiff = s.bpardiff
FROM [$(TargetDb)].dbo.code AS t
JOIN [$(RefDb)].dbo.code AS s ON s.iyear = @ry AND t.ccode = s.ccode
WHERE t.iyear = @ty;

INSERT INTO [$(TargetDb)].dbo.code (
    cclass, cclass_engl, cclassany, cclassany_engl, ccode, ccode_name, ccode_engl, igrade, bproperty,
    cbook_type, cbook_type_engl, chelp, cexch_name, cmeasure, bperson, bcus, bsup, bdept, bitem, cass_item,
    br, be, cgather, bend, bexchange, bcash, bbank, bused, bd_c, dbegin, dend, itrans, bclose, cother, iotherused,
    bcDefine1, bcDefine2, bcDefine3, bcDefine4, bcDefine5, bcDefine6, bcDefine7, bcDefine8, bcDefine9, bcDefine10,
    iViewItem, bGCJS, bCashItem, bcDefine11, bcDefine12, bcDefine13, bcDefine14, bcDefine15, bcDefine16,
    bReport, cUserDefineType, iyear, dModifyDate, bparacc, bpardiff)
SELECT
    s.cclass, s.cclass_engl, s.cclassany, s.cclassany_engl, s.ccode, s.ccode_name, s.ccode_engl, s.igrade, s.bproperty,
    s.cbook_type, s.cbook_type_engl, s.chelp, s.cexch_name, s.cmeasure, s.bperson, s.bcus, s.bsup, s.bdept, s.bitem, s.cass_item,
    s.br, s.be, s.cgather, s.bend, s.bexchange, s.bcash, s.bbank, s.bused, s.bd_c, s.dbegin, s.dend, s.itrans, s.bclose, s.cother, s.iotherused,
    s.bcDefine1, s.bcDefine2, s.bcDefine3, s.bcDefine4, s.bcDefine5, s.bcDefine6, s.bcDefine7, s.bcDefine8, s.bcDefine9, s.bcDefine10,
    s.iViewItem, s.bGCJS, s.bCashItem, s.bcDefine11, s.bcDefine12, s.bcDefine13, s.bcDefine14, s.bcDefine15, s.bcDefine16,
    s.bReport, s.cUserDefineType, @ty, s.dModifyDate, s.bparacc, s.bpardiff
FROM [$(RefDb)].dbo.code AS s
WHERE s.iyear = @ry
  AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.code AS t WHERE t.iyear = @ty AND t.ccode = s.ccode);

-- 3.5 现金流量项目 fitemss98（不写自增列 I_id）
MERGE [$(TargetDb)].dbo.fitemss98 AS t
USING (SELECT citemcode, citemname, bclose, citemccode, iotherused, cDirection FROM [$(RefDb)].dbo.fitemss98) AS s
   ON t.citemcode = s.citemcode
WHEN MATCHED THEN UPDATE SET
    t.citemname = s.citemname, t.bclose = s.bclose, t.citemccode = s.citemccode,
    t.iotherused = s.iotherused, t.cDirection = s.cDirection
WHEN NOT MATCHED BY TARGET THEN INSERT (citemcode, citemname, bclose, citemccode, iotherused, cDirection)
    VALUES (s.citemcode, s.citemname, s.bclose, s.citemccode, s.iotherused, s.cDirection);

-- 3.6 人员字典（人员类别 / 在职状态 / 用工形式）：只补缺
INSERT INTO [$(TargetDb)].dbo.HR_CT000
    (ccodeID, vdescription, vsimpleName, vsimpleSpell, ilevels, cpCodeID, bsysFlag, bChildFlag, bhideFlag, vMemo)
SELECT s.ccodeID, s.vdescription, s.vsimpleName, s.vsimpleSpell, s.ilevels, s.cpCodeID, s.bsysFlag, s.bChildFlag, s.bhideFlag, s.vMemo
FROM [$(RefDb)].dbo.HR_CT000 AS s
WHERE NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.HR_CT000 AS t WHERE t.ccodeID = s.ccodeID);

INSERT INTO [$(TargetDb)].dbo.HR_CT416
    (ccodeID, vdescription, vsimpleName, vsimpleSpell, ilevels, cpCodeID, bsysFlag, bChildFlag, bhideFlag, vMemo)
SELECT s.ccodeID, s.vdescription, s.vsimpleName, s.vsimpleSpell, s.ilevels, s.cpCodeID, s.bsysFlag, s.bChildFlag, s.bhideFlag, s.vMemo
FROM [$(RefDb)].dbo.HR_CT416 AS s
WHERE NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.HR_CT416 AS t WHERE t.ccodeID = s.ccodeID);

INSERT INTO [$(TargetDb)].dbo.HR_CT803
    (ccodeID, vdescription, vsimpleName, vsimpleSpell, ilevels, cpCodeID, bsysFlag, bChildFlag, bhideFlag, vMemo)
SELECT s.ccodeID, s.vdescription, s.vsimpleName, s.vsimpleSpell, s.ilevels, s.cpCodeID, s.bsysFlag, s.bChildFlag, s.bhideFlag, s.vMemo
FROM [$(RefDb)].dbo.HR_CT803 AS s
WHERE NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.HR_CT803 AS t WHERE t.ccodeID = s.ccodeID);

-- 3.7 行业分类 TradeClass、生产订单类别 mom_motype（不写时间戳列；MoTypeId 不是自增列）
DELETE FROM [$(TargetDb)].dbo.TradeClass;
INSERT INTO [$(TargetDb)].dbo.TradeClass (cTradeCCode, cTradeCName, iTradeCGrade, bTradeCEnd)
SELECT cTradeCCode, cTradeCName, iTradeCGrade, bTradeCEnd FROM [$(RefDb)].dbo.TradeClass;

DELETE FROM [$(TargetDb)].dbo.mom_motype;
INSERT INTO [$(TargetDb)].dbo.mom_motype (MoTypeId, MotypeCode, Description, PlanFlag)
SELECT MoTypeId, MotypeCode, Description, PlanFlag FROM [$(RefDb)].dbo.mom_motype;

-- 3.8 档案自定义项设置 UserDef_Base / UserDef_Lang（只是设置，不是自定义项的值）。
--     UserDefDataEngineConfig 不复制：它的编号指向源账套自己的 UFMeta 元数据。
MERGE [$(TargetDb)].dbo.UserDef_Base AS t
USING (SELECT cID, cClass, cItem, cType, iMaxLen, iLength, bFixLength, bCheckVal, bInput, bArchive,
              iDataSource, cRelArchive, cRelField, cDefaultVal, iDecimalDigits, cDicDbName, CTableDesc,
              bMultiSel, cParentID, bIsUserDefAttachFile, cRefID, bIsUserDefPic
       FROM [$(RefDb)].dbo.UserDef_Base WHERE bArchive = 1) AS s
   ON t.cID = s.cID
WHEN MATCHED THEN UPDATE SET
    t.cClass = s.cClass, t.cItem = s.cItem, t.cType = s.cType, t.iMaxLen = s.iMaxLen, t.iLength = s.iLength,
    t.bFixLength = s.bFixLength, t.bCheckVal = s.bCheckVal, t.bInput = s.bInput, t.bArchive = s.bArchive,
    t.iDataSource = s.iDataSource, t.cRelArchive = s.cRelArchive, t.cRelField = s.cRelField,
    t.cDefaultVal = s.cDefaultVal, t.iDecimalDigits = s.iDecimalDigits, t.cDicDbName = s.cDicDbName,
    t.CTableDesc = s.CTableDesc, t.bMultiSel = s.bMultiSel, t.cParentID = s.cParentID,
    t.bIsUserDefAttachFile = s.bIsUserDefAttachFile, t.cRefID = s.cRefID, t.bIsUserDefPic = s.bIsUserDefPic
WHEN NOT MATCHED BY TARGET THEN INSERT
    (cID, cClass, cItem, cType, iMaxLen, iLength, bFixLength, bCheckVal, bInput, bArchive,
     iDataSource, cRelArchive, cRelField, cDefaultVal, iDecimalDigits, cDicDbName, CTableDesc,
     bMultiSel, cParentID, bIsUserDefAttachFile, cRefID, bIsUserDefPic)
VALUES
    (s.cID, s.cClass, s.cItem, s.cType, s.iMaxLen, s.iLength, s.bFixLength, s.bCheckVal, s.bInput, s.bArchive,
     s.iDataSource, s.cRelArchive, s.cRelField, s.cDefaultVal, s.iDecimalDigits, s.cDicDbName, s.CTableDesc,
     s.bMultiSel, s.cParentID, s.bIsUserDefAttachFile, s.cRefID, s.bIsUserDefPic);

INSERT INTO [$(TargetDb)].dbo.UserDef_Lang (cId, cItemName, cClassLang, cItemLang, cTypeLang, LocaleID)
SELECT s.cId, s.cItemName, s.cClassLang, s.cItemLang, s.cTypeLang, s.LocaleID
FROM [$(RefDb)].dbo.UserDef_Lang AS s
WHERE s.cId IN (SELECT cID FROM [$(RefDb)].dbo.UserDef_Base WHERE bArchive = 1)
  AND NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.UserDef_Lang AS t WHERE t.cId = s.cId AND t.LocaleID = s.LocaleID);

-- 3.9 凭证类别 dsign：换成源账套的方案（空账套只有预置的类别，还没有凭证）。不写自增列 i_id。
DELETE FROM [$(TargetDb)].dbo.dsign WHERE csign NOT IN (SELECT csign FROM [$(RefDb)].dbo.dsign);
INSERT INTO [$(TargetDb)].dbo.dsign (csign, isignseq, ctext, itype, iotherused, cOtherName, iAdjustFlag)
SELECT s.csign, s.isignseq, s.ctext, s.itype, s.iotherused, s.cOtherName, s.iAdjustFlag
FROM [$(RefDb)].dbo.dsign AS s
WHERE NOT EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.dsign AS t WHERE t.csign = s.csign);

-- 3.10 项目大类在目标账套不存在的科目，去掉项目辅助核算（否则制凭证时报错）
UPDATE [$(TargetDb)].dbo.code SET bitem = 0, cass_item = NULL
WHERE iyear = @ty AND bitem = 1
  AND ISNULL(cass_item, N'') NOT IN (SELECT citem_class FROM [$(TargetDb)].dbo.fitem);

COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：源 / 目标条数应相同或接近（目标多出的是建账预置的行）
SELECT N'GradeDef_Base' AS tbl,
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.GradeDef_Base WHERE iYear IN (0, $(RefYear))) AS n_ref,
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.GradeDef_Base WHERE iYear IN (0, $(TargetYear))) AS n_target
UNION ALL SELECT N'AccInformation',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.AccInformation), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.AccInformation)
UNION ALL SELECT N'code',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.code WHERE iyear = $(RefYear)),
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.code WHERE iyear = $(TargetYear))
UNION ALL SELECT N'fitemss98',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.fitemss98), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.fitemss98)
UNION ALL SELECT N'HR_CT000',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.HR_CT000), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.HR_CT000)
UNION ALL SELECT N'TradeClass',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.TradeClass), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.TradeClass)
UNION ALL SELECT N'mom_motype',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.mom_motype), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.mom_motype)
UNION ALL SELECT N'UserDef_Base(archive)',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.UserDef_Base WHERE bArchive = 1),
       (SELECT COUNT(*) FROM [$(TargetDb)].dbo.UserDef_Base WHERE bArchive = 1)
UNION ALL SELECT N'dsign',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.dsign), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.dsign);

SELECT cSysID, cName, cValue FROM [$(TargetDb)].dbo.AccInformation
WHERE (cSysID = N'SA' AND cName = N'bMustSO_ptxs') OR (cSysID = N'PU' AND cName = N'bPTHavePO')
   OR (cSysID = N'ST' AND cName IN (N'ballowAddnewVouch', N'bQuality'))
   OR (cSysID = N'GL' AND cName IN (N'bXJLL', N'bMakShtSort', N'bMasterSign', N'bProofSign'))
ORDER BY cSysID, cName;
SELECT KEYWORD, iYear, CODINGRULE FROM [$(TargetDb)].dbo.GradeDef_Base
WHERE KEYWORD IN (N'code', N'rd_style', N'settlestyle', N'districtclass') ORDER BY KEYWORD, iYear;
GO
