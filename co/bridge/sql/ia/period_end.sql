-- U8 存货核算 期末处理。由桥在请求连接、请求事务里整批执行（不分 GO）；参数取自调用方先建好的 #ia_args
-- （y、m、keep_date、accounter、on_uncosted）。拒绝时 THROW 50000–50099，桥按编号转成 409。
-- 依据：测试账套实测（回滚事务），见 docs/api-reference.md「存货核算记账与期末处理」。
-- 期末处理. ONE batch. No GO.
-- 802: 全月平均 / 取本月成本 → @bRollCall=1. Skip 计划价/标准成本/FIFO/蓝字回冲.
SET NOCOUNT ON;
-- U8 过程返回的 @errmsg 用 THROW 50000 抛出（16 级 RAISERROR 不中止批，后面的语句还会接着执行）。
DECLARE @raise nvarchar(2048);

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @m tinyint; SELECT @m = m FROM #ia_args;
DECLARE @cDate nvarchar(10); SELECT @cDate = keep_date FROM #ia_args;
DECLARE @cAccounter nvarchar(20); SELECT @cAccounter = accounter FROM #ia_args;
DECLARE @cEDate nvarchar(10);
DECLARE @dKeep datetime;
DECLARE @errmsg nvarchar(200);

SET @cEDate = CONVERT(nvarchar(10), EOMONTH(DATEFROMPARTS(@y,@m,1)), 23);
IF NULLIF(LTRIM(@cDate),N'') IS NULL SET @cDate = @cEDate;
SET @dKeep = CONVERT(datetime, CONVERT(date, @cDate, 23));
SET @errmsg = NULL;

IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=@m AND ISNULL(bflag_IA,0)=1)
  THROW 50020, N'IA month already closed (bflag_IA=1)', 1;
-- 没有单据的月份也要期末处理：上月结账留下的期初结存（IA_Summary）在本月要确认为 iPeriod=1，才能结账。
IF NOT EXISTS (SELECT 1 FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m)
   AND NOT EXISTS (SELECT 1 FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iDirect,0)=0)
  THROW 50021, N'no IA data for this month — run ia/post first', 1;

IF EXISTS (SELECT 1 FROM sysobjects WHERE name=N'Ia_PerDealWhDep' AND xtype=N'U') DROP TABLE Ia_PerDealWhDep;
CREATE TABLE Ia_PerDealWhDep(cWhDepCode nvarchar(30));
CREATE INDEX ix_Ia_PerDealWhDep_cWhDepCode ON Ia_PerDealWhDep(cWhDepCode);

IF EXISTS (SELECT 1 FROM sysobjects WHERE name=N'IA_PerdealInventory' AND xtype=N'U') DROP TABLE IA_PerdealInventory;
SELECT cinvcode INTO IA_PerdealInventory FROM Inventory WHERE 1=0;
CREATE INDEX ix_IA_PerdealInventory_cInvCode ON IA_PerdealInventory(cinvcode);

INSERT INTO IA_PerdealInventory(cinvcode)
SELECT DISTINCT x.cInvCode FROM (
  SELECT cInvCode FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iDirect,0)=0 AND ISNULL(cInvCode,N'')<>N''
  UNION
  SELECT cInvCode FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m AND ISNULL(cInvCode,N'')<>N''
) x;

INSERT INTO Ia_PerDealWhDep(cWhDepCode)
SELECT DISTINCT x.cWhCode FROM (
  SELECT cWhCode FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iDirect,0)=0 AND ISNULL(cWhCode,N'')<>N''
  UNION
  SELECT cWhCode FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m AND ISNULL(cWhCode,N'')<>N''
) x;

IF OBJECT_ID(N'tempdb..#IA_Summary') IS NOT NULL DROP TABLE #IA_Summary;
IF OBJECT_ID(N'tempdb..#IA_AccountData') IS NOT NULL DROP TABLE #IA_AccountData;
IF OBJECT_ID(N'tempdb..#IA_AccountArea') IS NOT NULL DROP TABLE #IA_AccountArea;

SELECT * INTO #IA_Summary FROM IA_Summary WHERE 1=0;
SET IDENTITY_INSERT #IA_Summary ON;
INSERT INTO #IA_Summary(
  AutoID,cWhCode,cInvCode,cDepCode,iMonth,iINum,iONum,iNum,iIMoney,iOMoney,iMoney,
  iUnitPrice,iDifRate,iIDif,iODif,iDif,fMinInCost,fMaxInCost,iDirect,
  cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10,
  bManual,iperiod,iTrans,fLaborStdCostE,fManuFixStdCostE,fManuVarStdCostE,fOmCostE,
  iYear,dCreateDate,cbatchia,iAbsNum,iAbsMoney,iAbsINum,iAbsIMoney,iAbsONum,iAbsOMoney)
