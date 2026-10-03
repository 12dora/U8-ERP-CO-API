-- U8 取消汇兑损益（9M）第一段：带锁取出要取消的处理行、闸门、恢复余额，填好发票回写临时表。
-- 写入与 U8「取消操作」取消汇兑损益时执行的 SQL 一致（测试账套实测核对），由桥在请求连接、请求事务里整批执行（不分 GO）。
-- 参数取自调用方先建好的临时表（ArapExGainSql.Prepare）：
--   #exg_args (flag AR|AP, reg_date 登记日期, by_date 1=取消该登记日期全部未制单的 9M)
--   #exg_nos (cCancelNo)：要取消的处理号；by_date=1 时由本段按日期填。
--   #ap_SaleBillVouchHXdata / #ap_purbillHXdata (autoid, iexchsum, imoneysum)：本段填好后桥依次调 U8 的
--   clsWrite2Bill.UpdateBillForAR / UpdateBillForAP（同一连接），再执行第二段 exgain_cancel_end.sql 删行。
-- 闸门（任一不过整批拒绝，消息最后一个冒号后是处理号）：处理号不存在 50025、已制单 50026、期间已结账 50035、
-- 同一单据之后还有别的汇兑损益 50027、之后还有核销等其他处理 50028；按日期没有可取消的 50030、超过 1000 个 50031。
-- 只取消桥自己会做的处理号（应收 26 / 27 / R* / 48、应付 01 / 02 / P* / 49）：含进出口 RZ、代理进口等其他类型或 AR_RZDetail 行的
-- （U8 做的）按日期取消时跳过不动，按处理号取消时拒绝 50032。
-- 「保留线索」不做（CancelHdsy 的 PUSH 清单里没有）。第一个结果集是处理号清单（diff = 借方 − 贷方，同新增），最后是 k、n 计数。
SET NOCOUNT ON;

DECLARE @flag nvarchar(4), @reg nvarchar(10), @by_date int, @bad nvarchar(30), @msg nvarchar(200), @cnt int;
DECLARE @vpat nvarchar(10), @inv1 nvarchar(4), @inv2 nvarchar(4), @pay nvarchar(4), @skipped int;
SELECT @flag = flag, @reg = reg_date, @by_date = by_date FROM #exg_args;
IF ISNULL(@flag, N'') NOT IN (N'AR', N'AP') THROW 50001, N'9M flag must be AR or AP', 1;
SET @vpat = CASE WHEN @flag = N'AR' THEN N'R[0-9]' ELSE N'P[0-9]' END;
SET @inv1 = CASE WHEN @flag = N'AR' THEN N'26' ELSE N'01' END;
SET @inv2 = CASE WHEN @flag = N'AR' THEN N'27' ELSE N'02' END;
SET @pay = CASE WHEN @flag = N'AR' THEN N'48' ELSE N'49' END;
SET @skipped = 0;

IF ISNULL(@by_date, 0) = 1
BEGIN
  DELETE FROM #exg_nos;
  IF @flag = N'AR'
    INSERT INTO #exg_nos (cCancelNo)
    SELECT DISTINCT cCancelNo FROM Ar_Detail
    WHERE cProcStyle = N'9M' AND cFlag = N'AR' AND ISNULL(cPZid, N'') = N'' AND cCancelNo IS NOT NULL
      AND CONVERT(date, dRegDate) = CONVERT(date, @reg, 23);
  IF @flag = N'AP'
    INSERT INTO #exg_nos (cCancelNo)
    SELECT DISTINCT cCancelNo FROM Ap_Detail
    WHERE cProcStyle = N'9M' AND cFlag = N'AP' AND ISNULL(cPZid, N'') = N'' AND cCancelNo IS NOT NULL
      AND CONVERT(date, dRegDate) = CONVERT(date, @reg, 23);
END

