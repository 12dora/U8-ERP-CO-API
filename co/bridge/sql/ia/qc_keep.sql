-- U8 存货核算 期初记账（期初余额「取数」+「记账」）。由桥在请求连接、请求事务里整批执行（不分 GO，openings/post module=ia）；
-- 参数取自调用方先建好的 #ia_args（y、m、keep_date、accounter、on_uncosted）：y 是存货核算启用年度，m 不用（启用月份读 dIAStartDate），
-- keep_date 是记账日期（缺省启用日期），accounter 是登录操作员姓名（写 IA_Subsidiary.cAccounter / cMaker）。
-- 拒绝时 THROW 50101–50106（中文原文给调用方），桥按编号转成 409。
-- 依据：IA_OpenAccount.ClsQc 的 AccSummary / AccQC、IA_AddQCArea / IA_sp_QCAss 与 U8 期初记账存储过程一致；在测试账套回滚事务核对。
-- 不执行 IA_AddQCArea / IA_CreateTmpTable（会 DROP 桥 ia/post 用的模板表 IA_Data_Subsidiary / IA_AccountArea），
-- 不执行 IA_sp_QCAss（它调 IA_AccTmpTable 会 DROP IA_AutoJustIn；全月平均法下计价辅助表本来为空，先进先出行数在末尾计数里）。
SET NOCOUNT ON;

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @cDate nvarchar(10); SELECT @cDate = keep_date FROM #ia_args;
DECLARE @cAccounter nvarchar(20); SELECT @cAccounter = accounter FROM #ia_args;

DECLARE @dIA nvarchar(10), @dST nvarchar(10), @hsfs nvarchar(40);
DECLARE @startMonth tinyint, @dKeep datetime;
DECLARE @n34 int, @nSub int, @nSum int, @nFifo int;

SELECT @dIA = cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'dIAStartDate';
SELECT @dST = cValue FROM AccInformation WHERE cSysID=N'ST' AND cName=N'dSTStartDate';
SELECT @hsfs = cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'cValueStyle';
SET @hsfs = ISNULL(@hsfs, N'按仓库核算');

IF NULLIF(LTRIM(@dIA), N'') IS NULL
  THROW 50101, N'存货核算未启用', 1;
IF YEAR(CONVERT(datetime, @dIA)) <> @y
  THROW 50102, N'期初年度不是存货核算启用年度', 1;
IF NOT EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=0)
  THROW 50103, N'总账期间表（GL_mend）没有启用年度的第 0 期，请在 U8 里检查会计期间', 1;
IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=0 AND ISNULL(bflag_IA,0)=1)
  THROW 50104, N'存货核算期初已记账', 1;
IF NULLIF(LTRIM(@dST), N'') IS NULL
  THROW 50105, N'库存管理未启用，不能从库存期初结存取数', 1;
-- 同 ClsQc.CherkSameDate：库存与存货核算启用日期不同时 U8 不让取数。
IF CONVERT(date, @dST) <> CONVERT(date, @dIA)
  THROW 50106, N'库存管理与存货核算的启用日期不一致，不能期初取数', 1;

SET @startMonth = MONTH(CONVERT(datetime, @dIA));
IF NULLIF(LTRIM(@cDate), N'') IS NULL SET @cDate = @dIA;
SET @dKeep = CONVERT(datetime, CONVERT(date, @cDate, 23));
-- accounter 由桥取登录操作员姓名（空则桥先拒绝），这里只防 NULL。
SET @cAccounter = ISNULL(@cAccounter, N'');

-- 重新取数：清掉以前留下的第 0 期期初行（例如只置了 bQCInput、没有置标志的半截记账）。
DELETE FROM IA_Summary WHERE iYear=@y AND iMonth=0;
DELETE FROM IA_Subsidiary WHERE iYear=@y AND iMonth=0 AND cVouType=N'34';

-- U8 界面的取数工作表（SELECT IA_Subsidiary.* INTO IA_TMPQC_DataSource），结构同 IA_Subsidiary。
IF OBJECT_ID(N'IA_TMPQC_DataSource', N'U') IS NOT NULL DROP TABLE IA_TMPQC_DataSource;
SELECT * INTO IA_TMPQC_DataSource FROM IA_Subsidiary WHERE 1=0;

-- 取数：只取已审核（cHandler 非空）的期初结存单（34）。金额取表体 iPrice / iUnitCost，都为空记 0（U8 里是取数后手工录入）。
INSERT INTO IA_TMPQC_DataSource (
  bRdFlag, cVouType, cVouCode, ID, dVouDate, dKeepDate, iYear, iMonth,
  cWhCode, cDepCode, cInvCode, cHandler, cMaker, cAccounter,
  iAInQuantity, iInCost, iAInPrice, bFlag, bMoneyFlag, bSale, cPZID,
  cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10, cBatchia, cBatchCode)
