-- U8 取消坏账处理：计提 9F、发生 9G、收回 9H（处理号 HZAR…）。写入与 U8「取消操作」界面执行的 SQL 一致（测试账套实测核对）：
--   9F：iRemainAmount -= iJtAmount、清 iJtAmount / dJtDate（U8 在该年度参数行多于一行时改为删行，桥拒绝）；
--   9H：坏账准备余额减回收回金额（最新一行，同 U8 不带年度）、收款单行余额还原为金额、删 9H 行；
--   收款单的弃审由桥在本段之后用 U8 的审核组件（UFAPBO clsCloseBill.CancelSign）做，删掉组件自己写的审核行（坏账收回时
--   桥用 Sign 审核，见 bad_recover.sql）。收款单没有审核行的 9H（U8 客户端或早期版本照 U8 只填审核人）拒绝（50155）：
--   删掉 9H 借方行后客户应收余额会少掉收回金额，桥不照做；计数 signed 是收款单审核行的条数。
--   9G 余额部分：坏账准备余额加回发生金额、应收单 iRAmount* 加回、销售发票累计经 #ap_SaleBillVouchHXdata 交给
--   U8 的 clsWrite2Bill.UpdateBillForAR（桥在本段之后调用，同一连接），然后桥删 9G 行（DELETE 在 COM 之后，同 U8 的顺序）。
-- 由桥在请求连接、请求事务里整批执行（不分 GO）。参数取自调用方先建好的临时表（ArapProcCancelBadSql.Prepare）：
--   #badc_args (flag AR, style 9F|9G|9H, cancel_no, acc 登录账套号)；#ap_SaleBillVouchHXdata、#badc_bill（发票累计快照）由调用方建好，本段填。
-- 闸门（任一不过整批拒绝，THROW 50140–50159（坏账取消的号段），桥按编号转中文 409；消息最后一个冒号后是补充说明）：
--   50140 参数无效（防御性，桥按内部错误）、50141 期间已结账、50142 没有坏账准备参数、50143 收款单不存在、50144 已制单、
--   50145 之后还有其他处理、50146 处理号不存在、50147 处理号对应多行参数、50148 处理行日期不一致、50149 最新参数行不是本年度、
--   50150 处理行形状不符、50151 收款单已制单、50152 发票行缺 iBVid、50153 涉及合同、50154 处理日期不在会计期间里、
--   50155 收款单没有审核行、50156 该年度有多行参数、50157 收款单号不唯一、50159 写后核对不符。
-- 会计年度、期间按处理日期（9F 的 dJtDate、9G / 9H 的 dRegDate）在 UFSYSTEM..UA_Period 里查（同新增，WriteoffSql.PeriodOf），
-- 不用日历年月。
-- 与 U8 的出入：9F 要求 dJtDate 非空（U8 取消后保留 cCancelNo，重复取消会把余额减成 NULL）；坏账准备最新一行的年度必须是处理年度
-- （U8 不带年度直接改 max(autoid) 那行）；涉及合同的处理拒绝（新增也不做合同单据）。
-- 第一个结果集是涉及的单据（vtype、vid、line_id、doc_id、partner、amount_f、amount），最后是 k、n 计数
-- （rows、com、year、amount、remain_before、remain_after、dropped，9H 另有 signed）。
SET NOCOUNT ON;

DECLARE @flag nvarchar(4), @style nvarchar(4), @no nvarchar(30), @msg nvarchar(200);
DECLARE @y int, @m int, @n int, @d datetime, @pz nvarchar(60), @drop int, @com int;
DECLARE @jt money, @rb money, @ra money, @amt money, @amt_f money, @maxid int, @maxy int;
DECLARE @iid int, @vid nvarchar(60), @own nvarchar(80), @signed int, @acc nvarchar(10), @py int, @cnt int;
SELECT @flag = flag, @style = style, @no = cancel_no, @acc = acc FROM #badc_args;
IF ISNULL(@flag, N'') <> N'AR' THROW 50140, N'bad-cancel flag must be AR', 1;
IF ISNULL(@style, N'') NOT IN (N'9F', N'9G', N'9H') THROW 50140, N'bad-cancel style must be 9F/9G/9H', 1;
IF NULLIF(LTRIM(ISNULL(@acc, N'')), N'') IS NULL THROW 50140, N'bad-cancel acc missing', 1;
SET @drop = 0;
SET @com = 0;

