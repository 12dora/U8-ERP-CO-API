-- U8 应收 坏账发生（9G）：取号、写处理行、扣余额、减坏账准备余额，填好销售发票回写临时表。
-- 写入与 U8「坏账发生」界面执行的 SQL 一致（测试账套实测核对），由桥在请求连接、请求事务里整批执行（不分 GO）。
-- 参数取自调用方先建好的临时表（ArapBadOccurSql.Prepare）：
--   #bad_args (y 会计年度, m 会计期间, reg_date 登记日期 yyyy-MM-dd, accounter 操作员姓名, digest 摘要, dw 客户,
--              dept / person 部门、业务员（空 = 取审核行的）, para_id 桥查过的 Ar_BadPara 行（max(autoid)，登记年度的那一行）)
--   #bad_pick (vtype, vid, line 发票行 iBVid（应收单 0）, whole 1=应收单整单, amt_f 原币, amt_n 本币)：桥按行分摊好的金额，
--   每行写一条 9G 处理行（贷方，iFlag=0），从该单据（行）的审核行（cProcStyle = cVouchType）复制部门、业务员、存货、科目等。
--   #ap_SaleBillVouchHXdata (autoid, iexchsum, imoneysum)：本段填好销售发票行的本次金额（正数），桥随后调 U8 的
--   clsWrite2Bill.UpdateBillForAR 加到累计核销（同核销、转账一族）。
-- 应收单（R0–R9）扣 Ap_Vouch.iRAmount*（iRAmount_s 用 U8 的比例公式）。坏账准备余额：U8 是
-- update Ar_BadPara set iRemainAmount=iRemainAmount-(本币) where autoid=(select max(autoid) from Ar_BadPara)（不按年度），
-- 桥另要求那一行仍是闸门查过的登记年度的那一行。编号 Ap_CancelNo（cType HZ、cFlag AR）→ HZAR + 13 位补零序号（同 U8）。
-- 关联合同的单据桥在闸门里拒绝，这里不写合同。拒绝时 THROW 50101–50139（中文由桥按编号给出）；最后一个结果集是 k、n 计数。
SET NOCOUNT ON;

DECLARE @y smallint, @m tinyint, @reg nvarchar(10), @accounter nvarchar(20), @digest nvarchar(255);
DECLARE @dw nvarchar(40), @dept nvarchar(40), @person nvarchar(40), @para_id int;
DECLARE @dReg datetime, @pid int, @pyear int, @before money, @after money, @n int, @no nvarchar(30);
DECLARE @rows int, @want int, @got int, @sum_f money, @sum_n money, @bad nvarchar(130), @msg nvarchar(200);
SELECT @y = y, @m = m, @reg = reg_date, @accounter = accounter, @digest = digest, @dw = dw,
  @dept = NULLIF(LTRIM(ISNULL(dept, N'')), N''), @person = NULLIF(LTRIM(ISNULL(person, N'')), N''), @para_id = para_id
FROM #bad_args;

IF @y IS NULL OR @m IS NULL OR NULLIF(LTRIM(ISNULL(@reg, N'')), N'') IS NULL OR NULLIF(LTRIM(ISNULL(@dw, N'')), N'') IS NULL
  THROW 50101, N'9G args missing', 1;
IF NOT EXISTS (SELECT 1 FROM #bad_pick) THROW 50111, N'9G #bad_pick empty', 1;
SET @dReg = CONVERT(datetime, CONVERT(date, @reg, 23));
IF NULLIF(LTRIM(ISNULL(@digest, N'')), N'') IS NULL SET @digest = N'坏账发生';
SET @accounter = ISNULL(@accounter, N'');

IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear = @y AND iperiod = @m AND ISNULL(bflag_AR, 0) = 1)
  THROW 50102, N'AR period already closed (GL_mend)', 1;

-- 坏账准备参数：U8 改 max(autoid) 那一行；必须仍是桥查过的那一行、且是登记年度的。
SELECT TOP 1 @pid = autoid, @pyear = iYear, @before = ISNULL(iRemainAmount, 0)
FROM Ar_BadPara WITH (UPDLOCK, HOLDLOCK) ORDER BY autoid DESC;
IF @pid IS NULL OR @pid <> ISNULL(@para_id, 0) OR ISNULL(@pyear, 0) <> @y
  THROW 50103, N'Ar_BadPara max(autoid) row changed', 1;

-- 各分摊行的余额（借 − 贷，iFlag<3，口径同核销）再查一遍：本次原币不能超过、余额必须为正。
IF OBJECT_ID(N'tempdb..#bad_rem') IS NOT NULL DROP TABLE #bad_rem;
SELECT p.rid,
  CONVERT(money, ISNULL(SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)), 0)) AS rf,
  CONVERT(money, ISNULL(SUM(ISNULL(d.iDAmount, 0) - ISNULL(d.iCAmount, 0)), 0)) AS rn