SELECT
  1, N'34', h.cCode, b.AutoID, h.dDate, @dKeep, @y, 0,
  h.cWhCode, h.cDepCode, b.cInvCode, h.cHandler, @cAccounter, @cAccounter,
  b.iQuantity,
  CASE
    WHEN b.iUnitCost IS NOT NULL THEN b.iUnitCost
    WHEN ISNULL(b.iQuantity,0)<>0 AND b.iPrice IS NOT NULL THEN CAST(b.iPrice AS float)/CAST(b.iQuantity AS float)
    ELSE 0 END,
  CASE
    WHEN b.iPrice IS NOT NULL THEN b.iPrice
    ELSE CAST(ROUND(CAST(ISNULL(b.iQuantity,0) AS float)*CAST(ISNULL(b.iUnitCost,0) AS float), 2) AS money) END,
  0, 0, 0, N'0',
  b.cFree1,b.cFree2,b.cFree3,b.cFree4,b.cFree5,b.cFree6,b.cFree7,b.cFree8,b.cFree9,b.cFree10,
  b.cBatch, NULL
FROM rdrecord34 h
INNER JOIN rdrecords34 b ON b.id = h.id
INNER JOIN Inventory inv ON inv.cInvCode = b.cInvCode
WHERE ISNULL(h.cHandler, N'') <> N''
  AND ISNULL(inv.bService, 0) = 0
  AND LEFT(ISNULL(h.cBusType, N''), 2) <> N'直运';

INSERT INTO IA_Subsidiary (
  bRdFlag, cVouType, cVouCode, ID, dVouDate, dKeepDate, iYear, iMonth,
  cWhCode, cDepCode, cInvCode, cHandler, cMaker, cAccounter,
  iAInQuantity, iInCost, iAInPrice, bFlag, bMoneyFlag, bSale, cPZID,
  cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10, cBatchia)
SELECT
  bRdFlag, cVouType, cVouCode, ID, dVouDate, dKeepDate, iYear, iMonth,
  cWhCode, cDepCode, cInvCode, cHandler, cMaker, cAccounter,
  iAInQuantity, iInCost, iAInPrice, bFlag, bMoneyFlag, bSale, cPZID,
  cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10, cBatchia
FROM IA_TMPQC_DataSource;

SELECT @n34 = COUNT(*) FROM IA_TMPQC_DataSource;
SELECT @nSub = COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=0 AND cVouType=N'34';

-- AccSummary 的工作表 IA_TMP_Sum。用 CREATE + INSERT：三个分支各写 SELECT INTO 会在编译时报表名重复。
IF OBJECT_ID(N'IA_TMP_Sum', N'U') IS NOT NULL DROP TABLE IA_TMP_Sum;
CREATE TABLE IA_TMP_Sum (
  cWhCode nvarchar(10) NULL, cInvCode nvarchar(60) NULL, cDepCode nvarchar(12) NULL,
  cfree1 nvarchar(20) NULL, cfree2 nvarchar(20) NULL, cfree3 nvarchar(20) NULL, cfree4 nvarchar(20) NULL, cfree5 nvarchar(20) NULL,
  cfree6 nvarchar(20) NULL, cfree7 nvarchar(20) NULL, cfree8 nvarchar(20) NULL, cfree9 nvarchar(20) NULL, cfree10 nvarchar(20) NULL,
  cBatchia nvarchar(100) NULL, iMonth tinyint NULL, iNum decimal(38,10) NULL, iMoney money NULL
);