---------------------------------------------------------------------------
-- 9F：坏账准备参数行（没有往来明细行）。
---------------------------------------------------------------------------
IF @style = N'9F'
BEGIN
  SELECT @n = COUNT(*) FROM Ar_BadPara WITH (UPDLOCK, HOLDLOCK)
  WHERE cCancelNo = @no AND cProcStyle = N'9F' AND dJtDate IS NOT NULL;
  IF @n = 0 THROW 50146, N'9F batch not found on Ar_BadPara (or already cancelled)', 1;
  IF @n > 1 THROW 50147, N'9F cancel_no on more than one Ar_BadPara row', 1;
  SELECT @y = iYear, @d = dJtDate, @jt = ISNULL(iJtAmount, 0), @pz = ISNULL(cPZid, N''), @rb = ISNULL(iRemainAmount, 0)
  FROM Ar_BadPara WHERE cCancelNo = @no AND cProcStyle = N'9F' AND dJtDate IS NOT NULL;
  IF @pz <> N'' THROW 50144, N'9F cPZid set', 1;
  IF EXISTS (SELECT 1 FROM Ar_Detail WHERE cProcStyle = N'9F' AND cCancelNo = @no AND ISNULL(cPZid, N'') <> N'')
    THROW 50144, N'9F detail cPZid set', 1;
  SET @d = CONVERT(datetime, CONVERT(date, @d));
  SELECT TOP 1 @py = p.iYear, @m = p.iId FROM UFSYSTEM..UA_Period p
  WHERE p.cAcc_Id = @acc AND p.dBegin <= @d AND p.dEnd >= @d AND (p.bIsDelete = 0 OR p.bIsDelete IS NULL)
  ORDER BY p.iYear, p.iId;
  IF @py IS NULL OR @py <> @y
  BEGIN
    SET @msg = N'9F date not in UA_Period of year: ' + CONVERT(nvarchar(10), @d, 23);
    THROW 50154, @msg, 1;
  END
  IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear = @y AND iperiod = @m AND ISNULL(bflag_AR, 0) = 1)
  BEGIN
    SET @msg = N'AR period closed: ' + CONVERT(nvarchar(4), @y) + N'-' + CONVERT(nvarchar(2), @m);
    THROW 50141, @msg, 1;
  END

  -- U8 在该年度有多行参数时删掉本处理号那行；桥不删参数行（计提新增也拒绝多行年度），拒绝。
  IF (SELECT COUNT(autoid) FROM Ar_BadPara WITH (UPDLOCK, HOLDLOCK) WHERE iYear = @y) > 1
  BEGIN
    SET @msg = N'Ar_BadPara has more than one row for year: ' + CONVERT(nvarchar(4), @y);
    THROW 50156, @msg, 1;
  END
  UPDATE Ar_BadPara SET
    iRemainAmount = ISNULL(iRemainAmount, 0) - ISNULL(iJtAmount, 0),
    iJtAmount = NULL,
    dJtDate = NULL
  WHERE iYear = @y AND cCancelNo = @no AND cProcStyle = N'9F' AND dJtDate IS NOT NULL;
  IF @@ROWCOUNT <> 1 THROW 50159, N'9F update count mismatch', 1;
  SELECT @ra = iRemainAmount FROM Ar_BadPara WHERE iYear = @y AND cCancelNo = @no;
  IF @ra IS NULL OR ABS(@ra - (@rb - @jt)) > 0.005 THROW 50159, N'9F remain mismatch after cancel', 1;
  DELETE Ar_Detail WHERE cProcStyle = N'9F' AND cCancelNo = @no;

  SELECT N'9F' AS vtype, @no AS vid, 0 AS line_id, 0 AS doc_id, N'' AS partner,
    CONVERT(nvarchar(40), @jt) AS amount_f, CONVERT(nvarchar(40), @jt) AS amount;
  SELECT k, n FROM (VALUES
    (N'rows', N'0'), (N'com', N'0'), (N'year', CONVERT(nvarchar(40), @y)), (N'amount', CONVERT(nvarchar(40), @jt)),
    (N'remain_before', CONVERT(nvarchar(40), @rb)), (N'remain_after', ISNULL(CONVERT(nvarchar(40), @ra), N'')),
    (N'dropped', CONVERT(nvarchar(40), @drop))) AS t (k, n);
  RETURN;