SELECT
  AutoID,cWhCode,cInvCode,cDepCode,iMonth,iINum,iONum,iNum,iIMoney,iOMoney,iMoney,
  iUnitPrice,iDifRate,iIDif,iODif,iDif,fMinInCost,fMaxInCost,iDirect,
  cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10,
  bManual,iperiod,iTrans,fLaborStdCostE,fManuFixStdCostE,fManuVarStdCostE,fOmCostE,
  iYear,dCreateDate,cbatchia,iAbsNum,iAbsMoney,iAbsINum,iAbsIMoney,iAbsONum,iAbsOMoney
FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iDirect,0)=0;
SET IDENTITY_INSERT #IA_Summary OFF;

-- 按 U8 模板表结构显式建表（新建账套可能没有模板表）。
CREATE TABLE #IA_AccountArea (
  [areaID] int IDENTITY(1,1) NOT NULL,
  [BakID] int NULL,
  [cvoutype] nvarchar(4) NULL,
  [id] int NULL,
  [cvoucode] nvarchar(30) NULL,
  [ddate] datetime NULL,
  [cbustype] nvarchar(30) NULL,
  [brdflag] tinyint NULL,
  [cwhcode] nvarchar(30) NULL,
  [cdepcode] nvarchar(30) NULL,
  [cinvcode] nvarchar(60) NULL,
  [cfree1] nvarchar(60) NULL,
  [cfree2] nvarchar(60) NULL,
  [cfree3] nvarchar(60) NULL,
  [cfree4] nvarchar(60) NULL,
  [cfree5] nvarchar(60) NULL,
  [cfree6] nvarchar(60) NULL,
  [cfree7] nvarchar(60) NULL,
  [cfree8] nvarchar(60) NULL,
  [cfree9] nvarchar(60) NULL,
  [cfree10] nvarchar(60) NULL,
  [cBatchia] nvarchar(100) NULL,
  [iquantity] float NULL,
  [AutoID] int NULL,
  [iunitprice] float NULL,
  [iprice] decimal(38,2) NULL,
  [cValueStyle] nvarchar(20) NULL,
  [iDebit] decimal(38,2) NULL,
  [iCredit] decimal(38,2) NULL,
  [bMoneyFlag] tinyint NULL,
  [bManual] tinyint NULL,
  [bSale] tinyint NULL,
  [bFlag] int NULL,
  [cBusCode] nvarchar(30) NULL,
  [imaterialfee] decimal(38,2) NULL,
  [psvsid] int NULL,
  [ufts] money NULL,
  [ipzid] int NULL
);
ALTER TABLE #IA_AccountArea ADD GUID uniqueidentifier NULL, MoneySrc smallint NULL, iCostSrc smallint NULL;

CREATE TABLE #IA_AccountData(
  AutoID int IDENTITY(1,1) NOT NULL,
  ID int NULL, cVouType nvarchar(4) NULL, bUpdateCost tinyint NULL,
  iquantity float NULL, iUnitCost float NULL, iPrice decimal(38,2) NULL,
  faCost decimal(34,10) NULL, iAPrice money NULL,
  iProcessFee money NULL, iProcessCost money NULL,
  iMaterialFee decimal(38,2) NULL, iSMaterialFee money NULL,
  iMatSettleState int NULL, dmsdate datetime NULL,
  bOMUpdate tinyint NULL, bRdUpdated tinyint NULL, bNoItemUsed tinyint NULL
);

-- 期末处理链还要这几张会话表（空表即可）。
IF OBJECT_ID(N'tempdb..#Ia_ManualInputcost') IS NOT NULL DROP TABLE #Ia_ManualInputcost;
IF OBJECT_ID(N'tempdb..#IA_ValuationAss') IS NOT NULL DROP TABLE #IA_ValuationAss;
IF OBJECT_ID(N'tempdb..#Rdrecords') IS NOT NULL DROP TABLE #Rdrecords;
IF OBJECT_ID(N'tempdb..#Inventory') IS NOT NULL DROP TABLE #Inventory;
-- 按 U8 模板表结构显式建表（新建账套可能没有模板表）。
CREATE TABLE #Ia_ManualInputcost (
  [AutoID] int IDENTITY(1,1) NOT NULL,
  [bAcc] tinyint NULL,
  [cWhDepCode] nvarchar(12) NULL,
  [cInvCode] nvarchar(20) NULL,
  [cFree1] nvarchar(20) NULL,
  [cFree2] nvarchar(20) NULL,
  [cFree3] nvarchar(20) NULL,
  [cFree4] nvarchar(20) NULL,
  [cFree5] nvarchar(20) NULL,
  [cFree6] nvarchar(20) NULL,
  [cFree7] nvarchar(20) NULL,
  [cFree8] nvarchar(20) NULL,
  [cFree9] nvarchar(20) NULL,
  [cFree10] nvarchar(20) NULL,
  [Cost] float NULL
);
ALTER TABLE #Ia_ManualInputcost ADD cBatchia nvarchar(100) NULL;

