-- U8 存货核算 正常单据记账。由桥在请求连接、请求事务里整批执行（不分 GO）；参数取自调用方先建好的 #ia_args
-- （y、m、keep_date、accounter、on_uncosted）。拒绝时 THROW 50000–50099，桥按编号转成 409。
-- 依据：测试账套实测（回滚事务），见 docs/api-reference.md「存货核算记账与期末处理」。
-- 单据记账（按 U8 界面执行的 SQL 重建）. ONE batch. No GO.
-- Caller wraps BEGIN TRAN on one connection. Default 802 2026-10.
SET NOCOUNT ON;
-- U8 过程返回的 @errmsg 用 THROW 50000 抛出（16 级 RAISERROR 不中止批，后面的语句还会接着执行）。
DECLARE @raise nvarchar(2048);

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @m tinyint; SELECT @m = m FROM #ia_args;
DECLARE @cDate nvarchar(10); SELECT @cDate = keep_date FROM #ia_args;
DECLARE @cAccounter nvarchar(20); SELECT @cAccounter = accounter FROM #ia_args;
DECLARE @onUncosted nvarchar(10); SELECT @onUncosted = on_uncosted FROM #ia_args;
DECLARE @saleOutMode nvarchar(20) = N'auto';

DECLARE @dS datetime, @dE datetime, @dKeep datetime;
DECLARE @cRdwhere nvarchar(2000);
DECLARE @errmsg nvarchar(200);
DECLARE @lock nvarchar(50);
DECLARE @cSelect nvarchar(1000);
DECLARE @n int;
DECLARE @bSaleType nvarchar(20);
DECLARE @bEn bit = 0; -- 1 = 发出商品分支生效
DECLARE @cSaleOutStr nvarchar(2000);
DECLARE @cSalewhere nvarchar(2000);

SET @dS = DATEFROMPARTS(@y, @m, 1);
SET @dE = EOMONTH(@dS);
IF NULLIF(LTRIM(@cDate), N'') IS NULL
  SET @cDate = CONVERT(nvarchar(10), @dE, 23);
SET @dKeep = CONVERT(datetime, CONVERT(date, @cDate, 23));
SET @lock = N'IA_ASSUSER' + @cAccounter;
SET @errmsg = NULL;

IF NOT EXISTS (SELECT 1 FROM AccInformation WHERE cSysID=N'IA' AND cName=N'dIAFirstDate' AND ISNULL(cValue,N'')<>N'')
  THROW 50002, N'dIAFirstDate empty — WriteUnAccount would return 0 rows', 1;
IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=@m AND ISNULL(bflag_IA,0)=1)
  THROW 50003, N'IA month already closed (bflag_IA=1)', 1;

SELECT @bSaleType = ISNULL(cValue, cDefault)
FROM AccInformation WHERE cSysID=N'IA' AND cName=N'bSaleType';
IF @bSaleType = N'发出商品' SET @bEn = 1;

EXEC IA_SP_WriteUnAccountVouchForST NULL, N'01';
EXEC IA_SP_WriteUnAccountVouchForST NULL, N'08';
EXEC IA_SP_WriteUnAccountVouchForST NULL, N'09';
EXEC IA_SP_WriteUnAccountVouchForST NULL, N'10';
EXEC IA_SP_WriteUnAccountVouchForST NULL, N'11';
EXEC IA_SP_WriteUnAccountVouchForST NULL, N'32';
-- 发出商品：发票 26/27 同批记账（AccountLoadSale 连 IA_SA_UnAccountVouch）。
IF @bEn = 1 AND @saleOutMode <> N'hold'
BEGIN
  EXEC IA_SP_WriteUnAccountVouchForSA NULL, N'26';
  EXEC IA_SP_WriteUnAccountVouchForSA NULL, N'27';
END

-- 会话临时表由 post_tables.sql 先建好（桥按顺序执行）。

