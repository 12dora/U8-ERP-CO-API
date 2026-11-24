-- U8 存货核算 恢复记账。由桥在请求连接、请求事务里整批执行（不分 GO）；参数取自调用方先建好的 #ia_args
-- （y、m、keep_date、accounter、on_uncosted）。拒绝时 THROW 50000–50099，桥按编号转成 409。
-- 依据：测试账套实测（回滚事务），见 docs/api-reference.md「存货核算记账与期末处理」。
-- 恢复记账. ONE batch. No GO.
-- Refuses if any IA_Summary of the month has iPeriod=1 (期末处理 already run).
SET NOCOUNT ON;
-- U8 过程返回的 @errmsg 用 THROW 50000 抛出（16 级 RAISERROR 不中止批，后面的语句还会接着执行）。
DECLARE @raise nvarchar(2048);
IF OBJECT_ID(N'IA_RestoreAccount', N'U') IS NULL
  -- U8 界面首次恢复记账时才建这张工作表；新建账套没有。
  CREATE TABLE IA_RestoreAccount (
  [AutoID] int NULL,
  [MinID] int NULL,
  [BakID] int NULL,
  [bRdFlag] bit NULL,
  [cBusType] nvarchar(12) NULL,
  [cBusCode] nvarchar(30) NULL,
  [cVouCode] nvarchar(30) NULL,
  [ID] int NULL,
  [ValueID] int NULL,
  [JustID] int NULL,
  [dVouDate] datetime NULL,
  [dKeepDate] datetime NULL,
  [iMonth] tinyint NULL,
  [iYear] smallint NULL,
  [ipzid] int NULL,
  [cVouType] nvarchar(4) NULL,
  [cPTCode] nvarchar(30) NULL,
  [cSTCode] nvarchar(2) NULL,
  [cWhCode] nvarchar(10) NULL,
  [cAccDep] nvarchar(12) NULL,
  [cInvCode] nvarchar(60) NULL,
  [cRdCode] nvarchar(5) NULL,
  [cVenCode] nvarchar(30) NULL,
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
  [iAInQuantity] decimal(38,10) NULL,
  [iAOutQuantity] decimal(38,10) NULL,
  [iInCost] decimal(38,13) NULL,
  [iOutCost] decimal(38,13) NULL,
  [iAInPrice] money NULL,
  [iAOutPrice] money NULL,
  [iDebitDifCost] money NULL,
  [iCreditDifCost] money NULL,
  [cAccounter] nvarchar(30) NULL,
  [cMaker] nvarchar(20) NULL,
  [bFlag] tinyint NULL,
  [bMoneyFlag] bit NULL,
  [bSale] bit NULL,
  [cMemo] nvarchar(255) NULL,
  [cPZID] nvarchar(30) NULL,
  [cItem_class] nvarchar(10) NULL,
  [cItemCode] nvarchar(60) NULL,
  [cName] nvarchar(255) NULL,
  [cItemCName] nvarchar(20) NULL,
  [noJustQuantity] float NULL,
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
  [cDefine11] nvarchar(120) NULL,
  [cDefine12] nvarchar(120) NULL,
  [cDefine13] nvarchar(120) NULL,
  [cDefine14] nvarchar(120) NULL,
  [cDefine15] int NULL,
  [cDefine16] float NULL,
  [cDefine22] nvarchar(60) NULL,
  [cDefine23] nvarchar(60) NULL,
  [cDefine24] nvarchar(60) NULL,
  [cDefine25] nvarchar(60) NULL,
  [cDefine26] float NULL,
  [cDefine27] float NULL,
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
  [cCXFScode] nvarchar(30) NULL,
  [cBatchia] nvarchar(60) NULL,
  [dMadeDateia] datetime NULL,
  [iMassDateia] int NULL,
  [cMassUnit] smallint NULL,
  [dVDateia] datetime NULL,
  [cproordercode] nvarchar(30) NULL,
  [iproorderid] int NULL,
  [iproorderids] int NULL,
  [cworkprocode] nvarchar(30) NULL,
  [cworkprocodedis] nvarchar(60) NULL,
  [cworkcentercode] nvarchar(30) NULL,
  [cworkcentername] nvarchar(60) NULL,
  [cendcode] nvarchar(30) NULL,
  [csaleordercode] nvarchar(30) NULL,
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
  [inum] decimal(30,10) NULL,
  [cAssUnit] nvarchar(80) NULL,
  [exoCode] nvarchar(30) NULL,
  [iExRowno] int NULL,
  [consignMentCode] nvarchar(30) NULL,
  [iconsignmentautoid] int NULL,
  [imaterialfee] money NULL,
  [iProcessFee] money NULL,
  [isen] int NULL,
  [hasjust] int NULL,
  [iInvRCost] float NULL,
  [cIMOrdercode] nvarchar(30) NULL,
  [cSRcVoutype] nvarchar(4) NULL,
  [MoneySrc] int NULL,
  [cValueType] nvarchar(12) NULL
);

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @m tinyint; SELECT @m = m FROM #ia_args;
DECLARE @errmsg nvarchar(200);
DECLARE @n int;