-- 按 U8 模板表结构显式建表（新建账套可能没有模板表）。
CREATE TABLE #IA_ValuationAss (
  [AutoID] int IDENTITY(1,1) NOT NULL,
  [BakID] int NULL,
  [cIVouCode] nvarchar(30) NULL,
  [cWhCode] nvarchar(10) NULL,
  [cInvCode] nvarchar(60) NULL,
  [cDepCode] nvarchar(12) NULL,
  [dIDate] datetime NULL,
  [InID] int NULL,
  [bJustInFlag] bit NULL,
  [dIKeepDate] datetime NULL,
  [iIQuantity] decimal(30,10) NULL,
  [iICost] decimal(30,10) NULL,
  [iIPrice] money NULL,
  [cOVouCode] nvarchar(30) NULL,
  [dODate] datetime NULL,
  [OutID] int NULL,
  [bJustOutFlag] bit NULL,
  [dOKeepDate] datetime NULL,
  [iOQuantity] decimal(30,10) NULL,
  [iOCost] decimal(30,10) NULL,
  [iOPrice] money NULL,
  [SubID] int NULL,
  [bFlag] bit NULL,
  [cflag] nvarchar(10) NULL,
  [iJSDID] int NULL,
  [bMoneyFlag] tinyint NULL,
  [cFree1] nvarchar(20) NULL,
  [cFree2] nvarchar(20) NULL,
  [cFree3] nvarchar(20) NULL,
  [cFree4] nvarchar(20) NULL,
  [cFree5] nvarchar(20) NULL,
  [cFree6] nvarchar(20) NULL,
  [cFree7] nvarchar(20) NULL,
  [cFree8] nvarchar(20) NULL,
  [cFree9] nvarchar(20) NULL,
  [cFree10] nvarchar(20) NULL,
  [cInVouType] nvarchar(4) NULL,
  [cOutVouType] nvarchar(4) NULL,
  [OutBakID] int NULL,
  [AccID] int NULL,
  [iPreYear] smallint NULL,
  [cbatchia] nvarchar(60) NULL
);
ALTER TABLE #IA_ValuationAss ADD iOldAutoID int NULL;

CREATE TABLE #Rdrecords(
  AutoID bigint NULL,
  ID bigint NULL,
  cVouchType nvarchar(2) NULL,
  cBusType nvarchar(12) NULL,
  cBusCode nvarchar(30) NULL,
  cWhCode nvarchar(10) NULL,
  cInvCode nvarchar(60) NULL,
  iQuantity decimal(30,10) NULL,
  iUnitCost decimal(30,10) NULL,
  iPrice money NULL,
  cBAccounter nvarchar(30) NULL,
  iSQuantity decimal(30,10) NULL,
  fACost decimal(34,10) NULL,
  iAPrice money NULL,
  iMaterialFee money NULL,
  iProcessFee money NULL,
  iProcessCost decimal(30,10) NULL,
  iSMaterialFee money NULL,
  iMatSettleState int NULL,
  dmsdate datetime NULL,
  productinids int NULL,
  bUpdate tinyint NULL,
  iSBsID int NULL,
  iDLsID int NULL,
  cFree1 nvarchar(20) NULL,
  cFree2 nvarchar(20) NULL,
  cFree3 nvarchar(20) NULL,
  cFree4 nvarchar(20) NULL,
  cFree5 nvarchar(20) NULL,
  cFree6 nvarchar(20) NULL,
  cFree7 nvarchar(20) NULL,
  cFree8 nvarchar(20) NULL,
  cFree9 nvarchar(20) NULL,
  cFree10 nvarchar(20) NULL,
  cSource nvarchar(50) NULL,
  iTrIds bigint NULL,
  bNoItemUsed tinyint NULL,
  dsDate datetime NULL,
  iGroupNo int NULL,
  iSProcessFee money NULL,
  iShareMaterialFee decimal(30,10) NULL
);

-- UpdateVoucherCost always JOINs #Inventory. Empty = no Inventory.iInvNCost write-back.
CREATE TABLE #Inventory(
  cInvCode nvarchar(60) NULL,
  iInvNCost float NULL,
  bOMUpdate tinyint NULL
);

-- Observed UI trace runs CreateTempTable after CalcAverPrice; CalcAverPrice needs #IA_Summary so clone first. INFERRED.
SET @errmsg = NULL;
EXEC IA_sp_CalcAverPrice @cDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'CalcAverPrice: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