IF @hsfs = N'按存货核算'
  INSERT INTO IA_TMP_Sum (cWhCode, cInvCode, cDepCode, cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia, iMonth, iNum, iMoney)
  SELECT
    NULL, ia.cInvCode, NULL,
    CASE WHEN inv.bCheckFree1=1 THEN ia.cFree1 END, CASE WHEN inv.bCheckFree2=1 THEN ia.cFree2 END,
    CASE WHEN inv.bCheckFree3=1 THEN ia.cFree3 END, CASE WHEN inv.bCheckFree4=1 THEN ia.cFree4 END,
    CASE WHEN inv.bCheckFree5=1 THEN ia.cFree5 END, CASE WHEN inv.bCheckFree6=1 THEN ia.cFree6 END,
    CASE WHEN inv.bCheckFree7=1 THEN ia.cFree7 END, CASE WHEN inv.bCheckFree8=1 THEN ia.cFree8 END,
    CASE WHEN inv.bCheckFree9=1 THEN ia.cFree9 END, CASE WHEN inv.bCheckFree10=1 THEN ia.cFree10 END,
    CASE WHEN inv.bCheckBatch=1 AND inv.cValueType IN (N'全月平均法',N'移动平均法',N'先进先出法',N'后进先出法') THEN ia.cBatchia END,
    0,
    SUM(ISNULL(ia.iAInQuantity,0)-ISNULL(ia.iAOutQuantity,0)),
    SUM(ISNULL(ia.iAInPrice,0)-ISNULL(ia.iAOutPrice,0))
  FROM IA_Subsidiary ia
  INNER JOIN Inventory inv ON inv.cInvCode = ia.cInvCode
  WHERE ia.iYear=@y AND ia.iMonth=0 AND ia.cVouType=N'34'
  GROUP BY ia.cInvCode,
    CASE WHEN inv.bCheckFree1=1 THEN ia.cFree1 END, CASE WHEN inv.bCheckFree2=1 THEN ia.cFree2 END,
    CASE WHEN inv.bCheckFree3=1 THEN ia.cFree3 END, CASE WHEN inv.bCheckFree4=1 THEN ia.cFree4 END,
    CASE WHEN inv.bCheckFree5=1 THEN ia.cFree5 END, CASE WHEN inv.bCheckFree6=1 THEN ia.cFree6 END,
    CASE WHEN inv.bCheckFree7=1 THEN ia.cFree7 END, CASE WHEN inv.bCheckFree8=1 THEN ia.cFree8 END,
    CASE WHEN inv.bCheckFree9=1 THEN ia.cFree9 END, CASE WHEN inv.bCheckFree10=1 THEN ia.cFree10 END,
    CASE WHEN inv.bCheckBatch=1 AND inv.cValueType IN (N'全月平均法',N'移动平均法',N'先进先出法',N'后进先出法') THEN ia.cBatchia END;
ELSE IF @hsfs = N'按部门核算'
  INSERT INTO IA_TMP_Sum (cWhCode, cInvCode, cDepCode, cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia, iMonth, iNum, iMoney)
  SELECT
    NULL, ia.cInvCode, ia.cDepCode,
    CASE WHEN inv.bCheckFree1=1 THEN ia.cFree1 END, CASE WHEN inv.bCheckFree2=1 THEN ia.cFree2 END,
    CASE WHEN inv.bCheckFree3=1 THEN ia.cFree3 END, CASE WHEN inv.bCheckFree4=1 THEN ia.cFree4 END,
    CASE WHEN inv.bCheckFree5=1 THEN ia.cFree5 END, CASE WHEN inv.bCheckFree6=1 THEN ia.cFree6 END,
    CASE WHEN inv.bCheckFree7=1 THEN ia.cFree7 END, CASE WHEN inv.bCheckFree8=1 THEN ia.cFree8 END,
    CASE WHEN inv.bCheckFree9=1 THEN ia.cFree9 END, CASE WHEN inv.bCheckFree10=1 THEN ia.cFree10 END,
    CASE WHEN inv.bCheckBatch=1 THEN ia.cBatchia END,
    0,
    SUM(ISNULL(ia.iAInQuantity,0)-ISNULL(ia.iAOutQuantity,0)),
    SUM(ISNULL(ia.iAInPrice,0)-ISNULL(ia.iAOutPrice,0))
  FROM IA_Subsidiary ia
  INNER JOIN Inventory inv ON inv.cInvCode = ia.cInvCode
  WHERE ia.iYear=@y AND ia.iMonth=0 AND ia.cVouType=N'34'
  GROUP BY ia.cDepCode, ia.cInvCode,
    CASE WHEN inv.bCheckFree1=1 THEN ia.cFree1 END, CASE WHEN inv.bCheckFree2=1 THEN ia.cFree2 END,
    CASE WHEN inv.bCheckFree3=1 THEN ia.cFree3 END, CASE WHEN inv.bCheckFree4=1 THEN ia.cFree4 END,
    CASE WHEN inv.bCheckFree5=1 THEN ia.cFree5 END, CASE WHEN inv.bCheckFree6=1 THEN ia.cFree6 END,
    CASE WHEN inv.bCheckFree7=1 THEN ia.cFree7 END, CASE WHEN inv.bCheckFree8=1 THEN ia.cFree8 END,
    CASE WHEN inv.bCheckFree9=1 THEN ia.cFree9 END, CASE WHEN inv.bCheckFree10=1 THEN ia.cFree10 END,
    CASE WHEN inv.bCheckBatch=1 THEN ia.cBatchia END;