IF EXISTS (
  SELECT 1 FROM IA_Summary
  WHERE iYear=@y AND iMonth=@m AND ISNULL(iPeriod,0)=1 AND ISNULL(iDirect,0)=0)
  THROW 50010, N'cannot 恢复记账: 期末处理 already run (IA_Summary.iPeriod=1). Run ia/period_end cancel first.', 1;

DELETE FROM IA_RestoreAccount
WHERE iYear=@y AND iMonth=@m
  AND cVouType IN (N'01',N'08',N'09',N'10',N'11',N'32',N'3201',N'26',N'27',N'20',N'21');

-- 出入库调整单（20/21）只恢复手工录入的（cBusType 空）和记账时系统生成的（cBusType=记账，IA_sp_CreateJustVouch）。
-- 成本管理的「在产成本调整」（IA_SP_CreateAdjustVouchForRoll）、期末处理生成的（已由取消期末处理删除）不在恢复集合里：
-- IA_sp_RestoreAccGen 对它们的 cAuto 另有处理（保留 'True'/'FALSE'），只有成本管理能重新生成，删了就找不回来。

-- ia_cancelkeep has no cValueType; RestoreAccGen returns immediately unless cValueType is 全月平均法 (not FIFO/LIFO/个别).
INSERT INTO IA_RestoreAccount(
  AutoID, MinID, BakID, bRdFlag, cBusType, cBusCode, cVouCode, ID, ValueID, JustID,
  dVouDate, dKeepDate, iMonth, iYear, ipzid, cVouType, cPTCode, cSTCode, cWhCode, cAccDep,
  cInvCode, cRdCode, cVenCode, cCusCode, cOrderCode, cARVCode, cBillCode, cDLCode, cPSPCode, cProCode,
  cDepCode, cPersonCode, cHandler, iAInQuantity, iAOutQuantity, iInCost, iOutCost, iAInPrice, iAOutPrice,
  iDebitDifCost, iCreditDifCost, cAccounter, cMaker, bFlag, bMoneyFlag, bSale, cMemo, cPZID,
  cItem_class, cItemCode, cName, cItemCName, noJustQuantity,
  cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10,
  cDefine1,cDefine2,cDefine3,cDefine4,cDefine5,cDefine6,cDefine7,cDefine8,cDefine9,cDefine10,
  cDefine11,cDefine12,cDefine13,cDefine14,cDefine15,cDefine16,
  cDefine22,cDefine23,cDefine24,cDefine25,cDefine26,cDefine27,
  cDefine28,cDefine29,cDefine30,cDefine31,cDefine32,cDefine33,cDefine34,cDefine35,cDefine36,cDefine37,
  psvsid, cCXHDcode, cCXFScode, cBatchia, dMadeDateia, iMassDateia, cMassUnit, dVDateia,
  cproordercode, iproorderid, iproorderids, cworkprocode, cworkprocodedis, cworkcentercode, cworkcentername,
  cendcode, csaleordercode, isaleorderid, isaleordersid, isaleorderids,
  centrustordercode, ientrustorderid, ientrustordersid, cpurordercode, ipurordersid, idlsid, strContractCode,
  inum, cAssUnit, exoCode, iExRowno, consignMentCode, iconsignmentautoid,
  imaterialfee, iProcessFee, isen, hasjust, iInvRCost, cIMOrdercode, cSRcVoutype, MoneySrc, cValueType)