-- 含桥不做的类型的处理号（U8 做的进出口 RZ 等）。
IF OBJECT_ID(N'tempdb..#exg_odd') IS NOT NULL DROP TABLE #exg_odd;
CREATE TABLE #exg_odd (cCancelNo nvarchar(30));
INSERT INTO #exg_odd (cCancelNo)
SELECT n.cCancelNo FROM #exg_nos n
WHERE (@flag = N'AR' AND EXISTS (SELECT 1 FROM Ar_Detail d WHERE d.cProcStyle = N'9M' AND d.cCancelNo = n.cCancelNo
        AND NOT (ISNULL(d.cFlag, N'') = @flag
                 AND (ISNULL(d.cCoVouchType, N'') IN (@inv1, @inv2, @pay) OR ISNULL(d.cCoVouchType, N'') LIKE @vpat))))
   OR (@flag = N'AP' AND EXISTS (SELECT 1 FROM Ap_Detail d WHERE d.cProcStyle = N'9M' AND d.cCancelNo = n.cCancelNo
        AND NOT (ISNULL(d.cFlag, N'') = @flag
                 AND (ISNULL(d.cCoVouchType, N'') IN (@inv1, @inv2, @pay) OR ISNULL(d.cCoVouchType, N'') LIKE @vpat))));
IF @flag = N'AR' AND OBJECT_ID(N'AR_RZDetail') IS NOT NULL
  INSERT INTO #exg_odd (cCancelNo)
  SELECT n.cCancelNo FROM #exg_nos n
  WHERE EXISTS (SELECT 1 FROM AR_RZDetail r WHERE r.cProcStyle = N'9M' AND r.cCancelNo = n.cCancelNo);
IF ISNULL(@by_date, 0) = 1
BEGIN
  DELETE n FROM #exg_nos n WHERE n.cCancelNo IN (SELECT cCancelNo FROM #exg_odd);
  SET @skipped = @@ROWCOUNT;
END
IF ISNULL(@by_date, 0) <> 1 OR NOT EXISTS (SELECT 1 FROM #exg_nos)
  SELECT TOP 1 @bad = cCancelNo FROM #exg_odd ORDER BY cCancelNo;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'9M batch has types the bridge does not make (RZ etc.): ' + @bad;
  THROW 50032, @msg, 1;
END

SELECT @cnt = COUNT(*) FROM #exg_nos;
IF @cnt = 0 THROW 50030, N'no unvouched 9M on reg_date', 1;
IF @cnt > 1000 THROW 50031, N'too many 9M batches', 1;

IF OBJECT_ID(N'tempdb..#CancelHdsy') IS NOT NULL DROP TABLE #CancelHdsy;
CREATE TABLE #CancelHdsy (
  Auto_ID int, cCancelNo nvarchar(30), cFlag nvarchar(4), cBusType nvarchar(20), cDwCode nvarchar(40),
  cVouchType nvarchar(10), cVouchID nvarchar(60), cCoVouchType nvarchar(10), cCoVouchID nvarchar(60),
  iPeriod int, dRegDate datetime, cPZid nvarchar(60), cInvCode nvarchar(60), iFlag int, iBVid int, iCoClosesID int,
  iDAmount money, iCAmount money, iDAmount_f money, iCAmount_f money, iDAmount_s float, iCAmount_s float,
  IJE decimal(30, 10), IWB decimal(30, 10), ISL decimal(30, 10), iInvID int, bd_c int
);
CREATE INDEX ix_CancelHdsy ON #CancelHdsy (cVouchType, cCoVouchType, iFlag);