-- 界面在全月平均之后调 CalDifRate：它重建 DifRateMassRollValue，PerdealAccount 要读（没有计划价时为空表）。
SET @errmsg = NULL;
EXEC IA_sp_CalDifRate @cDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'CalDifRate: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

-- 取本月成本时调拨/组装/形态转换的出入库成本在期末处理定：先定特殊出库成本，再按配对出库定特殊入库成本，
-- 然后按 @bRollCall=2 重算全月平均（含刚补上的入库金额），PerdealAccount 再给其余出库定成本。
SET @errmsg = NULL;
EXEC IA_sp_SetSpecialOutCostForRoll @y, @m, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'SetSpecialOutCostForRoll: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
SET @errmsg = NULL;
EXEC IA_sp_SetSpecialInCost @y, @m, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'SetSpecialInCost: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
SET @errmsg = NULL;
EXEC IA_sp_CalcAverPrice @cDate, @y, @m, @cAccounter, 2, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'CalcAverPrice(2): ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

-- Skip IA_sp_CalDifRate / MakeBlueVouchs / ST_PerdealAccount (计划价/标准成本/月初回冲).
-- Skip ia_sp_ClearIATempTables here: it DROPs UnitCostMassRollValue which PerdealAccount needs. INFERRED.

SET @errmsg = NULL;
EXEC IA_sp_CreateIssueJustInfo @dKeep, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'CreateIssueJustInfo: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

SET @errmsg = NULL;
EXEC IA_sp_PerdealAccount @cDate, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT, 0, 0;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'PerdealAccount: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

-- Skip UpdateMaxMinTable (bmaxmin=FALSE).
SET @errmsg = NULL;
EXEC IA_sp_PerdealRedCostJustInfo @cDate, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'PerdealRedCostJustInfo: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
IF EXISTS (SELECT 1 FROM sysobjects WHERE name=N'Ia_PerdealRedCostJustInfo' AND xtype=N'U')
  AND EXISTS (SELECT TOP 1 AutoID FROM Ia_PerdealRedCostJustInfo WHERE bCreate=1)
BEGIN
  SET @errmsg = NULL;
  EXEC IA_sp_PerdealRedCostJustVouch @cDate, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT, 0, NULL;
  IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'PerdealRedCostJustVouch: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
END

SET @errmsg = NULL;
EXEC IA_sp_PerdealJustInfo @cDate, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'PerdealJustInfo: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
IF EXISTS (SELECT 1 FROM sysobjects WHERE name=N'Ia_PerdealJustInfo' AND xtype=N'U')
  AND EXISTS (SELECT TOP 1 AutoID FROM Ia_PerdealJustInfo WHERE bCreate=1)
BEGIN
  SET @errmsg = NULL;
  EXEC IA_sp_PerdealJustVouch @cDate, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT, 0, NULL;
  IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'PerdealJustVouch: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
END

SET @errmsg = NULL;
EXEC IA_sp_PerdealIssueZero @cDate, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'PerdealIssueZero: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

IF EXISTS (SELECT 1 FROM sysobjects WHERE name=N'IA_Perdeal_IssueJustInfo' AND xtype=N'U')
  AND EXISTS (SELECT TOP 1 AutoID FROM IA_Perdeal_IssueJustInfo WHERE bCreate=1)
BEGIN
  SET @errmsg = NULL;
  EXEC IA_sp_CreateIssueJustVouch @dKeep, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT, 0, NULL;
  IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'CreateIssueJustVouch: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
END

-- 实测：PerdealAccount 只处理 iPeriod=0 的结存，ReAccountSummaryByPerdeal 会把 iPeriod 置 1，必须放在确认成本之后。
SET @errmsg = NULL;
EXEC IA_sp_ReAccountSummaryByPerdeal @cDate, @cEDate, @y, @m, @cAccounter, 1, @errmsg OUTPUT, NULL, 0, 0;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL BEGIN SET @raise = N'ReAccountSummaryByPerdeal: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

IF OBJECT_ID(N'tempdb..#Ia_Valuationass') IS NOT NULL
  EXEC IA_sp_ProcTmpToTable;

EXEC ia_sp_UpdateVoucherCost @cAccounter;
EXEC IA_sp_DropTmpTable N'期末处理';

SELECT N'summary_month' k, COUNT(*) n FROM IA_Summary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'summary_iPeriod1', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iPeriod,0)=1
UNION ALL SELECT N'summary_iPeriod0', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iPeriod,0)=0
UNION ALL SELECT N'subsidiary_month', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'type21_out_just', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m AND cVouType=N'21'
UNION ALL SELECT N'out_null_cost', COUNT(*) FROM IA_Subsidiary
  WHERE iYear=@y AND iMonth=@m AND bRdFlag=0 AND ISNULL(bMoneyFlag,0)=0 AND iOutCost IS NULL;