SELECT
  k.AutoID, k.MinID, k.BakID, k.bRdFlag, k.cBusType, k.cBusCode, k.cVouCode, k.ID, k.ValueID, k.JustID,
  k.dVouDate, k.dKeepDate, k.iMonth, k.iYear, k.ipzid, k.cVouType, k.cPTCode, k.cSTCode, k.cWhCode, k.cAccDep,
  k.cInvCode, k.cRdCode, k.cVenCode, k.cCusCode, k.cOrderCode, k.cARVCode, k.cBillCode, k.cDLCode, k.cPSPCode, k.cProCode,
  k.cDepCode, k.cPersonCode, k.cHandler, k.iAInQuantity, k.iAOutQuantity, k.iInCost, k.iOutCost, k.iAInPrice, k.iAOutPrice,
  k.iDebitDifCost, k.iCreditDifCost, k.cAccounter, k.cMaker, k.bFlag, k.bMoneyFlag, k.bSale, k.cMemo, k.cPZID,
  k.cItem_class, k.cItemCode, k.cName, k.cItemCName, k.noJustQuantity,
  k.cFree1,k.cFree2,k.cFree3,k.cFree4,k.cFree5,k.cFree6,k.cFree7,k.cFree8,k.cFree9,k.cFree10,
  k.cDefine1,k.cDefine2,k.cDefine3,k.cDefine4,k.cDefine5,k.cDefine6,k.cDefine7,k.cDefine8,k.cDefine9,k.cDefine10,
  k.cDefine11,k.cDefine12,k.cDefine13,k.cDefine14,k.cDefine15,k.cDefine16,
  k.cDefine22,k.cDefine23,k.cDefine24,k.cDefine25,k.cDefine26,k.cDefine27,
  k.cDefine28,k.cDefine29,k.cDefine30,k.cDefine31,k.cDefine32,k.cDefine33,k.cDefine34,k.cDefine35,k.cDefine36,k.cDefine37,
  k.psvsid, k.cCXHDcode, k.cCXFScode, k.cBatchia, k.dMadeDateia, k.iMassDateia, k.cMassUnit, k.dVDateia,
  k.cproordercode, k.iproorderid, k.iproorderids, k.cworkprocode, k.cworkprocodedis, k.cworkcentercode, k.cworkcentername,
  k.cendcode, k.csaleordercode, k.isaleorderid, k.isaleordersid, k.isaleorderids,
  k.centrustordercode, k.ientrustorderid, k.ientrustordersid, k.cpurordercode, k.ipurordersid, k.idlsid, k.strContractCode,
  k.inum, k.cAssUnit, k.exoCode, k.iExRowno, k.consignMentCode, k.iconsignmentautoid,
  k.imaterialfee, k.iProcessFee, k.isen, k.hasjust, k.iInvRCost, k.cIMOrdercode, k.cSRcVoutype, k.MoneySrc,
  ISNULL(w.cWhValueStyle, N'全月平均法')
FROM ia_cancelkeep k
LEFT JOIN Warehouse w ON w.cWhCode = k.cWhCode
WHERE k.iYear=@y AND k.iMonth=@m
  AND k.cVouType IN (N'01',N'08',N'09',N'10',N'11',N'32',N'3201',N'26',N'27',N'20',N'21')
  AND (k.cVouType NOT IN (N'20',N'21') OR ISNULL(k.cBusType,N'') IN (N'',N'记账'));

-- 已制单的记账行（cPZID 非空）U8 的恢复记账清单里不列；桥拒绝，免得总账凭证的来源行被删。
IF EXISTS (SELECT 1 FROM IA_RestoreAccount WHERE iYear=@y AND iMonth=@m AND ISNULL(cPZID,N'')<>N'')
  THROW 50012, N'cannot 恢复记账: some posted lines already have an IA voucher (cPZID). Delete the voucher first (ia/voucher/delete).', 1;

SELECT @n = COUNT(*) FROM IA_RestoreAccount
WHERE iYear=@y AND iMonth=@m AND cVouType IN (N'01',N'08',N'09',N'10',N'11',N'32',N'3201',N'26',N'27',N'20',N'21');

IF @n = 0
BEGIN
  -- 本月没有已记账的行：照常返回计数。
  SELECT N'restore_rows' k, 0 n UNION ALL SELECT N'subsidiary_month', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m;
  RETURN;
END