END

---------------------------------------------------------------------------
-- 9G / 9H：带锁快照整批处理行。
---------------------------------------------------------------------------
IF OBJECT_ID(N'tempdb..#badc_snap') IS NOT NULL DROP TABLE #badc_snap;
SELECT Auto_ID, cFlag, cVouchType, cVouchID, cCoVouchType, cCoVouchID, cDwCode, iBVid, iPeriod, dRegDate,
  ISNULL(cPZid, N'') AS cPZid, ISNULL(cContractID, N'') AS cContractID,
  ISNULL(iDAmount, 0) AS iDAmount, ISNULL(iCAmount, 0) AS iCAmount, ISNULL(iDAmount_f, 0) AS iDAmount_f,
  ISNULL(iCAmount_f, 0) AS iCAmount_f, ISNULL(iDAmount_s, 0) AS iDAmount_s, ISNULL(iCAmount_s, 0) AS iCAmount_s
INTO #badc_snap
FROM Ar_Detail WITH (UPDLOCK, HOLDLOCK)
WHERE cProcStyle = @style AND cCancelNo = @no;

SELECT @n = COUNT(*) FROM #badc_snap;
IF @n = 0 THROW 50146, N'9G/9H batch not found', 1;
IF EXISTS (SELECT 1 FROM #badc_snap WHERE ISNULL(cFlag, N'') <> N'AR') THROW 50150, N'batch row not on AR', 1;
IF EXISTS (SELECT 1 FROM #badc_snap WHERE cPZid <> N'') THROW 50144, N'cPZid set', 1;
IF (SELECT COUNT(*) FROM (SELECT DISTINCT iPeriod, CONVERT(date, dRegDate) AS d FROM #badc_snap) t) > 1
  THROW 50148, N'batch rows differ in period / reg date', 1;
IF EXISTS (SELECT 1 FROM #badc_snap WHERE cContractID <> N'') THROW 50153, N'contract rows', 1;
IF EXISTS (SELECT 1 FROM #badc_snap WHERE ISNULL(cVouchType, N'') <> ISNULL(cCoVouchType, N'')
    OR ISNULL(cVouchID, N'') <> ISNULL(cCoVouchID, N''))
  THROW 50150, N'batch row vouch <> co-vouch', 1;
IF @style = N'9G' AND EXISTS (SELECT 1 FROM #badc_snap
    WHERE cVouchType NOT IN (N'26', N'27', N'28', N'29', N'R0', N'R1', N'R2', N'R3', N'R4', N'R5', N'R6', N'R7', N'R8', N'R9'))
  THROW 50150, N'9G row on unsupported type', 1;
IF @style = N'9G' AND EXISTS (SELECT 1 FROM #badc_snap
    WHERE cVouchType IN (N'26', N'27', N'28', N'29') AND ISNULL(iBVid, 0) <= 0)
  THROW 50152, N'9G invoice row without iBVid', 1;
IF @style = N'9H' AND (EXISTS (SELECT 1 FROM #badc_snap WHERE cVouchType <> N'48')
    OR (SELECT COUNT(DISTINCT cVouchID) FROM #badc_snap) <> 1)
  THROW 50150, N'9H rows not on one receipt', 1;

SELECT TOP 1 @d = CONVERT(datetime, CONVERT(date, dRegDate)) FROM #badc_snap;
SELECT TOP 1 @y = p.iYear, @m = p.iId FROM UFSYSTEM..UA_Period p
WHERE p.cAcc_Id = @acc AND p.dBegin <= @d AND p.dEnd >= @d AND (p.bIsDelete = 0 OR p.bIsDelete IS NULL)
ORDER BY p.iYear, p.iId;
IF @y IS NULL
BEGIN
  SET @msg = N'reg date not in UA_Period: ' + ISNULL(CONVERT(nvarchar(10), @d, 23), N'?');
  THROW 50154, @msg, 1;
END
IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear = @y AND iperiod = @m AND ISNULL(bflag_AR, 0) = 1)
BEGIN
  SET @msg = N'AR period closed: ' + CONVERT(nvarchar(4), @y) + N'-' + CONVERT(nvarchar(2), @m);
  THROW 50141, @msg, 1;
END

-- 坏账准备参数：U8 改 max(autoid) 那行（不带年度）；那行不是处理年度的就拒绝，免得改到别的年度。
SELECT @maxid = MAX(autoid) FROM Ar_BadPara WITH (UPDLOCK, HOLDLOCK);
IF @maxid IS NULL THROW 50142, N'Ar_BadPara empty', 1;
SELECT @maxy = iYear, @rb = ISNULL(iRemainAmount, 0) FROM Ar_BadPara WHERE autoid = @maxid;
IF ISNULL(@maxy, 0) <> @y
BEGIN
  SET @msg = N'latest Ar_BadPara row year differs: ' + ISNULL(CONVERT(nvarchar(4), @maxy), N'?');
  THROW 50149, @msg, 1;
END

-- 同一单据之后（Auto_ID 更大）还有别的非审核处理（9G 报 01644，同 U8）。
IF EXISTS (
  SELECT TOP 1 1
  FROM Ar_Detail AS t1
  INNER JOIN #badc_snap AS t2 ON t1.cCoVouchType = t2.cCoVouchType AND t1.cCoVouchID = t2.cCoVouchID
    AND t1.cDwCode = t2.cDwCode AND t1.cFlag = t2.cFlag
  WHERE t1.Auto_ID > t2.Auto_ID
    AND ISNULL(t1.cProcStyle, N'') <> ISNULL(t1.cVouchType, N'')
    AND t1.cProcStyle <> @style
    AND ISNULL(t1.cCancelNo, N'') <> @no)
  THROW 50145, N'later processing on same co-doc (01644)', 1;

---------------------------------------------------------------------------
-- 9H：用 iDAmount（不走 9G 的余额部分）。
---------------------------------------------------------------------------
IF @style = N'9H'
BEGIN
  SELECT TOP 1 @vid = cVouchID FROM #badc_snap;
  SELECT @cnt = COUNT(*) FROM Ap_CloseBill WITH (UPDLOCK, HOLDLOCK)
  WHERE cVouchType = N'48' AND cVouchID = @vid AND cFlag = N'AR';
  IF @cnt = 0
  BEGIN
    SET @msg = N'9H receipt not found: ' + @vid;
    THROW 50143, @msg, 1;
  END
  IF @cnt > 1
  BEGIN
    SET @msg = N'9H receipt number not unique: ' + @vid;
    THROW 50157, @msg, 1;
  END
  SELECT @iid = iID, @pz = ISNULL(cPzID, N'') FROM Ap_CloseBill
  WHERE cVouchType = N'48' AND cVouchID = @vid AND cFlag = N'AR';
  IF @pz <> N'' THROW 50151, N'9H receipt has cPzID', 1;
  -- 收款单上除本批 9H 和本单未制单的审核行（cProcStyle = 48、cCancelNo = AR48 + 单号）以外不能还有往来明细。
  SET @own = N'AR48' + @vid;
  IF EXISTS (SELECT 1 FROM Ar_Detail WHERE cFlag = N'AR'
      AND ((cVouchType = N'48' AND cVouchID = @vid) OR (cCoVouchType = N'48' AND cCoVouchID = @vid))
      AND NOT (cProcStyle = N'9H' AND cCancelNo = @no)
      AND NOT (ISNULL(cProcStyle, N'') = N'48' AND ISNULL(cCancelNo, N'') = @own AND ISNULL(cPZid, N'') = N''))
    THROW 50145, N'receipt has other detail rows', 1;
  SELECT @signed = COUNT(*) FROM Ar_Detail WHERE cFlag = N'AR' AND cVouchType = N'48' AND cVouchID = @vid
    AND cProcStyle = N'48' AND cCancelNo = @own;
  IF @signed = 0
  BEGIN
    SET @msg = N'9H receipt has no audit row (not made by the bridge): ' + @vid;
    THROW 50155, @msg, 1;
  END

  SELECT @amt = SUM(iDAmount), @amt_f = SUM(iDAmount_f) FROM #badc_snap;
  UPDATE Ar_BadPara SET iRemainAmount = ISNULL(iRemainAmount, 0) - @amt WHERE autoid = @maxid;
  UPDATE Ap_CloseBills SET iRAmt = iAmt, iRAmt_f = iAmt_f, iRAmt_s = iAmt_s WHERE iID = @iid;
  DELETE Ar_Detail WHERE cProcStyle = N'9H' AND cCancelNo = @no;
  SET @drop = @@ROWCOUNT;
  IF @drop <> @n THROW 50159, N'9H delete count mismatch', 1;
  SELECT @ra = iRemainAmount FROM Ar_BadPara WHERE autoid = @maxid;
  IF ABS(@ra - (@rb - @amt)) > 0.005 THROW 50159, N'9H remain mismatch', 1;
  IF EXISTS (SELECT 1 FROM Ap_CloseBills WHERE iID = @iid AND ISNULL(iRAmt_f, 0) <> ISNULL(iAmt_f, 0))
    THROW 50159, N'9H receipt lines not restored', 1;

  SELECT N'48' AS vtype, @vid AS vid, 0 AS line_id, @iid AS doc_id, MAX(cDwCode) AS partner,
    CONVERT(nvarchar(40), @amt_f) AS amount_f, CONVERT(nvarchar(40), @amt) AS amount
  FROM #badc_snap;
  SELECT k, n FROM (VALUES
    (N'rows', CONVERT(nvarchar(40), @n)), (N'com', N'0'), (N'year', CONVERT(nvarchar(40), @y)),
    (N'amount', CONVERT(nvarchar(40), @amt)), (N'remain_before', CONVERT(nvarchar(40), @rb)),
    (N'remain_after', CONVERT(nvarchar(40), @ra)), (N'dropped', CONVERT(nvarchar(40), @drop)),
    (N'signed', CONVERT(nvarchar(40), @signed))) AS t (k, n);
  RETURN;
END

---------------------------------------------------------------------------
-- 9G 余额部分：IJE = -(借 + 贷) = -贷；坏账准备余额 -= IJE（加回），应收单 iRAmount -= IJE（加回），
-- 销售发票 imoneysum = IJE（UpdateBillForAR 减回累计）。删行由桥在 COM 之后做。
---------------------------------------------------------------------------
SELECT @amt = SUM(iCAmount + iDAmount), @amt_f = SUM(iCAmount_f + iDAmount_f) FROM #badc_snap;
UPDATE Ar_BadPara SET iRemainAmount = ISNULL(iRemainAmount, 0) + @amt WHERE autoid = @maxid;
SELECT @ra = iRemainAmount FROM Ar_BadPara WHERE autoid = @maxid;
IF ABS(@ra - (@rb + @amt)) > 0.005 THROW 50159, N'9G remain mismatch', 1;

IF OBJECT_ID(N'tempdb..#badc_vouch') IS NOT NULL DROP TABLE #badc_vouch;
SELECT v.cVouchType, v.cVouchID, ISNULL(v.iRAmount_f, 0) AS before_f, t.back_f
INTO #badc_vouch
FROM Ap_Vouch v WITH (UPDLOCK, HOLDLOCK)
INNER JOIN (
  SELECT cVouchType, cVouchID, SUM(iCAmount_f + iDAmount_f) AS back_f
  FROM #badc_snap WHERE cVouchType LIKE N'R[0-9]'
  GROUP BY cVouchType, cVouchID) t
ON v.cVouchType = t.cVouchType AND v.cVouchID = t.cVouchID AND v.cFlag = N'AR';
IF (SELECT COUNT(*) FROM #badc_vouch)
   <> (SELECT COUNT(*) FROM (SELECT DISTINCT cVouchType, cVouchID FROM #badc_snap WHERE cVouchType LIKE N'R[0-9]') t)
  THROW 50159, N'9G Ap_Vouch missing or not unique', 1;

UPDATE v SET
  iRAmount   = iRAmount   + t.back,
  iRAmount_f = iRAmount_f + t.back_f,
  iRAmount_s = CASE WHEN v.iAmount_f <> 0
    THEN v.iAmount_s * (v.iRAmount_f + t.back_f) / v.iAmount_f
    ELSE v.iRAmount_f + t.back_f END
FROM Ap_Vouch v
INNER JOIN (
  SELECT cVouchID, cVouchType, SUM(iCAmount + iDAmount) AS back, SUM(iCAmount_f + iDAmount_f) AS back_f
  FROM #badc_snap
  WHERE cVouchType LIKE N'R[0-9]'
  GROUP BY cVouchID, cVouchType) t
ON v.cVouchType = t.cVouchType AND v.cVouchID = t.cVouchID AND v.cFlag = N'AR';

IF EXISTS (SELECT 1 FROM #badc_vouch b INNER JOIN Ap_Vouch v
    ON v.cVouchType = b.cVouchType AND v.cVouchID = b.cVouchID AND v.cFlag = N'AR'
    WHERE ABS(ISNULL(v.iRAmount_f, 0) - (b.before_f + b.back_f)) > 0.005)
  THROW 50159, N'9G Ap_Vouch remain mismatch', 1;

-- 销售发票：#ap_SaleBillVouchHXdata（autoid, iexchsum = IWB, imoneysum = IJE），#badc_bill 是调用前的累计快照（桥核对用）。
DELETE FROM #ap_SaleBillVouchHXdata;
DELETE FROM #badc_bill;
INSERT INTO #ap_SaleBillVouchHXdata (autoid, iexchsum, imoneysum)
SELECT iBVid, -(iDAmount_f + iCAmount_f), -(iDAmount + iCAmount)
FROM #badc_snap
WHERE cVouchType IN (N'26', N'27', N'28', N'29');
SET @com = @@ROWCOUNT;
INSERT INTO #badc_bill (id, a, b)
SELECT x.AutoID, ISNULL(x.iExchSum, 0), ISNULL(x.iMoneySum, 0)
FROM SaleBillVouchs x WITH (UPDLOCK, HOLDLOCK)
INNER JOIN (SELECT DISTINCT autoid FROM #ap_SaleBillVouchHXdata) t ON x.AutoID = t.autoid;
IF (SELECT COUNT(*) FROM #badc_bill) <> (SELECT COUNT(DISTINCT autoid) FROM #ap_SaleBillVouchHXdata)
  THROW 50152, N'9G invoice line missing', 1;

SELECT s.cVouchType AS vtype, s.cVouchID AS vid, ISNULL(s.iBVid, 0) AS line_id,
  ISNULL(MAX(CASE WHEN s.cVouchType IN (N'26', N'27', N'28', N'29') THEN b.SBVID END), 0) AS doc_id,
  MAX(s.cDwCode) AS partner, CONVERT(nvarchar(40), SUM(s.iCAmount_f + s.iDAmount_f)) AS amount_f,
  CONVERT(nvarchar(40), SUM(s.iCAmount + s.iDAmount)) AS amount
FROM #badc_snap s
LEFT JOIN SaleBillVouchs b ON b.AutoID = s.iBVid AND s.cVouchType IN (N'26', N'27', N'28', N'29')
GROUP BY s.cVouchType, s.cVouchID, ISNULL(s.iBVid, 0)
ORDER BY MIN(s.Auto_ID);
SELECT k, n FROM (VALUES
  (N'rows', CONVERT(nvarchar(40), @n)), (N'com', CONVERT(nvarchar(40), @com)), (N'year', CONVERT(nvarchar(40), @y)),
  (N'amount', CONVERT(nvarchar(40), @amt)), (N'remain_before', CONVERT(nvarchar(40), @rb)),
  (N'remain_after', CONVERT(nvarchar(40), @ra)), (N'dropped', N'0')) AS t (k, n);
