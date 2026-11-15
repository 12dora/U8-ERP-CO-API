-- U8 存货核算 正常单据记账要用的会话临时表（U8 界面建表时在模板之外另加的列都在这里）。
-- 桥先在同一连接上执行本文件，再执行 post.sql。按模板表结构显式建表：新建账套可能没有这些模板表。
SET NOCOUNT ON;
-- Session #temp CREATE/ALTER for IA chains. ONE batch. No GO.
-- Templates: SELECT * INTO #t FROM <template> WHERE 1=0 + ALTER ADD extras.
-- No three-part names. OBJECT_ID(N'tempdb..#t') is the session-temp existence check U8 itself uses.
-- Two independent sections (post / period-end). DROP first so each can run alone or PE after post on one connection.

-- =============================================================================
-- POST CHAIN  (ia/post.sql + transitive: Load, UnAccPerDeal, SetRedIssue,
-- SetMaterialCost/GetRdRecords, MonthAcc/GetOptionCost/AddToSummary/SetSummaryFlag,
-- PatchManual, AccManualRec, CreateJustVouch, InsertIntoSubsidiary/BatchSettle,
-- ReCountSummary, SetVoucherAccounter)
-- Empty shells. UI/post INSERT fills #IA_AccountArea; AccountLoad fills #IA_data_subsidiary.
-- =============================================================================

IF OBJECT_ID(N'tempdb..#IA_data_subsidiary') IS NOT NULL DROP TABLE #IA_data_subsidiary;
IF OBJECT_ID(N'tempdb..#IA_AccountArea') IS NOT NULL DROP TABLE #IA_AccountArea;
IF OBJECT_ID(N'tempdb..#IA_EnAccountArea') IS NOT NULL DROP TABLE #IA_EnAccountArea;
IF OBJECT_ID(N'tempdb..#IA_Summary') IS NOT NULL DROP TABLE #IA_Summary;
IF OBJECT_ID(N'tempdb..#IA_AutoJustIn') IS NOT NULL DROP TABLE #IA_AutoJustIn;
IF OBJECT_ID(N'tempdb..#Ia_ManualInputcost') IS NOT NULL DROP TABLE #Ia_ManualInputcost;
IF OBJECT_ID(N'tempdb..#IA_ValuationAss') IS NOT NULL DROP TABLE #IA_ValuationAss;
IF OBJECT_ID(N'tempdb..#IA_AccountData') IS NOT NULL DROP TABLE #IA_AccountData;
IF OBJECT_ID(N'tempdb..#Rdrecords') IS NOT NULL DROP TABLE #Rdrecords;
IF OBJECT_ID(N'tempdb..#Inventory') IS NOT NULL DROP TABLE #Inventory;