ELSE
  -- 按仓库核算：存货管批次、且仓库计价方式是这四种之一时才按批次汇总。
  INSERT INTO IA_TMP_Sum (cWhCode, cInvCode, cDepCode, cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia, iMonth, iNum, iMoney)
  SELECT
    ia.cWhCode, ia.cInvCode, NULL,
    CASE WHEN inv.bCheckFree1=1 THEN ia.cFree1 END, CASE WHEN inv.bCheckFree2=1 THEN ia.cFree2 END,
    CASE WHEN inv.bCheckFree3=1 THEN ia.cFree3 END, CASE WHEN inv.bCheckFree4=1 THEN ia.cFree4 END,
    CASE WHEN inv.bCheckFree5=1 THEN ia.cFree5 END, CASE WHEN inv.bCheckFree6=1 THEN ia.cFree6 END,
    CASE WHEN inv.bCheckFree7=1 THEN ia.cFree7 END, CASE WHEN inv.bCheckFree8=1 THEN ia.cFree8 END,
    CASE WHEN inv.bCheckFree9=1 THEN ia.cFree9 END, CASE WHEN inv.bCheckFree10=1 THEN ia.cFree10 END,
    CASE WHEN inv.bCheckBatch=1 AND war.cWhValueStyle IN (N'全月平均法',N'移动平均法',N'先进先出法',N'后进先出法')
         AND LEFT(ISNULL(ia.cBusType,N''),2)<>N'直运' THEN ia.cBatchia END,
    0,
    SUM(ISNULL(ia.iAInQuantity,0)-ISNULL(ia.iAOutQuantity,0)),
    SUM(ISNULL(ia.iAInPrice,0)-ISNULL(ia.iAOutPrice,0))
  FROM IA_Subsidiary ia
  INNER JOIN Inventory inv ON inv.cInvCode = ia.cInvCode
  LEFT JOIN Warehouse war ON war.cWhCode = ia.cWhCode
  WHERE ia.iYear=@y AND ia.iMonth=0 AND ia.cVouType=N'34'
  GROUP BY ia.cWhCode, ia.cInvCode,
    CASE WHEN inv.bCheckFree1=1 THEN ia.cFree1 END, CASE WHEN inv.bCheckFree2=1 THEN ia.cFree2 END,
    CASE WHEN inv.bCheckFree3=1 THEN ia.cFree3 END, CASE WHEN inv.bCheckFree4=1 THEN ia.cFree4 END,
    CASE WHEN inv.bCheckFree5=1 THEN ia.cFree5 END, CASE WHEN inv.bCheckFree6=1 THEN ia.cFree6 END,
    CASE WHEN inv.bCheckFree7=1 THEN ia.cFree7 END, CASE WHEN inv.bCheckFree8=1 THEN ia.cFree8 END,
    CASE WHEN inv.bCheckFree9=1 THEN ia.cFree9 END, CASE WHEN inv.bCheckFree10=1 THEN ia.cFree10 END,
    CASE WHEN inv.bCheckBatch=1 AND war.cWhValueStyle IN (N'全月平均法',N'移动平均法',N'先进先出法',N'后进先出法')
         AND LEFT(ISNULL(ia.cBusType,N''),2)<>N'直运' THEN ia.cBatchia END;

INSERT INTO IA_Summary (
  cWhCode, cInvCode, cDepCode, iYear, iMonth,
  iINum, iONum, iNum, iIMoney, iOMoney, iMoney,
  iUnitPrice, iDifRate, iIDif, iODif, iDif, fMinInCost, fMaxInCost, iDirect,
  cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10, cbatchia, iperiod, dCreateDate)
SELECT
  a.cWhCode, a.cInvCode, a.cDepCode, @y, 0,
  NULL, NULL, a.iNum, NULL, NULL, a.iMoney,
  CASE WHEN ISNULL(a.iNum,0)=0 THEN 0 ELSE a.iMoney/a.iNum END,
  0, NULL, NULL, 0, 0, 0, 0,
  a.cfree1,a.cfree2,a.cfree3,a.cfree4,a.cfree5,a.cfree6,a.cfree7,a.cfree8,a.cfree9,a.cfree10, a.cBatchia,
  0, GETDATE()
FROM IA_TMP_Sum a WHERE a.iMonth=0;

SELECT @nSum = COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=0;