INTO #bad_rem
FROM #bad_pick p
LEFT JOIN Ar_Detail d WITH (UPDLOCK, HOLDLOCK) ON d.cFlag = N'AR' AND d.cCoVouchType = p.vtype AND d.cCoVouchID = p.vid
  AND d.cDwCode = @dw AND d.iFlag < 3 AND (p.whole = 1 OR ISNULL(d.iBVid, 0) = p.line)
GROUP BY p.rid;

SELECT TOP 1 @bad = p.vtype + N' ' + p.vid FROM #bad_pick p
INNER JOIN #bad_rem r ON r.rid = p.rid
WHERE r.rf <= 0 OR CONVERT(money, p.amt_f) > r.rf
ORDER BY p.rid;
IF @bad IS NOT NULL
BEGIN
  SET @msg = N'9G remainder changed: ' + @bad;
  THROW 50113, @msg, 1;
END

-- 编号：Ap_CancelNo（HZ / AR）带锁取当前号 + 1；账套里还没有这一行时补一行（同汇兑损益）。
IF NOT EXISTS (SELECT 1 FROM Ap_CancelNo WITH (UPDLOCK, HOLDLOCK) WHERE cType = N'HZ' AND cFlag = N'AR')
  INSERT INTO Ap_CancelNo (cType, cFlag, iCancelNo) VALUES (N'HZ', N'AR', 0);
SELECT @n = ISNULL(iCancelNo, 0) + 1 FROM Ap_CancelNo WITH (UPDLOCK, HOLDLOCK) WHERE cType = N'HZ' AND cFlag = N'AR';
SET @no = N'HZAR' + RIGHT(REPLICATE(N'0', 13) + CONVERT(nvarchar(13), @n), 13);
IF EXISTS (SELECT 1 FROM Ar_Detail WHERE cCancelNo = @no) OR EXISTS (SELECT 1 FROM Ar_BadPara WHERE cCancelNo = @no)
BEGIN
  SET @msg = N'cancel no already used: ' + @no;
  THROW 50104, @msg, 1;
END
UPDATE Ap_CancelNo SET iCancelNo = @n WHERE cType = N'HZ' AND cFlag = N'AR';

-- 处理行：每个分摊行一条，从审核行复制（列同转账 9I 的模板，已在测试账套核对）；贷方本次金额，未制单。
INSERT INTO Ar_Detail (
  iPeriod, cVouchType, cVouchSType, cVouchID, dVouchDate, dRegDate, cDwCode, cDeptCode, cPerson,
  cInvCode, iBVid, cCode, cItem_Class, cItemCode, csign, isignseq, ino_id, cDigest, iPrice,
  cexch_name, iExchRate, iDAmount, iCAmount, iDAmount_f, iCAmount_f, iDAmount_s, iCAmount_s,
  cOrderNo, cSSCode, cPayCode, cProcStyle, cCancelNo, cPZid, bPrePay, iFlag, cCoVouchType,
  cCoVouchID, cFlag, cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7,
  cDefine8, cDefine9, cDefine10, iClosesID, iCoClosesID, cDefine11, cDefine12, cDefine13,
  cDefine14, cDefine15, cDefine16, cGLSign, iGLno_id, dPZDate, cItemName, cContractType,
  cContractID, BalancesGuid, dHideDate, cGatheringPlan, dCreditStart, iCreditPeriod, dGatheringDate,
  bCredit, cOperator, cCheckMan, iOrderType, cDLCode)
SELECT
  @m, s.cVouchType, s.cVouchSType, s.cVouchID, s.dVouchDate, @dReg, s.cDwCode,
  COALESCE(@dept, s.cDeptCode), COALESCE(@person, s.cPerson),
  s.cInvCode, s.iBVid, s.cCode, s.cItem_Class, s.cItemCode, s.csign, NULL, NULL, @digest, s.iPrice,
  s.cexch_name, s.iExchRate, 0, CONVERT(money, p.amt_n), 0, CONVERT(money, p.amt_f), 0, 0,
  s.cOrderNo, s.cSSCode, s.cPayCode, N'9G', @no, NULL, 0, 0, s.cVouchType,
  s.cVouchID, N'AR', s.cDefine1, s.cDefine2, s.cDefine3, s.cDefine4, s.cDefine5, s.cDefine6, s.cDefine7,
  s.cDefine8, s.cDefine9, s.cDefine10, s.iClosesID, s.iClosesID, s.cDefine11, s.cDefine12, s.cDefine13,
  s.cDefine14, s.cDefine15, s.cDefine16, NULL, NULL, NULL, s.cItemName, s.cContractType,
  s.cContractID, s.BalancesGuid, NULL, s.cGatheringPlan, s.dCreditStart, s.iCreditPeriod, s.dGatheringDate,
  s.bCredit, @accounter, @accounter, s.iOrderType, s.cDLCode