-- IA_AccountLoad prepends: where isnull(nosub.cAccounter,'')='' and
-- then appends And dVouDate >= dIAFirstDate. bAccByVerDate=TRUE on 802.
SET @cRdwhere = N'ISNULL(Inventory.bService,0)=0'
  + N' AND ISNULL(dtAudit,dVouDate) >= ''' + CONVERT(varchar(10), @dS, 23) + N''''
  + N' AND ISNULL(dtAudit,dVouDate) < ''' + CONVERT(varchar(10), DATEADD(day,1,@dE), 23) + N'''';
-- 发出商品：32 不走普通 Load（UI ~23721），改由 AccountLoadDispatch 以 3201 装入。
IF @bEn = 1
  SET @cRdwhere = @cRdwhere + N' AND nosub.cVouType <> N''32''';

EXEC IA_AccountLoad @cRdwhere, N'', N'', N'', N'', 0;

IF @bEn = 1 AND @saleOutMode <> N'hold'
BEGIN
  -- @cWherestr N'1=0'：不装委托代销发货单 05/06；@cSaleOutStr：rd32 → 3201。
  SET @cSaleOutStr = N'ISNULL(Inventory.bService,0)=0'
    + N' AND ISNULL(dtAudit,dVouDate) >= ''' + CONVERT(varchar(10), @dS, 23) + N''''
    + N' AND ISNULL(dtAudit,dVouDate) < ''' + CONVERT(varchar(10), DATEADD(day,1,@dE), 23) + N'''';
  EXEC IA_AccountLoadDispatch N'1=0', @cSaleOutStr;

  -- 发票 26/27（SaleBillVouchsIA870，cChecker<>''）。
  SET @cSalewhere = N'ISNULL(Inventory.bService,0)=0'
    + N' AND ISNULL(dtAudit,dVouDate) >= ''' + CONVERT(varchar(10), @dS, 23) + N''''
    + N' AND ISNULL(dtAudit,dVouDate) < ''' + CONVERT(varchar(10), DATEADD(day,1,@dE), 23) + N'''';
  EXEC IA_AccountLoadSale @cSalewhere;
END

DELETE FROM #IA_Data_Subsidiary
WHERE ISNULL(cVerifier,N'')=N'' AND cvoutype NOT IN (N'20',N'21',N'65',N'66');
DELETE FROM #IA_Data_Subsidiary WHERE ISNULL(bCosting,1)=0;
UPDATE #IA_Data_Subsidiary SET iunitcost=NULL, iprice=NULL
WHERE brdflag=0 AND cVouType IN (N'11',N'09',N'32');
-- UI ~23807：销售出库成本一律留到期末。
UPDATE #IA_Data_Subsidiary SET iunitcost=NULL, iprice=NULL, bmoneyflag=0
WHERE cVouType IN (N'32',N'3201');

UPDATE #IA_data_subsidiary SET bFlag=ISNULL(bFlag,1), iTrans=ISNULL(iTrans,0);
IF @saleOutMode=N'hold'
  DELETE FROM #IA_data_subsidiary WHERE cVouType IN (N'32',N'3201',N'26',N'27');

-- 视图把 bMoneyFlag 写成 NULL，界面按「单据自带金额」置位：有金额为 1（按单据金额记账），没有为 0（由 U8 取成本）。
UPDATE #IA_data_subsidiary SET bMoneyFlag = CASE WHEN iprice IS NOT NULL THEN 1 ELSE 0 END WHERE bMoneyFlag IS NULL;

EXEC IA_UnAccPerDeal @y, @m;

CREATE TABLE #skipkey(cWhCode nvarchar(10), cInvCode nvarchar(60), cFree1 nvarchar(120), cFree2 nvarchar(120), cFree3 nvarchar(120), cFree4 nvarchar(120), cFree5 nvarchar(120), cFree6 nvarchar(120), cFree7 nvarchar(120), cFree8 nvarchar(120), cFree9 nvarchar(120), cFree10 nvarchar(120), cBatchia nvarchar(100));
DECLARE @pass int = 1;
PASS_START:
-- 每遍重建：AccountArea 从 #IA_data_subsidiary 全量（含 3201/26/27）重读，En/暂存表清空。
DELETE FROM #IA_EnAccountArea;
DELETE FROM #IA_NormArea;
INSERT INTO #IA_AccountArea(
  BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
  cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
  iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
  bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
  GUID, MoneySrc, iCostSrc)
SELECT
  NULL, d.cVouType, d.ID, d.cVouCode, d.dVouDate, d.cBusType, d.bRdFlag, d.cWhCode, d.cDepCode, d.cInvCode,
  d.cFree1,d.cFree2,d.cFree3,d.cFree4,d.cFree5,d.cFree6,d.cFree7,d.cFree8,d.cFree9,d.cFree10, d.cBatchia,
  d.iquantity, d.AutoID, d.iunitcost, d.iprice,
  ISNULL(w.cWhValueStyle, N'全月平均法'),
  0, 0,
  CASE WHEN ISNULL(CONVERT(int, d.bMoneyFlag),0)=1 THEN 1 ELSE 0 END,
  0,
  CASE WHEN ISNULL(CONVERT(int, d.bSale),0)=1 THEN 1 ELSE 0 END,
  d.bFlag, d.cBusCode, d.imaterialfee, d.psvsid, d.ufts, d.iPZID,
  NEWID(), CONVERT(smallint,0), CONVERT(smallint, NULL)
FROM #IA_data_subsidiary d
LEFT JOIN Warehouse w ON w.cWhCode = d.cWhCode;

-- 不按批次核算的存货（bCheckBatch=0）结存键不带批号；明细账（#IA_data_subsidiary）仍保留库存批号。
UPDATE a SET cBatchia = N''
FROM #IA_AccountArea a
INNER JOIN Inventory i ON i.cInvCode = a.cInvCode
LEFT JOIN Warehouse w ON w.cWhCode = a.cWhCode
WHERE ISNULL(CONVERT(int, i.bCheckBatch),0) = 0
   OR ISNULL(w.cWhValueStyle, N'全月平均法') NOT IN (N'全月平均法',N'移动平均法',N'先进先出法',N'后进先出法');
DELETE a FROM #IA_AccountArea a INNER JOIN #skipkey k ON a.cWhCode=k.cWhCode AND a.cInvCode=k.cInvCode
  AND ISNULL(a.cFree1,N'')=ISNULL(k.cFree1,N'') AND ISNULL(a.cFree2,N'')=ISNULL(k.cFree2,N'') AND ISNULL(a.cFree3,N'')=ISNULL(k.cFree3,N'')
  AND ISNULL(a.cFree4,N'')=ISNULL(k.cFree4,N'') AND ISNULL(a.cFree5,N'')=ISNULL(k.cFree5,N'') AND ISNULL(a.cFree6,N'')=ISNULL(k.cFree6,N'')
  AND ISNULL(a.cFree7,N'')=ISNULL(k.cFree7,N'') AND ISNULL(a.cFree8,N'')=ISNULL(k.cFree8,N'') AND ISNULL(a.cFree9,N'')=ISNULL(k.cFree9,N'')
  AND ISNULL(a.cFree10,N'')=ISNULL(k.cFree10,N'') AND ISNULL(a.cBatchia,N'')=ISNULL(k.cBatchia,N'');
SELECT @n = COUNT(*) FROM #IA_AccountArea;
IF @n = 0
BEGIN
  -- 没有可记账的行（未审核、不核算、服务类、该键已期末处理）：照常返回计数。
  SELECT N'area' k, 0 n UNION ALL SELECT N'subsidiary_month', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m;
  RETURN;
END

BEGIN TRY
  UPDATE Rdrecords01 SET cbAccounter=@lock
  FROM #IA_AccountArea a INNER JOIN Rdrecords01 r ON a.ID=r.AutoID AND a.cVouType=N'01';
  UPDATE Rdrecords08 SET cbAccounter=@lock
  FROM #IA_AccountArea a INNER JOIN Rdrecords08 r ON a.ID=r.AutoID AND a.cVouType=N'08';
  UPDATE Rdrecords09 SET cbAccounter=@lock
  FROM #IA_AccountArea a INNER JOIN Rdrecords09 r ON a.ID=r.AutoID AND a.cVouType=N'09';
  UPDATE Rdrecords10 SET cbAccounter=@lock
  FROM #IA_AccountArea a INNER JOIN Rdrecords10 r ON a.ID=r.AutoID AND a.cVouType=N'10';
  UPDATE Rdrecords11 SET cbAccounter=@lock
  FROM #IA_AccountArea a INNER JOIN Rdrecords11 r ON a.ID=r.AutoID AND a.cVouType=N'11';
  UPDATE Rdrecords32 SET cbAccounter=@lock
  FROM #IA_AccountArea a INNER JOIN Rdrecords32 r ON a.ID=r.AutoID AND a.cVouType IN (N'32',N'3201');
  UPDATE SaleBillVouchs SET cbAccounter=@lock
  FROM #IA_AccountArea a INNER JOIN SaleBillVouchs s ON a.ID=s.AutoID AND a.cVouType IN (N'26',N'27');

  EXEC IA_sp_SetRedIssue;
  -- 3201 红字取蓝字 IA_Subsidiary 成本（iCostSrc=31）。3201 须留在 AccountArea 过 MonthAcc。
  IF @bEn = 1
    EXEC IA_sp_SetRedEnSaleOut;

  -- 直接供应的材料出库按产成品入库取成本（IA_sp_SetMaterialCost 要调用方先建 #Rdrecords），暂不支持。
  IF EXISTS (SELECT 1 FROM #IA_AccountArea a INNER JOIN Rdrecords11 r ON a.ID=r.AutoID
             WHERE a.cVouType=N'11' AND a.cBusType=N'直接供应' AND r.productinids IS NOT NULL)
    THROW 50060, N'direct-supply material issues (直接供应) not supported yet', 1;

  SET @errmsg = NULL;
  EXEC IA_sp_MonthAcc @cDate, @y, @m, @cAccounter, 0, @errmsg OUTPUT;
  IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL
  BEGIN
    BEGIN SET @raise = N'MonthAcc: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
  END

  -- U8 算不出入库成本的存货（选项「手工输入」）：refuse 列出后拒绝；skip 整键不记账（界面上取消勾选），清空工作表后重跑一遍。
  IF EXISTS (SELECT TOP 1 AutoID FROM #Ia_ManualInputcost)
  BEGIN
    IF @onUncosted <> N'skip' OR @pass > 1
    BEGIN
      SELECT TOP 20 m.cWhDepCode, m.cInvCode, m.cBatchia, (SELECT COUNT(*) FROM #Ia_ManualInputcost) total FROM #Ia_ManualInputcost m;
      THROW 50061, N'outgoing cost undetermined for some items (U8 asks for manual unit cost)', 1;
    END
    INSERT INTO #skipkey SELECT cWhDepCode, cInvCode, cFree1, cFree2, cFree3, cFree4, cFree5, cFree6, cFree7, cFree8, cFree9, cFree10, cBatchia FROM #Ia_ManualInputcost;
    DELETE FROM #IA_AccountArea; DELETE FROM #IA_Summary; DELETE FROM #IA_AutoJustIn; DELETE FROM #IA_AccountData; DELETE FROM #Ia_ManualInputcost;
    SET @pass = 2;
    GOTO PASS_START;
  END
  IF EXISTS (SELECT TOP 1 AutoID FROM #IA_AutoJustIn)
  BEGIN
    SET @errmsg = NULL;
    EXEC IA_sp_CreateJustVouch @dKeep, @m, @cAccounter, N'IA', 1, 10, 0, @errmsg OUTPUT, 0;
    IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL
      BEGIN SET @raise = N'CreateJustVouch: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END
  END

  -- 拆分：3201/26/27 移到 #IA_EnAccountArea，普通记账（InsertSubWithStand）不得含它们。
  INSERT INTO #IA_EnAccountArea(
    BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
    cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
    iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
    bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
    GUID, MoneySrc, iCostSrc)
  SELECT
    BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
    cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
    iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
    bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
    GUID, MoneySrc, iCostSrc
  FROM #IA_AccountArea
  WHERE cVouType IN (N'3201', N'26', N'27', N'05', N'06', N'5', N'6');
  DELETE FROM #IA_AccountArea
  WHERE cVouType IN (N'3201', N'26', N'27', N'05', N'06', N'5', N'6');

  SET @errmsg = NULL;
  EXEC IA_sp_InsertIntoSubsidiary @dKeep, @y, @m, @cAccounter, 0, @errmsg OUTPUT, 0, 0;
  IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL
    BEGIN SET @raise = N'InsertIntoSubsidiary: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

  SET @errmsg = NULL;
  EXEC IA_sp_ReCountSummary @cDate, @y, @m, @cAccounter, 0, @errmsg OUTPUT, 0;
  IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL
    BEGIN SET @raise = N'ReCountSummary: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

  -- 发出商品记账（UI OutGoodsAccounting）：AccountArea 只放 3201（InsertOutSubWithStand 写库存侧出库），
  -- 26/27 只在 EnAccountArea；普通行先暂存，En 记完后放回供 SetVoucherAccounter 盖章。
  IF EXISTS (SELECT 1 FROM #IA_EnAccountArea)
  BEGIN
    INSERT INTO #IA_NormArea(
      BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
      cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
      iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
      bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
      GUID, MoneySrc, iCostSrc)
    SELECT
      BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
      cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
      iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
      bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
      GUID, MoneySrc, iCostSrc
    FROM #IA_AccountArea;
    DELETE FROM #IA_AccountArea;

    INSERT INTO #IA_AccountArea(
      BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
      cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
      iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
      bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
      GUID, MoneySrc, iCostSrc)
    SELECT
      BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
      cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
      iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
      bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
      GUID, MoneySrc, iCostSrc
    FROM #IA_EnAccountArea
    WHERE cVouType = N'3201';

    EXEC IA_sp_SetRedEnSaleOut;
    EXEC IA_sp_SetRedDispatchlist;
    SET @errmsg = NULL;
    EXEC IA_sp_InsertIntoEnSubsidiary @dKeep, @y, @m, @cAccounter, 0, @errmsg OUTPUT, 0;
    IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL
      BEGIN SET @raise = N'InsertIntoEnSubsidiary: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

    INSERT INTO #IA_AccountArea(
      BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
      cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
      iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
      bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
      GUID, MoneySrc, iCostSrc)
    SELECT
      BakID, cvoutype, id, cvoucode, ddate, cbustype, brdflag, cwhcode, cdepcode, cinvcode,
      cfree1,cfree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10, cBatchia,
      iquantity, AutoID, iunitprice, iprice, cValueStyle, iDebit, iCredit,
      bMoneyFlag, bManual, bSale, bFlag, cBusCode, imaterialfee, psvsid, ufts, ipzid,
      GUID, MoneySrc, iCostSrc
    FROM #IA_NormArea;
  END

  -- rd32 按 AccountArea 3201（UnAccount32 映射 3201→32）；SaleBillVouchs 26/27 按 #IA_EnAccountArea。
  EXEC ia_sp_SetVoucherAccounter @dKeep, @cAccounter;
  -- 本次没记账的行（跳过的键、整键排除）上残留的 IA_ASSUSER 锁标记一并清掉。
  EXEC ia_sp_ClearVoucherAccounter @cAccounter;
END TRY
BEGIN CATCH
  IF OBJECT_ID(N'ia_sp_ClearErrorVoucherAccounter', N'P') IS NOT NULL
    EXEC ia_sp_ClearErrorVoucherAccounter;
  EXEC ia_sp_ClearVoucherAccounter @cAccounter;
  THROW;
END CATCH

-- Do NOT EXEC IA_sp_DropTmpTable N'记账' — it DROPs permanent templates IA_AccountArea / IA_Data_Subsidiary.

SELECT N'area' k, COUNT(*) n FROM #IA_AccountArea
UNION ALL SELECT N'subsidiary_month', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'summary_month', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'summary_iPeriod1', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iPeriod,0)=1
UNION ALL SELECT N'rd01_posted', COUNT(*) FROM rdrecords01 WHERE ISNULL(cbaccounter,N'')=@cAccounter
UNION ALL SELECT N'rd08_posted', COUNT(*) FROM rdrecords08 WHERE ISNULL(cbaccounter,N'')=@cAccounter
UNION ALL SELECT N'rd09_posted', COUNT(*) FROM rdrecords09 WHERE ISNULL(cbaccounter,N'')=@cAccounter
UNION ALL SELECT N'rd10_posted', COUNT(*) FROM rdrecords10 WHERE ISNULL(cbaccounter,N'')=@cAccounter
UNION ALL SELECT N'rd11_posted', COUNT(*) FROM rdrecords11 WHERE ISNULL(cbaccounter,N'')=@cAccounter
UNION ALL SELECT N'rd32_posted', COUNT(*) FROM rdrecords32 WHERE ISNULL(cbaccounter,N'')=@cAccounter
UNION ALL SELECT N'unacc01', COUNT(*) FROM IA_ST_UnAccountVouch01
UNION ALL SELECT N'unacc08', COUNT(*) FROM IA_ST_UnAccountVouch08
UNION ALL SELECT N'unacc09', COUNT(*) FROM IA_ST_UnAccountVouch09
UNION ALL SELECT N'unacc10', COUNT(*) FROM IA_ST_UnAccountVouch10
UNION ALL SELECT N'unacc11', COUNT(*) FROM IA_ST_UnAccountVouch11
UNION ALL SELECT N'unacc32', COUNT(*) FROM IA_ST_UnAccountVouch32
UNION ALL SELECT N'en_area', COUNT(*) FROM #IA_EnAccountArea
UNION ALL SELECT N'ensubsidiary_month', COUNT(*) FROM IA_EnSubsidiary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'sbv_posted', COUNT(*) FROM SaleBillVouchs WHERE ISNULL(cbAccounter,N'')=@cAccounter
UNION ALL SELECT N'unacc_SA', COUNT(*) FROM IA_SA_UnAccountVouch
UNION ALL SELECT N'lock_left', (
  SELECT COUNT(*) FROM rdrecords01 WHERE cbAccounter LIKE N'IA_ASSUSER%'
)+ (SELECT COUNT(*) FROM rdrecords08 WHERE cbAccounter LIKE N'IA_ASSUSER%')
+ (SELECT COUNT(*) FROM rdrecords09 WHERE cbAccounter LIKE N'IA_ASSUSER%')
+ (SELECT COUNT(*) FROM rdrecords10 WHERE cbAccounter LIKE N'IA_ASSUSER%')
+ (SELECT COUNT(*) FROM rdrecords11 WHERE cbAccounter LIKE N'IA_ASSUSER%')
+ (SELECT COUNT(*) FROM rdrecords32 WHERE cbAccounter LIKE N'IA_ASSUSER%')
+ (SELECT COUNT(*) FROM SaleBillVouchs WHERE cbAccounter LIKE N'IA_ASSUSER%');