-- 发出商品：3201 的 En 发票 26/27（含后续月份）不在本次恢复集合里则拒绝（UI ~7818，U8.IA.U871.0002）。
IF EXISTS (
  SELECT 1
  FROM IA_RestoreAccount r
  INNER JOIN Ia_EnSubsidiary e
    ON e.iSrcID = r.ID
   AND e.cSrcVouType = r.cVouType
   AND (e.iSrcID <> e.InID OR e.bRdFlag <> 1)
   AND e.cVouType NOT IN (N'05', N'06', N'5', N'6', N'3201')
   AND e.iYear = @y
  LEFT JOIN IA_RestoreAccount chk
    ON chk.AutoID = e.AutoID AND chk.cVouType = e.cVouType
  WHERE r.cVouType IN (N'05', N'06', N'5', N'6', N'3201')
    AND chk.AutoID IS NULL)
  THROW 50011, N'cannot 恢复记账: 发出商品 3201 still has 发票 26/27 (or later-month En OUT) not in this restore set. Unpost invoices with the 3201.', 1;

SET @errmsg = NULL;
EXEC IA_sp_RestoreAccGen @y, @m, @errmsg OUTPUT, 0;
IF NULLIF(LTRIM(@errmsg),N'') IS NOT NULL
  BEGIN SET @raise = N'RestoreAccGen: ' + ISNULL(@errmsg, N''); THROW 50000, @raise, 1; END

