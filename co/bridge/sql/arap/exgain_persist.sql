-- U8 应收 / 应付 汇兑损益（9M）第二段：把第一段（exgain_create.sql）建好的 #HdsyNew 写进往来明细，
-- 不写 iAmount（U8 留空）。在桥调过 UpdateBillForAR 之后、同一连接同一事务里执行（不分 GO）。
-- 第一个结果集是本次的处理号清单（每个处理号一行：往来单位、单据类型、单号、行数、本币差额 = 借方 − 贷方），最后是 k、n 计数；
-- total 是全部处理行的借方 − 贷方合计（往来科目的净调整额）。
SET NOCOUNT ON;

DECLARE @flag nvarchar(4), @rows int;
SELECT @flag = flag FROM #exg_args;
IF ISNULL(@flag, N'') NOT IN (N'AR', N'AP') THROW 50001, N'9M flag must be AR or AP', 1;
IF OBJECT_ID(N'tempdb..#HdsyNew') IS NULL THROW 50029, N'9M working table missing', 1;

IF @flag = N'AR'
BEGIN
  INSERT INTO Ar_Detail (
    cDwCode, iPeriod, dRegDate, dPZDate, dVouchDate, cVouchType, cVouchID, cCoVouchType, cCoVouchID, cProcStyle,
    cDigest, cexch_name, iExchRate, cDeptCode, cPerson, cInvCode, cCode, cItem_Class, cItemCode, cItemName,
    cContractType, cContractID, cSign, iPrice, cOrderNo, cSSCode, cPayCode, bPrepay,
    cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7, cDefine8, cDefine9, cDefine10,
    cDefine11, cDefine12, cDefine13, cDefine14, cDefine15, cDefine16,
    cDefine22, cDefine23, cDefine24, cDefine25, cDefine26, cDefine27, cDefine28, cDefine29, cDefine30,
    cDefine31, cDefine32, cDefine33, cDefine34,
    cOperator, cCheckMan, iOrderType, cDLCode, bCredit, dGatheringDate, idlsid, iClosesID, iCoClosesID, BalancesGUID, iBVid,
    iDAmount, iDAmount_f, iDAmount_s, iCAmount, iCAmount_f, iCAmount_s, cCancelNo, cFlag, cBusType, iFlag)
  SELECT
    cDwCode, iPeriod, dRegDate, dPZDate, dVouchDate, cVouchType, cVouchID, cCoVouchType, cCoVouchID, cProcStyle,
    cDigest, cexch_name, iExchRate, cDeptCode, cPerson, cInvCode, cCode, cItem_Class, cItemCode, cItemName,
    cContractType, cContractID, cSign, iPrice, cOrderNo, cSSCode, cPayCode, bPrepay,
    cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7, cDefine8, cDefine9, cDefine10,
    cDefine11, cDefine12, cDefine13, cDefine14, cDefine15, cDefine16,
    cDefine22, cDefine23, cDefine24, cDefine25, cDefine26, cDefine27, cDefine28, cDefine29, cDefine30,
    cDefine31, cDefine32, cDefine33, cDefine34,
    cOperator, cCheckMan, iOrderType, cDLCode, bCredit, dGatheringDate, idlsid, iClosesID, iCoClosesID, BalancesGUID, iBVid,
    iDAmount, iDAmount_f, iDAmount_s, iCAmount, iCAmount_f, iCAmount_s, cCancelNo, cFlag, cBusType, iFlag
  FROM #HdsyNew
  ORDER BY cCancelNo, ID;
  SET @rows = @@ROWCOUNT;
END
ELSE
BEGIN
  INSERT INTO Ap_Detail (
    cDwCode, iPeriod, dRegDate, dPZDate, dVouchDate, cVouchType, cVouchID, cCoVouchType, cCoVouchID, cProcStyle,
    cDigest, cexch_name, iExchRate, cDeptCode, cPerson, cInvCode, cCode, cItem_Class, cItemCode, cItemName,
    cContractType, cContractID, cSign, iPrice, cOrderNo, cSSCode, cPayCode, bPrepay,
    cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7, cDefine8, cDefine9, cDefine10,
    cDefine11, cDefine12, cDefine13, cDefine14, cDefine15, cDefine16,
    cDefine22, cDefine23, cDefine24, cDefine25, cDefine26, cDefine27, cDefine28, cDefine29, cDefine30,
    cDefine31, cDefine32, cDefine33, cDefine34,
    cOperator, cCheckMan, iOrderType, cDLCode, bCredit, dGatheringDate, idlsid, iClosesID, iCoClosesID, BalancesGUID, iBVid,
    iDAmount, iDAmount_f, iDAmount_s, iCAmount, iCAmount_f, iCAmount_s, cCancelNo, cFlag, cBusType, iFlag)
  SELECT
    cDwCode, iPeriod, dRegDate, dPZDate, dVouchDate, cVouchType, cVouchID, cCoVouchType, cCoVouchID, cProcStyle,
    cDigest, cexch_name, iExchRate, cDeptCode, cPerson, cInvCode, cCode, cItem_Class, cItemCode, cItemName,
    cContractType, cContractID, cSign, iPrice, cOrderNo, cSSCode, cPayCode, bPrepay,
    cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7, cDefine8, cDefine9, cDefine10,
    cDefine11, cDefine12, cDefine13, cDefine14, cDefine15, cDefine16,
    cDefine22, cDefine23, cDefine24, cDefine25, cDefine26, cDefine27, cDefine28, cDefine29, cDefine30,
    cDefine31, cDefine32, cDefine33, cDefine34,
    cOperator, cCheckMan, iOrderType, cDLCode, bCredit, dGatheringDate, idlsid, iClosesID, iCoClosesID, BalancesGUID, iBVid,
    iDAmount, iDAmount_f, iDAmount_s, iCAmount, iCAmount_f, iCAmount_s, cCancelNo, cFlag, cBusType, iFlag
  FROM #HdsyNew
  ORDER BY cCancelNo, ID;
  SET @rows = @@ROWCOUNT;
END

SELECT cCancelNo AS cancel_no, cDwCode AS partner, cVouchType AS vtype, cVouchID AS vid,
  CONVERT(varchar(20), COUNT(*)) AS lines, CONVERT(varchar(40), CONVERT(decimal(18, 2), SUM(iDAmount) - SUM(iCAmount))) AS diff
FROM #HdsyNew
GROUP BY cCancelNo, cDwCode, cVouchType, cVouchID
ORDER BY cCancelNo;

SELECT k, n FROM (VALUES
  (N'inserted', CONVERT(nvarchar(20), @rows)),
  (N'batches', CONVERT(nvarchar(20), (SELECT COUNT(DISTINCT cCancelNo) FROM #HdsyNew))),
  (N'total', CONVERT(nvarchar(40), (SELECT CONVERT(decimal(18, 2), SUM(iDAmount) - SUM(iCAmount)) FROM #HdsyNew))),
  (N'first', (SELECT MIN(cCancelNo) FROM #HdsyNew)),
  (N'last', (SELECT MAX(cCancelNo) FROM #HdsyNew))) v (k, n);

DROP TABLE #HdsyNew;
IF OBJECT_ID(N'tempdb..#Hdsy') IS NOT NULL DROP TABLE #Hdsy;
IF OBJECT_ID(N'tempdb..#Num') IS NOT NULL DROP TABLE #Num;
IF OBJECT_ID(N'tempdb..#exg_skip') IS NOT NULL DROP TABLE #exg_skip;