FROM #bad_pick p
CROSS APPLY (
  SELECT TOP 1 x.* FROM Ar_Detail x
  WHERE x.cProcStyle = x.cVouchType AND x.cVouchType = p.vtype AND x.cVouchID = p.vid AND x.cFlag = N'AR'
    AND x.cDwCode = @dw AND x.iFlag < 3 AND ISNULL(x.iBVid, 0) = p.line
  ORDER BY x.Auto_ID
) s;
SET @rows = @@ROWCOUNT;
IF @rows <> (SELECT COUNT(*) FROM #bad_pick)
BEGIN
  SELECT TOP 1 @bad = p.vtype + N' ' + p.vid FROM #bad_pick p
  WHERE NOT EXISTS (SELECT 1 FROM Ar_Detail x WHERE x.cProcStyle = N'9G' AND x.cCancelNo = @no
    AND x.cVouchType = p.vtype AND x.cVouchID = p.vid AND ISNULL(x.iBVid, 0) = p.line)
  ORDER BY p.rid;
  SET @msg = N'9G sign row missing: ' + ISNULL(@bad, N'');
  THROW 50112, @msg, 1;
END

-- 应收单表头余额（同转账）：每张都要改到一行，否则回滚。
SELECT @want = COUNT(*) FROM (SELECT DISTINCT vtype, vid FROM #bad_pick WHERE whole = 1) t;
UPDATE v SET
  iRAmount_f = v.iRAmount_f - t.f,
  iRAmount = v.iRAmount - t.n,
  iRAmount_s = CASE WHEN v.iAmount_f <> 0 THEN v.iAmount_s * (v.iRAmount_f - t.f) / v.iAmount_f ELSE v.iRAmount_f - t.f END
FROM Ap_Vouch v
INNER JOIN (SELECT vtype, vid, SUM(CONVERT(money, amt_f)) AS f, SUM(CONVERT(money, amt_n)) AS n
            FROM #bad_pick WHERE whole = 1 GROUP BY vtype, vid) t
  ON v.cVouchType = t.vtype AND v.cVouchID = t.vid AND v.cFlag = N'AR';
SET @got = @@ROWCOUNT;
IF @got <> @want
BEGIN
  SET @msg = N'Ap_Vouch remainder rows ' + CONVERT(nvarchar(12), @got) + N'/' + CONVERT(nvarchar(12), @want);
  THROW 50114, @msg, 1;
END

-- 销售发票行的本次金额（正数，加到累计核销）；桥在本段之后调 UpdateBillForAR。
TRUNCATE TABLE #ap_SaleBillVouchHXdata;
INSERT INTO #ap_SaleBillVouchHXdata (autoid, iexchsum, imoneysum)
SELECT line, CONVERT(decimal(29, 6), SUM(CONVERT(money, amt_f))), CONVERT(decimal(29, 6), SUM(CONVERT(money, amt_n)))
FROM #bad_pick WHERE whole = 0 GROUP BY line;

-- 坏账准备余额减本币合计（同 U8 的 max(autoid) 行，上面已核对就是 @pid）。
SELECT @sum_f = SUM(CONVERT(money, amt_f)), @sum_n = SUM(CONVERT(money, amt_n)) FROM #bad_pick;
UPDATE Ar_BadPara SET iRemainAmount = ISNULL(iRemainAmount, 0) - @sum_n WHERE autoid = @pid;
SELECT @after = ISNULL(iRemainAmount, 0) FROM Ar_BadPara WHERE autoid = @pid;

SELECT k, n FROM (VALUES
  (N'cancel_no', @no),
  (N'rows', CONVERT(nvarchar(20), @rows)),
  (N'com_ar', CONVERT(nvarchar(20), (SELECT COUNT(*) FROM #ap_SaleBillVouchHXdata))),
  (N'total_f', CONVERT(nvarchar(40), CONVERT(decimal(28, 2), @sum_f))),
  (N'total_n', CONVERT(nvarchar(40), CONVERT(decimal(28, 2), @sum_n))),
  (N'remain_before', CONVERT(nvarchar(40), CONVERT(decimal(28, 2), @before))),
  (N'remain_after', CONVERT(nvarchar(40), CONVERT(decimal(28, 2), @after)))) v (k, n);
