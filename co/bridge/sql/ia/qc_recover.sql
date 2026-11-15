-- U8 存货核算 取消期初记账。由桥在请求连接、请求事务里整批执行（不分 GO，openings/post module=ia action=unpost）；
-- 参数取自调用方先建好的 #ia_args，只用 y（存货核算启用年度）。
-- 只删第 0 期（iMonth=0）的期初数据、清第 0 期标志；不是 U8 的「取消开账」（那会删掉整个年度的数据并清启用日期）。
-- 拒绝时 THROW 50111–50116（中文原文给调用方），桥按编号转成 409。
SET NOCOUNT ON;

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @dIA nvarchar(10);
DECLARE @nSum0 int, @nSub0 int, @nItem0 int;

SELECT @dIA = cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'dIAStartDate';
IF NULLIF(LTRIM(@dIA), N'') IS NULL
  THROW 50111, N'存货核算未启用', 1;
IF YEAR(CONVERT(datetime, @dIA)) <> @y
  THROW 50112, N'期初年度不是存货核算启用年度', 1;
IF NOT EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=0)
  THROW 50113, N'总账期间表（GL_mend）没有启用年度的第 0 期，请在 U8 里检查会计期间', 1;
IF NOT EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=0 AND ISNULL(bflag_IA,0)=1)
  THROW 50114, N'存货核算期初未记账', 1;
-- 有月份结账、有日常数据时不能取消（U8 的取消开账会删掉整个年度的数据）。
IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod BETWEEN 1 AND 12 AND ISNULL(bflag_IA,0)=1)
  THROW 50115, N'存货核算已有月份结账，不能取消期初记账', 1;
IF EXISTS (SELECT 1 FROM IA_Summary WHERE iYear=@y AND iMonth<>0)
   OR EXISTS (SELECT 1 FROM IA_Subsidiary WHERE iYear=@y AND iMonth<>0)
  THROW 50116, N'存货核算本年已有日常数据（汇总或明细），不能取消期初记账', 1;

SELECT @nSum0 = COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=0;
SELECT @nSub0 = COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=0;
SELECT @nItem0 = COUNT(*) FROM IA_Summary_Item WHERE iYear=@y AND iMonth=0;

DELETE FROM IA_Summary_Item WHERE iYear=@y AND iMonth=0;
DELETE FROM IA_Subsidiary_Item WHERE iYear=@y AND iMonth=0;
DELETE FROM IA_Subsidiary WHERE iYear=@y AND iMonth=0;
DELETE FROM IA_Summary WHERE iYear=@y AND iMonth=0;
DELETE FROM IA_Subsidiary_QC_Item;
DELETE FROM IA_Subsidiary_QC;
-- IA_sp_QCAss 写的先进先出期初批次（cInVouType='34'）。
DELETE FROM IA_ValuationAss WHERE ISNULL(cInVouType, N'')=N'34' AND OutID IS NULL;

-- 只清第 0 期；第 1–12 期的标志不动（1 月启用时记账也只置了第 0 期）。
UPDATE GL_mend SET bflag_IA=0 WHERE iyear=@y AND iperiod=0;
UPDATE AccInformation SET cValue=N'False' WHERE cSysID=N'IA' AND cName=N'bQCInput';
DELETE FROM AccInformation_Year WHERE cSysID=N'IA' AND cName IN (N'OpenAccFlag', N'IsFxAccount') AND iYear=@y;
DELETE FROM IA_HisOption WHERE iYear=@y;
DELETE FROM IA_HisWarehouse WHERE iYear=@y;
DELETE FROM IA_HisInventory WHERE iYear=@y;
DELETE FROM IA_HisInvCheckFree WHERE iYear=@y;

IF OBJECT_ID(N'IA_TMPQC_DataSource', N'U') IS NOT NULL DROP TABLE IA_TMPQC_DataSource;
IF OBJECT_ID(N'IA_TMP_Sum', N'U') IS NOT NULL DROP TABLE IA_TMP_Sum;
IF OBJECT_ID(N'IA_TMP_Sum1', N'U') IS NOT NULL DROP TABLE IA_TMP_Sum1;
IF OBJECT_ID(N'IA_TMP_Sum33', N'U') IS NOT NULL DROP TABLE IA_TMP_Sum33;

SELECT N'summary_m0_deleted' k, @nSum0 n
UNION ALL SELECT N'subsidiary_m0_deleted', @nSub0
UNION ALL SELECT N'summary_item_m0_deleted', @nItem0
UNION ALL SELECT N'summary_m0_left', (SELECT COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=0)
UNION ALL SELECT N'gl_mend_p0', CONVERT(int, (SELECT ISNULL(bflag_IA,0) FROM GL_mend WHERE iyear=@y AND iperiod=0))
UNION ALL SELECT N'bQCInput_true', CASE WHEN EXISTS (
  SELECT 1 FROM AccInformation WHERE cSysID=N'IA' AND cName=N'bQCInput' AND cValue IN (N'True',N'TRUE',N'true',N'1')) THEN 1 ELSE 0 END;