-- 按 U8 模板表结构显式建表（新建账套可能没有模板表）。
CREATE TABLE #IA_data_subsidiary (
  [bRdFlag] tinyint NULL,
  [cBusType] nvarchar(12) NULL,
  [cBusCode] nvarchar(30) NULL,
  [cVouCode] nvarchar(30) NULL,
  [ID] int NULL,
  [ValueID] int NULL,
  [JustID] int NULL,
  [dVouDate] datetime NULL,
  [dKeepDate] datetime NULL,
  [dtAudit] datetime NULL,
  [iMonth] tinyint NULL,
  [iPZID] int NULL,
  [iPZDate] datetime NULL,
  [cPZtype] nvarchar(8) NULL,
  [cPZdigest] nvarchar(60) NULL,
  [cInvHead] nvarchar(40) NULL,
  [cDifHead] nvarchar(40) NULL,
  [cOppHead] nvarchar(40) NULL,
  [cVouType] nvarchar(4) NULL,
  [cPTCode] nvarchar(2) NULL,
  [cSTCode] nvarchar(2) NULL,
  [cWhCode] nvarchar(10) NULL,
  [cAccDep] nvarchar(12) NULL,
  [cInvCode] nvarchar(60) NULL,
  [cRdCode] nvarchar(5) NULL,
  [cVenCode] nvarchar(20) NULL,
  [cCusCode] nvarchar(20) NULL,
  [cOrderCode] nvarchar(30) NULL,
  [cARVCode] nvarchar(30) NULL,
  [cBillCode] int NULL,
  [cDLCode] int NULL,
  [cPSPCode] nvarchar(60) NULL,
  [cProCode] nvarchar(60) NULL,
  [cDepCode] nvarchar(12) NULL,
  [cPersonCode] nvarchar(20) NULL,
  [cHandler] nvarchar(20) NULL,
  [iquantity] float NULL,
  [iunitcost] float NULL,
  [iprice] decimal(38,2) NULL,
  [cBatchCode] nvarchar(20) NULL,
  [cAccounter] nvarchar(20) NULL,
  [cMaker] nvarchar(20) NULL,
  [bFlag] tinyint NULL,
  [bMoneyFlag] bit NULL,
  [bSale] bit NULL,
  [cMemo] nvarchar(255) NULL,
  [cDefine1] nvarchar(20) NULL,
  [cDefine2] nvarchar(20) NULL,
  [cDefine3] nvarchar(20) NULL,
  [cDefine4] datetime NULL,
  [cDefine5] int NULL,
  [cDefine6] datetime NULL,
  [cDefine7] float NULL,
  [cDefine8] nvarchar(4) NULL,
  [cDefine9] nvarchar(8) NULL,
  [cDefine10] nvarchar(60) NULL,
  [cFree1] nvarchar(20) NULL,
  [cFree2] nvarchar(20) NULL,
  [cPZID] nvarchar(30) NULL,
  [cDefine22] nvarchar(60) NULL,
  [cDefine23] nvarchar(60) NULL,
  [cDefine24] nvarchar(60) NULL,
  [cDefine25] nvarchar(60) NULL,
  [cDefine26] float NULL,
  [cDefine27] float NULL,
  [cItem_class] nvarchar(10) NULL,
  [cItemCode] nvarchar(60) NULL,
  [cName] nvarchar(255) NULL,
  [cItemCName] nvarchar(20) NULL,
  [noJustQuantity] decimal(28,6) NULL,
  [cFree3] nvarchar(20) NULL,
  [cFree4] nvarchar(20) NULL,
  [cFree5] nvarchar(20) NULL,
  [cFree6] nvarchar(20) NULL,
  [cFree7] nvarchar(20) NULL,
  [cFree8] nvarchar(20) NULL,
  [cFree9] nvarchar(20) NULL,
  [cFree10] nvarchar(20) NULL,
  [cDefine11] nvarchar(120) NULL,
  [cDefine12] nvarchar(120) NULL,
  [cDefine13] nvarchar(120) NULL,
  [cDefine14] nvarchar(120) NULL,
  [cDefine15] int NULL,
  [cDefine16] float NULL,
  [cDefine28] nvarchar(120) NULL,
  [cDefine29] nvarchar(120) NULL,
  [cDefine30] nvarchar(120) NULL,
  [cDefine31] nvarchar(120) NULL,
  [cDefine32] nvarchar(120) NULL,
  [cDefine33] nvarchar(120) NULL,
  [cDefine34] int NULL,
  [cDefine35] int NULL,
  [cDefine36] datetime NULL,
  [cDefine37] datetime NULL,
  [psvsid] int NULL,
  [cCXHDcode] nvarchar(30) NULL,
  [cCXFScode] nvarchar(20) NULL,
  [cBatchia] nvarchar(100) NULL,
  [dMadeDateia] datetime NULL,
  [iMassDateia] int NULL,
  [cMassUnit] smallint NULL,
  [dVDateia] datetime NULL,
  [cproordercode] nvarchar(30) NULL,
  [iproorderid] int NULL,
  [iproorderids] int NULL,
  [cworkprocode] nvarchar(20) NULL,
  [cworkprocodedis] nvarchar(60) NULL,
  [cworkcentercode] nvarchar(20) NULL,
  [cworkcentername] nvarchar(60) NULL,
  [cendcode] nvarchar(30) NULL,
  [csaleordercode] nvarchar(30) NULL,
  [cIMOrdercode ] nvarchar(30) NULL,
  [isaleorderid] int NULL,
  [isaleordersid] int NULL,
  [isaleorderids] int NULL,
  [centrustordercode] nvarchar(30) NULL,
  [ientrustorderid] int NULL,
  [ientrustordersid] int NULL,
  [cpurordercode] nvarchar(60) NULL,
  [ipurordersid] int NULL,
  [idlsid] int NULL,
  [strContractCode] nvarchar(150) NULL,
  [inum] float NULL,
  [cAssUnit] nvarchar(80) NULL,
  [exoCode] nvarchar(30) NULL,
  [iExRowno] int NULL,
  [consignMentCode] nvarchar(30) NULL,
  [iconsignmentautoid] int NULL,
  [imaterialfee] decimal(38,2) NULL,
  [iprocessfee] decimal(38,2) NULL,
  [cVerifier] nvarchar(20) NULL,
  [cvmivencode] nvarchar(40) NULL,
  [AutoID] int IDENTITY(1,1) NOT NULL,
  [ibg_ctrl] bit NULL,
  [iTrans] int NULL,
  [iLevel] int NULL,
  [bEn] tinyint NULL,
  [bFX] tinyint NULL,
  [ufts] money NULL,
  [bCosting] bit NULL,
  [dnOrderTime] datetime NULL,
  [cdemandcode] nvarchar(30) NULL,
  [cdemandmemo] nvarchar(300) NULL
);
-- IA_AccountLoad @DataField extras (not on the permanent template).
ALTER TABLE #IA_data_subsidiary ADD
  bSpIN tinyint NULL,
  cbMemo nvarchar(255) NULL,
  iProductType int NULL,
  dMSDate datetime NULL;