-- 出入库调整单（20/21）。IA_sp_RestoreAccGen 的两段处理都排除这两类，什么也不做；U8 界面另外清调整单行的记账人和日期、
-- 删分项、刷新表头时间戳（CHHSSQL ~7707）。其余照 RestoreAccGen「按仓库核算」的公式：回退汇总表本月的发生额，删明细账行。
-- 记账时系统自动生成的调整单（cBusType=记账，生成时已盖记账人，下次记账会再生成）整张删除；手工调整单保留，只清记账人。
IF EXISTS (SELECT 1 FROM IA_RestoreAccount WHERE iYear=@y AND iMonth=@m AND cVouType IN (N'20',N'21'))
BEGIN
  IF OBJECT_ID(N'tempdb..#just_rows') IS NOT NULL DROP TABLE #just_rows;
  SELECT AutoID, JustID, cVouCode, cVouType, cBusType INTO #just_rows
  FROM IA_RestoreAccount WHERE iYear=@y AND iMonth=@m AND cVouType IN (N'20',N'21');

  UPDATE s SET
    iNum = ISNULL(s.iNum,0) - ISNULL(t.InNum,0) + ISNULL(t.OutNum,0),
    iINum = ISNULL(s.iINum,0) - ISNULL(t.InNum,0), iONum = ISNULL(s.iONum,0) - ISNULL(t.OutNum,0),
    iMoney = ISNULL(s.iMoney,0) - ISNULL(t.InPrice,0) + ISNULL(t.OutPrice,0),
    iIMoney = ISNULL(s.iIMoney,0) - ISNULL(t.InPrice,0), iOMoney = ISNULL(s.iOMoney,0) - ISNULL(t.OutPrice,0),
    iDif = ISNULL(s.iDif,0) - ISNULL(t.InDif,0) + ISNULL(t.OutDif,0),
    iIDif = ISNULL(s.iIDif,0) - ISNULL(t.InDif,0), iODif = ISNULL(s.iODif,0) - ISNULL(t.OutDif,0),
    iAbsIMoney = ABS(ISNULL(ABS(s.iAbsIMoney),0) - ISNULL(ABS(t.InAbsPrice),0)),
    iAbsOMoney = ABS(ISNULL(ABS(s.iAbsOMoney),0) - ISNULL(ABS(t.OutAbsPrice),0)),
    iAbsNum = ABS(ABS(ISNULL(s.iAbsNum,0) - ISNULL(t.InNum,0) + ISNULL(t.OutNum,0))),
    iAbsMoney = ABS(ABS(ISNULL(s.iAbsMoney,0) - ABS(ISNULL(t.InAbsPrice,0)) + ABS(ISNULL(t.OutAbsPrice,0))))
  FROM IA_Summary s
  INNER JOIN (
    SELECT r.cWhCode, r.cInvCode,
      ISNULL(CASE WHEN i.bCheckFree1=1 THEN r.cFree1 ELSE N'' END, N'') cFree1, ISNULL(CASE WHEN i.bCheckFree2=1 THEN r.cFree2 ELSE N'' END, N'') cFree2,
      ISNULL(CASE WHEN i.bCheckFree3=1 THEN r.cFree3 ELSE N'' END, N'') cFree3, ISNULL(CASE WHEN i.bCheckFree4=1 THEN r.cFree4 ELSE N'' END, N'') cFree4,
      ISNULL(CASE WHEN i.bCheckFree5=1 THEN r.cFree5 ELSE N'' END, N'') cFree5, ISNULL(CASE WHEN i.bCheckFree6=1 THEN r.cFree6 ELSE N'' END, N'') cFree6,
      ISNULL(CASE WHEN i.bCheckFree7=1 THEN r.cFree7 ELSE N'' END, N'') cFree7, ISNULL(CASE WHEN i.bCheckFree8=1 THEN r.cFree8 ELSE N'' END, N'') cFree8,
      ISNULL(CASE WHEN i.bCheckFree9=1 THEN r.cFree9 ELSE N'' END, N'') cFree9, ISNULL(CASE WHEN i.bCheckFree10=1 THEN r.cFree10 ELSE N'' END, N'') cFree10,
      ISNULL(CASE WHEN i.bCheckBatch=1 AND r.cValueType IN (N'全月平均法',N'移动平均法',N'先进先出法',N'后进先出法') THEN r.cBatchia ELSE N'' END, N'') cBatchia,
      SUM(ISNULL(r.iAInQuantity,0)) InNum, SUM(ISNULL(r.iAOutQuantity,0)) OutNum,
      SUM(ISNULL(r.iAInPrice,0)) InPrice, SUM(ISNULL(r.iAOutPrice,0)) OutPrice,
      SUM(ABS(ISNULL(r.iAInPrice,0))) InAbsPrice, SUM(ABS(ISNULL(r.iAOutPrice,0))) OutAbsPrice,
      SUM(CASE WHEN r.bRdFlag=1 THEN ISNULL(r.iDebitDifCost,0) - ISNULL(r.iCreditDifCost,0) ELSE CAST(0 AS float) END) InDif,
      SUM(CASE WHEN r.bRdFlag=0 THEN ISNULL(r.iCreditDifCost,0) - ISNULL(r.iDebitDifCost,0) ELSE CAST(0 AS float) END) OutDif
    FROM IA_RestoreAccount r LEFT JOIN Inventory i ON i.cInvCode=r.cInvCode
    WHERE r.iYear=@y AND r.iMonth=@m AND r.cVouType IN (N'20',N'21') AND LEFT(ISNULL(r.cBusType,N''),2) <> N'直运'
    GROUP BY r.cWhCode, r.cInvCode,
      ISNULL(CASE WHEN i.bCheckFree1=1 THEN r.cFree1 ELSE N'' END, N''), ISNULL(CASE WHEN i.bCheckFree2=1 THEN r.cFree2 ELSE N'' END, N''),
      ISNULL(CASE WHEN i.bCheckFree3=1 THEN r.cFree3 ELSE N'' END, N''), ISNULL(CASE WHEN i.bCheckFree4=1 THEN r.cFree4 ELSE N'' END, N''),
      ISNULL(CASE WHEN i.bCheckFree5=1 THEN r.cFree5 ELSE N'' END, N''), ISNULL(CASE WHEN i.bCheckFree6=1 THEN r.cFree6 ELSE N'' END, N''),
      ISNULL(CASE WHEN i.bCheckFree7=1 THEN r.cFree7 ELSE N'' END, N''), ISNULL(CASE WHEN i.bCheckFree8=1 THEN r.cFree8 ELSE N'' END, N''),
      ISNULL(CASE WHEN i.bCheckFree9=1 THEN r.cFree9 ELSE N'' END, N''), ISNULL(CASE WHEN i.bCheckFree10=1 THEN r.cFree10 ELSE N'' END, N''),
      ISNULL(CASE WHEN i.bCheckBatch=1 AND r.cValueType IN (N'全月平均法',N'移动平均法',N'先进先出法',N'后进先出法') THEN r.cBatchia ELSE N'' END, N'')
  ) t ON s.cWhCode=t.cWhCode AND s.cInvCode=t.cInvCode
    AND ISNULL(s.cFree1,N'')=t.cFree1 AND ISNULL(s.cFree2,N'')=t.cFree2 AND ISNULL(s.cFree3,N'')=t.cFree3
    AND ISNULL(s.cFree4,N'')=t.cFree4 AND ISNULL(s.cFree5,N'')=t.cFree5 AND ISNULL(s.cFree6,N'')=t.cFree6
    AND ISNULL(s.cFree7,N'')=t.cFree7 AND ISNULL(s.cFree8,N'')=t.cFree8 AND ISNULL(s.cFree9,N'')=t.cFree9
    AND ISNULL(s.cFree10,N'')=t.cFree10 AND ISNULL(s.cBatchia,N'')=t.cBatchia
  WHERE s.iYear=@y AND s.iMonth=@m AND ISNULL(s.iDirect,0)=0;

  IF OBJECT_ID(N'IA_Subsidiary_Item', N'U') IS NOT NULL
    DELETE x FROM IA_Subsidiary_Item x INNER JOIN #just_rows j ON x.iSubID=j.AutoID;
  IF OBJECT_ID(N'IA_Subsidiary_ItemOut', N'U') IS NOT NULL
    DELETE x FROM IA_Subsidiary_ItemOut x INNER JOIN #just_rows j ON x.iSubID=j.AutoID;
  IF OBJECT_ID(N'IA_Subsidiary_STSub', N'U') IS NOT NULL
    DELETE x FROM IA_Subsidiary_STSub x INNER JOIN #just_rows j ON x.iIASubID=j.AutoID;
  DELETE x FROM IA_Subsidiary x INNER JOIN #just_rows j ON x.AutoID=j.AutoID AND x.cVouType=j.cVouType WHERE x.iYear=@y;

  IF OBJECT_ID(N'IA_JustInVouchs_Item', N'U') IS NOT NULL
    DELETE x FROM IA_JustInVouchs_Item x INNER JOIN #just_rows j ON x.iJustAutoID=j.JustID
    WHERE ISNULL(j.cBusType,N'') <> N'分项调整';

  DELETE l FROM JustInVouchs l
  INNER JOIN #just_rows j ON l.AutoID=j.JustID
  WHERE j.cBusType=N'记账';
  DELETE h FROM JustInVouch h
  WHERE h.cJVCode IN (SELECT cVouCode FROM #just_rows)
    AND NOT EXISTS (SELECT 1 FROM JustInVouchs l WHERE l.cJVCode=h.cJVCode);
  UPDATE JustInVouchs SET cBAccounter=NULL, dKeepDate=NULL WHERE AutoID IN (SELECT JustID FROM #just_rows);
  UPDATE JustInVouch SET cBusType=cBusType WHERE cJVCode IN (SELECT cVouCode FROM #just_rows);
  DROP TABLE #just_rows;
END

-- UI ~7722–7788: clear rdrecords stamp; keep iunitcost/iPrice/iAPrice/faCost only if bMoneyFlag=1 and MoneySrc=0.
UPDATE RdRecords01 SET cbAccounter=NULL, dbkeepdate=NULL,
  iunitcost = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iunitcost ELSE NULL END,
  iPrice    = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iPrice    ELSE NULL END,
  faCost    = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.faCost    ELSE NULL END,
  iAPrice   = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iAPrice   ELSE NULL END
FROM RdRecords01 s INNER JOIN IA_RestoreAccount r ON s.autoid=r.id WHERE r.cVouType=N'01';

UPDATE RdRecords08 SET cbAccounter=NULL, dbkeepdate=NULL,
  iunitcost = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iunitcost ELSE NULL END,
  iPrice    = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iPrice    ELSE NULL END
FROM RdRecords08 s INNER JOIN IA_RestoreAccount r ON s.autoid=r.id WHERE r.cVouType=N'08';

UPDATE RdRecords09 SET cbAccounter=NULL, dbkeepdate=NULL,
  iunitcost = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iunitcost ELSE NULL END,
  iPrice    = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iPrice    ELSE NULL END
FROM RdRecords09 s INNER JOIN IA_RestoreAccount r ON s.autoid=r.id WHERE r.cVouType=N'09';

UPDATE RdRecords10 SET cbAccounter=NULL, dbkeepdate=NULL,
  iunitcost = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iunitcost ELSE NULL END,
  iPrice    = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iPrice    ELSE NULL END
FROM RdRecords10 s INNER JOIN IA_RestoreAccount r ON s.autoid=r.id WHERE r.cVouType=N'10';

UPDATE RdRecords11 SET cbAccounter=NULL, dbkeepdate=NULL,
  iunitcost = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iunitcost ELSE NULL END,
  iPrice    = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iPrice    ELSE NULL END
FROM RdRecords11 s INNER JOIN IA_RestoreAccount r ON s.autoid=r.id WHERE r.cVouType=N'11';

UPDATE RdRecords32 SET cbAccounter=NULL, dbkeepdate=NULL,
  iunitcost = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iunitcost ELSE NULL END,
  iPrice    = CASE WHEN (r.bmoneyflag=1 AND ISNULL(r.MoneySrc,0)=0) THEN s.iPrice    ELSE NULL END
FROM RdRecords32 s INNER JOIN IA_RestoreAccount r ON s.autoid=r.id WHERE r.cVouType IN (N'32',N'3201');

-- UI ~7694–7698：发票去记账标记、补回 IA_SA_UnAccountVouch。En 行 isen=1 用 ValueID（=SaleBillVouchs.AutoID）。
UPDATE SaleBillVouchs SET cbAccounter=NULL, dKeepDate=NULL
WHERE AutoID IN (
  SELECT CASE WHEN isen = 1 THEN ISNULL(ValueID, ID) ELSE ID END
  FROM IA_RestoreAccount
  WHERE cVouType IN (N'26', N'27', N'28', N'29'));

DELETE IA_SA_UnAccountVouch
FROM IA_SA_UnAccountVouch
INNER JOIN IA_RestoreAccount r
  ON IA_SA_UnAccountVouch.IDSUN = CASE WHEN r.isen = 1 THEN ISNULL(r.ValueID, r.ID) ELSE r.ID END
 AND IA_SA_UnAccountVouch.cVouTypeUN = r.cVouType
WHERE r.cVouType IN (N'26', N'27', N'28', N'29');

INSERT INTO IA_SA_UnAccountVouch(IDUN, IDSUN, cVouTypeUN, cBusTypeUN)
SELECT DISTINCT ipzid,
  CASE WHEN isen = 1 THEN ISNULL(ValueID, ID) ELSE ID END,
  cvoutype, cbustype
FROM IA_RestoreAccount
WHERE cvoutype IN (N'26', N'27', N'28', N'29');

DELETE IA_ST_UnAccountVouch01 FROM IA_ST_UnAccountVouch01 INNER JOIN IA_RestoreAccount r
  ON IA_ST_UnAccountVouch01.IDSUN=r.ID AND IA_ST_UnAccountVouch01.cVouTypeUN=r.cVouType WHERE r.cVouType=N'01';
INSERT INTO IA_ST_UnAccountVouch01(IDUN,IDSUN,cVouTypeUN,cBusTypeUN)
SELECT DISTINCT ipzid, id, cvoutype, cbustype FROM IA_RestoreAccount WHERE cvoutype=N'01';

DELETE IA_ST_UnAccountVouch08 FROM IA_ST_UnAccountVouch08 INNER JOIN IA_RestoreAccount r
  ON IA_ST_UnAccountVouch08.IDSUN=r.ID AND IA_ST_UnAccountVouch08.cVouTypeUN=r.cVouType WHERE r.cVouType=N'08';
INSERT INTO IA_ST_UnAccountVouch08(IDUN,IDSUN,cVouTypeUN,cBusTypeUN)
SELECT DISTINCT ipzid, id, cvoutype, cbustype FROM IA_RestoreAccount WHERE cvoutype=N'08';

DELETE IA_ST_UnAccountVouch09 FROM IA_ST_UnAccountVouch09 INNER JOIN IA_RestoreAccount r
  ON IA_ST_UnAccountVouch09.IDSUN=r.ID AND IA_ST_UnAccountVouch09.cVouTypeUN=r.cVouType WHERE r.cVouType=N'09';
INSERT INTO IA_ST_UnAccountVouch09(IDUN,IDSUN,cVouTypeUN,cBusTypeUN)
SELECT DISTINCT ipzid, id, cvoutype, cbustype FROM IA_RestoreAccount WHERE cvoutype=N'09';

DELETE IA_ST_UnAccountVouch10 FROM IA_ST_UnAccountVouch10 INNER JOIN IA_RestoreAccount r
  ON IA_ST_UnAccountVouch10.IDSUN=r.ID AND IA_ST_UnAccountVouch10.cVouTypeUN=r.cVouType WHERE r.cVouType=N'10';
INSERT INTO IA_ST_UnAccountVouch10(IDUN,IDSUN,cVouTypeUN,cBusTypeUN)
SELECT DISTINCT ipzid, id, cvoutype, cbustype FROM IA_RestoreAccount WHERE cvoutype=N'10';

DELETE IA_ST_UnAccountVouch11 FROM IA_ST_UnAccountVouch11 INNER JOIN IA_RestoreAccount r
  ON IA_ST_UnAccountVouch11.IDSUN=r.ID AND IA_ST_UnAccountVouch11.cVouTypeUN=r.cVouType WHERE r.cVouType=N'11';
INSERT INTO IA_ST_UnAccountVouch11(IDUN,IDSUN,cVouTypeUN,cBusTypeUN)
SELECT DISTINCT ipzid, id, cvoutype, cbustype FROM IA_RestoreAccount WHERE cvoutype=N'11';

DELETE IA_ST_UnAccountVouch32 FROM IA_ST_UnAccountVouch32 INNER JOIN IA_RestoreAccount r
  ON IA_ST_UnAccountVouch32.IDSUN=r.ID WHERE r.cVouType IN (N'32',N'3201');
INSERT INTO IA_ST_UnAccountVouch32(IDUN,IDSUN,cVouTypeUN,cBusTypeUN)
SELECT DISTINCT ipzid, id, N'32', cbustype FROM IA_RestoreAccount WHERE cvoutype IN (N'32',N'3201');

SELECT N'restore_rows' k, @n n
UNION ALL SELECT N'subsidiary_month', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'summary_month', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'unacc01', COUNT(*) FROM IA_ST_UnAccountVouch01
UNION ALL SELECT N'unacc08', COUNT(*) FROM IA_ST_UnAccountVouch08
UNION ALL SELECT N'unacc09', COUNT(*) FROM IA_ST_UnAccountVouch09
UNION ALL SELECT N'unacc10', COUNT(*) FROM IA_ST_UnAccountVouch10
UNION ALL SELECT N'unacc11', COUNT(*) FROM IA_ST_UnAccountVouch11
UNION ALL SELECT N'unacc32', COUNT(*) FROM IA_ST_UnAccountVouch32
UNION ALL SELECT N'ensubsidiary_month', COUNT(*) FROM IA_EnSubsidiary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'sbv_still_posted', COUNT(*) FROM SaleBillVouchs s
  INNER JOIN IA_RestoreAccount a ON s.AutoID = CASE WHEN a.isen=1 THEN ISNULL(a.ValueID,a.ID) ELSE a.ID END
  AND a.cVouType IN (N'26',N'27') WHERE ISNULL(s.cbAccounter,N'')<>N''
UNION ALL SELECT N'unacc_SA', COUNT(*) FROM IA_SA_UnAccountVouch
UNION ALL SELECT N'just_still_posted', COUNT(*) FROM JustInVouchs l
  INNER JOIN IA_RestoreAccount a ON l.AutoID=a.JustID AND a.cVouType IN (N'20',N'21') WHERE ISNULL(l.cBAccounter,N'')<>N''
UNION ALL SELECT N'rd_still_posted', (
  SELECT COUNT(*) FROM rdrecords01 r INNER JOIN IA_RestoreAccount a ON r.autoid=a.id AND a.cVouType=N'01' WHERE ISNULL(r.cbaccounter,N'')<>N''
)+(SELECT COUNT(*) FROM rdrecords08 r INNER JOIN IA_RestoreAccount a ON r.autoid=a.id AND a.cVouType=N'08' WHERE ISNULL(r.cbaccounter,N'')<>N'')
+(SELECT COUNT(*) FROM rdrecords09 r INNER JOIN IA_RestoreAccount a ON r.autoid=a.id AND a.cVouType=N'09' WHERE ISNULL(r.cbaccounter,N'')<>N'')
+(SELECT COUNT(*) FROM rdrecords10 r INNER JOIN IA_RestoreAccount a ON r.autoid=a.id AND a.cVouType=N'10' WHERE ISNULL(r.cbaccounter,N'')<>N'')
+(SELECT COUNT(*) FROM rdrecords11 r INNER JOIN IA_RestoreAccount a ON r.autoid=a.id AND a.cVouType=N'11' WHERE ISNULL(r.cbaccounter,N'')<>N'')
+(SELECT COUNT(*) FROM rdrecords32 r INNER JOIN IA_RestoreAccount a ON r.autoid=a.id AND a.cVouType IN (N'32',N'3201') WHERE ISNULL(r.cbaccounter,N'')<>N'');