-- AccQC 的收尾：置 bQCInput、启用月份之前各期（iPeriod<启用月份，1 月启用时只有第 0 期）bflag_IA=1，写年度标志和历史设置。
UPDATE AccInformation SET cValue=N'True' WHERE cSysID=N'IA' AND cName=N'bQCInput';
UPDATE GL_mend SET bflag_IA=1 WHERE iyear=@y AND iperiod<@startMonth;

IF NOT EXISTS (SELECT 1 FROM AccInformation_Year WHERE cSysID=N'IA' AND cName=N'OpenAccFlag' AND iYear=@y)
  INSERT INTO AccInformation_Year (cSysID, cName, cCaption, cValue, cDefault, iYear, bVisible, bEnable)
  VALUES (N'IA', N'OpenAccFlag', N'存货核算开账标志_0未开帐_1开账_2调整开账', N'1', N'0', @y, 1, 1);
ELSE
  UPDATE AccInformation_Year SET cValue=N'1' WHERE cSysID=N'IA' AND cName=N'OpenAccFlag' AND iYear=@y;

IF NOT EXISTS (SELECT 1 FROM AccInformation_Year WHERE cSysID=N'IA' AND cName=N'IsFxAccount' AND iYear=@y)
  INSERT INTO AccInformation_Year (cSysID, cID, cName, cCaption, cType, iYear, cValue, cDefault, bVisible, bEnable)
  VALUES (N'IA', N'01', N'IsFxAccount', N'分项期初是否记账', N'integer', @y, N'1', N'0', 1, 1);

IF NOT EXISTS (SELECT 1 FROM IA_HisOption WHERE iYear=@y AND cName=N'cValueStyle')
  INSERT INTO IA_HisOption (csysID, cName, cCaption, cValue, iYear, cCreator, dCreatedDate)
  VALUES (N'IA', N'cValueStyle', N'存货核算方式', @hsfs, @y, @cAccounter, GETDATE());
IF NOT EXISTS (SELECT 1 FROM IA_HisWarehouse WHERE iYear=@y)
  INSERT INTO IA_HisWarehouse (cWhCode, cWhValueStyle, cDepCode, iYear)
  SELECT cWhCode, cWhValueStyle, cDepCode, @y FROM Warehouse;
IF NOT EXISTS (SELECT 1 FROM IA_HisInventory WHERE iYear=@y)
  INSERT INTO IA_HisInventory (cInvCode, cValueType, bFree1, bFree2, bFree3, bFree4, bFree5, bFree6, bFree7, bFree8, bFree9, bFree10,
    bCheckFree1, bCheckFree2, bCheckFree3, bCheckFree4, bCheckFree5, bCheckFree6, bCheckFree7, bCheckFree8, bCheckFree9, bCheckFree10,
    iInvRCost, iYear, bCheckBatch)
  SELECT cInvCode, cValueType, bFree1, bFree2, bFree3, bFree4, bFree5, bFree6, bFree7, bFree8, bFree9, bFree10,
    bCheckFree1, bCheckFree2, bCheckFree3, bCheckFree4, bCheckFree5, bCheckFree6, bCheckFree7, bCheckFree8, bCheckFree9, bCheckFree10,
    iInvRCost, @y, bCheckBatch
  FROM Inventory;

SELECT @nFifo = COUNT(*) FROM IA_TMPQC_DataSource t
  LEFT JOIN Warehouse w ON w.cWhCode=t.cWhCode
  LEFT JOIN Inventory inv ON inv.cInvCode=t.cInvCode
  WHERE (CASE WHEN @hsfs=N'按存货核算' THEN inv.cValueType ELSE w.cWhValueStyle END) IN (N'先进先出法',N'后进先出法');

IF OBJECT_ID(N'IA_TMPQC_DataSource', N'U') IS NOT NULL DROP TABLE IA_TMPQC_DataSource;
IF OBJECT_ID(N'IA_TMP_Sum', N'U') IS NOT NULL DROP TABLE IA_TMP_Sum;

SELECT N'st34_verified' k, @n34 n
UNION ALL SELECT N'subsidiary_m0_34', @nSub
UNION ALL SELECT N'summary_m0', @nSum
UNION ALL SELECT N'fifo_lines_qcass_skipped', @nFifo
UNION ALL SELECT N'gl_mend_p0', CONVERT(int, (SELECT ISNULL(bflag_IA,0) FROM GL_mend WHERE iyear=@y AND iperiod=0))
UNION ALL SELECT N'gl_mend_lt_start', (SELECT COUNT(*) FROM GL_mend WHERE iyear=@y AND iperiod<@startMonth AND ISNULL(bflag_IA,0)=1);