-- AccountLoadSale 写 isaleoutid；InsertIntoEnSubsidiary 回填 cSrcVouType/iSrcid。
ALTER TABLE #IA_data_subsidiary ADD
  isaleoutid int NULL,
  cSrcVouType nvarchar(4) NULL,
  iSrcid int NULL;

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
ALTER TABLE #IA_AccountArea ADD
  GUID uniqueidentifier NULL,
  MoneySrc smallint NULL,
  iCostSrc smallint NULL;

-- MonthAcc/AccManualRec UPDATE this for cVouType=3201 without OBJECT_ID. UI: Select * Into from #IA_AccountArea.
SELECT * INTO #IA_EnAccountArea FROM #IA_AccountArea WHERE 1=0;
-- 发出商品记账时暂存普通行（InsertOutSubWithStand 会把整个 #IA_AccountArea 写成出库）。
IF OBJECT_ID(N'tempdb..#IA_NormArea') IS NOT NULL DROP TABLE #IA_NormArea;
SELECT * INTO #IA_NormArea FROM #IA_AccountArea WHERE 1=0;

SELECT * INTO #IA_Summary FROM IA_Summary WHERE 1=0;

-- 按 U8 模板表结构显式建表（新建账套可能没有模板表）。
CREATE TABLE #IA_AutoJustIn (
  [AccID] int NULL,
  [bAcc] tinyint NULL,
  [cSrcVoutype] nvarchar(20) NULL,
  [cVouType] nvarchar(20) NULL,
  [ID] int NULL,
  [iJustPrice] float NULL,
  [iJustDebit] float NULL,
  [iJustCredit] float NULL,
  [cFlag] nvarchar(2) NULL,
  [cBusType] nvarchar(12) NULL,
  [AutoID] int IDENTITY(1,1) NOT NULL,
  [bRdFlag] tinyint NULL
);
ALTER TABLE #IA_AutoJustIn ADD GUID uniqueidentifier NULL;

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
-- GetOptionCost / PatchManual INSERT cBatchIA. AccountArea.cBatchia is nvarchar(100).
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
-- ProcTmpToTable join. PatchManual FIFO INSERT compiles this table even on 全月平均.
ALTER TABLE #IA_ValuationAss ADD iOldAutoID int NULL;

-- COM CreateTempTableAccountData (no 0-row template).
CREATE TABLE #IA_AccountData(
  AutoID int IDENTITY(1,1) NOT NULL,
  ID int NULL,
  cVouType nvarchar(4) NULL,
  bUpdateCost tinyint NULL,
  iquantity float NULL,
  iUnitCost float NULL,
  iPrice decimal(38,2) NULL,
  faCost decimal(34,10) NULL,
  iAPrice money NULL,
  iProcessFee money NULL,
  iProcessCost money NULL,
  iMaterialFee decimal(38,2) NULL,
  iSMaterialFee money NULL,
  iMatSettleState int NULL,
  dmsdate datetime NULL,
  bOMUpdate tinyint NULL,
  bRdUpdated tinyint NULL,
  bNoItemUsed tinyint NULL
);

-- IA_sp_GetRdRecords INSERT list. Types from RdRecords11 / RdRecord11 / RdRecords01.
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

-- BatchSettle INSERT (cInvCode,iInvNCost,bOMUpdate). UpdateVoucherCost always JOINs this.
CREATE TABLE #Inventory(
  cInvCode nvarchar(60) NULL,
  iInvNCost float NULL,
  bOMUpdate tinyint NULL
);