-- 带锁快照（U8 读 AR_V_detail / Ap_detail；桥读要删的那张表本身）。
IF @flag = N'AR'
  INSERT INTO #CancelHdsy (Auto_ID, cCancelNo, cFlag, cBusType, cDwCode, cVouchType, cVouchID, cCoVouchType, cCoVouchID,
    iPeriod, dRegDate, cPZid, cInvCode, iFlag, iBVid, iCoClosesID, iDAmount, iCAmount, iDAmount_f, iCAmount_f, iDAmount_s, iCAmount_s)
  SELECT d.Auto_ID, d.cCancelNo, d.cFlag, d.cBusType, d.cDwCode, d.cVouchType, d.cVouchID, d.cCoVouchType, d.cCoVouchID,
    d.iPeriod, d.dRegDate, d.cPZid, d.cInvCode, d.iFlag, d.iBVid, d.iCoClosesID,
    ISNULL(d.iDAmount, 0), ISNULL(d.iCAmount, 0), ISNULL(d.iDAmount_f, 0), ISNULL(d.iCAmount_f, 0),
    ISNULL(d.iDAmount_s, 0), ISNULL(d.iCAmount_s, 0)
  FROM Ar_Detail d WITH (UPDLOCK, HOLDLOCK)
  WHERE d.cProcStyle = N'9M' AND d.cCancelNo IN (SELECT cCancelNo FROM #exg_nos)
    AND (d.cFlag = N'AR' OR d.cBusType = N'代理进口')
  ORDER BY d.Auto_ID;
IF @flag = N'AP'
  INSERT INTO #CancelHdsy (Auto_ID, cCancelNo, cFlag, cBusType, cDwCode, cVouchType, cVouchID, cCoVouchType, cCoVouchID,
    iPeriod, dRegDate, cPZid, cInvCode, iFlag, iBVid, iCoClosesID, iDAmount, iCAmount, iDAmount_f, iCAmount_f, iDAmount_s, iCAmount_s)
  SELECT d.Auto_ID, d.cCancelNo, d.cFlag, d.cBusType, d.cDwCode, d.cVouchType, d.cVouchID, d.cCoVouchType, d.cCoVouchID,
    d.iPeriod, d.dRegDate, d.cPZid, d.cInvCode, d.iFlag, d.iBVid, d.iCoClosesID,
    ISNULL(d.iDAmount, 0), ISNULL(d.iCAmount, 0), ISNULL(d.iDAmount_f, 0), ISNULL(d.iCAmount_f, 0),
    ISNULL(d.iDAmount_s, 0), ISNULL(d.iCAmount_s, 0)
  FROM Ap_Detail d WITH (UPDLOCK, HOLDLOCK)
  WHERE d.cProcStyle = N'9M' AND d.cCancelNo IN (SELECT cCancelNo FROM #exg_nos)
    AND (d.cFlag = N'AP' OR d.cBusType = N'代理进口')
  ORDER BY d.Auto_ID;

SELECT TOP 1 @bad = n.cCancelNo FROM #exg_nos n
WHERE NOT EXISTS (SELECT 1 FROM #CancelHdsy t WHERE t.cCancelNo = n.cCancelNo) ORDER BY n.cCancelNo;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'9M batch not found: ' + @bad;
  THROW 50025, @msg, 1;
END

-- 带锁快照里再核一次类型（防御）。
SELECT TOP 1 @bad = cCancelNo FROM #CancelHdsy
WHERE NOT (ISNULL(cFlag, N'') = @flag AND (ISNULL(cCoVouchType, N'') IN (@inv1, @inv2, @pay) OR ISNULL(cCoVouchType, N'') LIKE @vpat))
ORDER BY cCancelNo;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'9M batch has types the bridge does not make (RZ etc.): ' + @bad;
  THROW 50032, @msg, 1;
END

SELECT TOP 1 @bad = cCancelNo FROM #CancelHdsy WHERE ISNULL(cPZid, N'') <> N'' ORDER BY cCancelNo;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'9M cPZid set, CancelAccVouch first: ' + @bad;
  THROW 50026, @msg, 1;
END

SELECT TOP 1 @bad = t.cCancelNo FROM #CancelHdsy t
INNER JOIN GL_mend g ON g.iyear = YEAR(t.dRegDate) AND g.iperiod = t.iPeriod
WHERE (@flag = N'AR' AND ISNULL(g.bflag_AR, 0) = 1) OR (@flag = N'AP' AND ISNULL(g.bflag_AP, 0) = 1)
ORDER BY t.cCancelNo;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'AR/AP period already closed (GL_mend): ' + @bad;
  THROW 50035, @msg, 1;
END

-- 同一（单据类型、单号、往来单位）之后又做过汇兑损益（本次一并取消的不算）。
IF @flag = N'AR'
  SELECT TOP 1 @bad = t2.cCancelNo FROM Ar_Detail t1
  INNER JOIN #CancelHdsy t2 ON t1.cCoVouchType = t2.cCoVouchType AND t1.cCoVouchID = t2.cCoVouchID AND t1.cDwCode = t2.cDwCode
  WHERE t1.cProcStyle = N'9M' AND t1.cFlag = N'AR' AND t1.Auto_ID > t2.Auto_ID AND t1.iPeriod >= t2.iPeriod
    AND ISNULL(t1.cCancelNo, N'') NOT IN (SELECT cCancelNo FROM #exg_nos)
  ORDER BY t2.cCancelNo;
IF @flag = N'AP'
  SELECT TOP 1 @bad = t2.cCancelNo FROM Ap_Detail t1
  INNER JOIN #CancelHdsy t2 ON t1.cCoVouchType = t2.cCoVouchType AND t1.cCoVouchID = t2.cCoVouchID AND t1.cDwCode = t2.cDwCode
  WHERE t1.cProcStyle = N'9M' AND t1.cFlag = N'AP' AND t1.Auto_ID > t2.Auto_ID AND t1.iPeriod >= t2.iPeriod
    AND ISNULL(t1.cCancelNo, N'') NOT IN (SELECT cCancelNo FROM #exg_nos)
  ORDER BY t2.cCancelNo;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'later 9M on same co-doc: ' + @bad;
  THROW 50027, @msg, 1;
END

-- 之后还有审核登记（Sign）以外的其他处理（U8 同样拒绝）：核销、红票对冲、转账、并账等。
-- Sign 行的 cProcStyle 是单据类型本身（与取消核销同一判断，docs/u8-notes.md）。
IF @flag = N'AR'
  SELECT TOP 1 @bad = t2.cCancelNo FROM Ar_Detail t1
  INNER JOIN #CancelHdsy t2 ON t1.cCoVouchType = t2.cCoVouchType AND t1.cCoVouchID = t2.cCoVouchID
    AND t1.cDwCode = t2.cDwCode AND t1.cFlag = t2.cFlag
  WHERE t1.Auto_ID > t2.Auto_ID AND ISNULL(t1.cProcStyle, N'') <> ISNULL(t1.cVouchType, N'')
    AND ISNULL(t1.cProcStyle, N'') NOT IN (N'9M', N'26', N'27', N'R0', N'48', N'49', N'01', N'02', N'P0')
  ORDER BY t2.cCancelNo;
IF @flag = N'AP'
  SELECT TOP 1 @bad = t2.cCancelNo FROM Ap_Detail t1
  INNER JOIN #CancelHdsy t2 ON t1.cCoVouchType = t2.cCoVouchType AND t1.cCoVouchID = t2.cCoVouchID
    AND t1.cDwCode = t2.cDwCode AND t1.cFlag = t2.cFlag
  WHERE t1.Auto_ID > t2.Auto_ID AND ISNULL(t1.cProcStyle, N'') <> ISNULL(t1.cVouchType, N'')
    AND ISNULL(t1.cProcStyle, N'') NOT IN (N'9M', N'26', N'27', N'R0', N'48', N'49', N'01', N'02', N'P0')
  ORDER BY t2.cCancelNo;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'Bill_Settlement_Deal_Else: ' + @bad;
  THROW 50028, @msg, 1;
END

-- 恢复金额：IJE = −(借 + 贷)，原币 / 数量同理；收付款单行按 iCoClosesID 找，其余按 iBVid。
UPDATE #CancelHdsy SET
  iInvID = CASE WHEN cCoVouchType IN (N'48', N'49') THEN iCoClosesID ELSE iBVid END,
  cInvCode = CASE WHEN cCoVouchType IN (N'48', N'49') THEN N'UnHx' ELSE cInvCode END,
  IJE = -(iDAmount + iCAmount), IWB = -(iDAmount_f + iCAmount_f), ISL = -(iDAmount_s + iCAmount_s);

UPDATE t SET bd_c = v.bd_c
FROM #CancelHdsy t
INNER JOIN Ap_Vouch v ON t.cVouchID = v.cVouchID AND t.cVouchType = v.cVouchType AND t.cFlag = v.cFlag;
UPDATE #CancelHdsy SET IJE = -IJE, IWB = -IWB WHERE (cFlag = N'AR' AND bd_c = 0) OR (cFlag = N'AP' AND bd_c = 1);

-- 应收单 / 应付单表头余额。
UPDATE v SET iRAmount = v.iRAmount + t.IJE, iRAmount_f = v.iRAmount_f + t.IWB
FROM Ap_Vouch v
INNER JOIN (SELECT cVouchID, cVouchType, cFlag, SUM(IJE) AS IJE, SUM(IWB) AS IWB
            FROM #CancelHdsy GROUP BY cVouchID, cVouchType, cFlag) t
  ON t.cVouchID = v.cVouchID AND t.cVouchType = v.cVouchType AND t.cFlag = v.cFlag;
UPDATE v SET iRAmount_s = CASE WHEN v.iAmount_f <> 0 THEN v.iAmount_s * v.iRAmount_f / v.iAmount_f ELSE v.iRAmount_f END
FROM Ap_Vouch v
INNER JOIN (SELECT DISTINCT cVouchID, cVouchType, cFlag FROM #CancelHdsy) t
  ON t.cVouchID = v.cVouchID AND t.cVouchType = v.cVouchType AND t.cFlag = v.cFlag;

-- 收付款单行余额（RecoverOPForHdsy）。
UPDATE b SET iRAmt_f = b.iRAmt_f + t.IWB, iRAmt = b.iRAmt + t.IJE, iRAmt_s = b.iRAmt_s + t.ISL
FROM Ap_CloseBills b
INNER JOIN (SELECT iInvID, SUM(IWB) AS IWB, SUM(IJE) AS IJE, SUM(ISL) AS ISL FROM #CancelHdsy
            WHERE cVouchType IN (N'48', N'49') AND iFlag < 3 AND cInvCode = N'UnHx' GROUP BY iInvID) t
  ON b.ID = t.iInvID;

-- 销售发票（26–29）→ UpdateBillForAR；采购发票（01–06）→ UpdateBillForAP。imoneysum = −IJE，桥在本段之后调用。
-- 行号 0（表头登记）不进表（U8 的 UpdateBillForAR 也不回写它）。
TRUNCATE TABLE #ap_SaleBillVouchHXdata;
INSERT INTO #ap_SaleBillVouchHXdata (autoid, iexchsum, imoneysum)
SELECT t.iInvID, 0, CONVERT(decimal(29, 6), -1 * t.IJE)
FROM (SELECT cVouchType, cVouchID, iInvID, SUM(IJE) AS IJE FROM #CancelHdsy
      WHERE cVouchType IN (N'26', N'27', N'28', N'29') AND iFlag < 3 AND ISNULL(cInvCode, N'') <> N'' AND iInvID > 0
      GROUP BY cVouchType, cVouchID, iInvID) t
INNER JOIN SaleBillVouch v ON v.cSBVCode = t.cVouchID AND v.cVouchType = t.cVouchType;

TRUNCATE TABLE #ap_purbillHXdata;
INSERT INTO #ap_purbillHXdata (autoid, iexchsum, imoneysum)
SELECT t.iInvID, 0, CONVERT(decimal(29, 6), -1 * t.IJE)
FROM (SELECT cVouchType, cVouchID, iInvID, SUM(IJE) AS IJE FROM #CancelHdsy
      WHERE cVouchType IN (N'01', N'02', N'03', N'04', N'05', N'06') AND iFlag < 3 AND ISNULL(cInvCode, N'') <> N'' AND iInvID > 0
      GROUP BY cVouchType, cVouchID, iInvID) t
INNER JOIN PurBillVouch p ON p.cPBVCode = t.cVouchID AND p.cPBVBillType = t.cVouchType;

SELECT cCancelNo AS cancel_no, MAX(cDwCode) AS partner, MAX(cCoVouchType) AS vtype, MAX(cCoVouchID) AS vid,
  CONVERT(varchar(20), COUNT(*)) AS lines, CONVERT(varchar(40), CONVERT(decimal(18, 2), SUM(iDAmount) - SUM(iCAmount))) AS diff
FROM #CancelHdsy
GROUP BY cCancelNo
ORDER BY cCancelNo;

SELECT k, n FROM (VALUES
  (N'rows', CONVERT(nvarchar(20), (SELECT COUNT(*) FROM #CancelHdsy))),
  (N'batches', CONVERT(nvarchar(20), (SELECT COUNT(DISTINCT cCancelNo) FROM #CancelHdsy))),
  (N'total', CONVERT(nvarchar(40), (SELECT CONVERT(decimal(18, 2), SUM(iDAmount) - SUM(iCAmount)) FROM #CancelHdsy))),
  (N'skipped', CONVERT(nvarchar(20), @skipped)),
  (N'com_ar', CONVERT(nvarchar(20), (SELECT COUNT(*) FROM #ap_SaleBillVouchHXdata))),
  (N'com_ap', CONVERT(nvarchar(20), (SELECT COUNT(*) FROM #ap_purbillHXdata)))) v (k, n);
