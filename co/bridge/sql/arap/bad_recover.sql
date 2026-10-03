-- U8 应收 坏账收回（9H）：取号、在收款单上写处理行、清零收款单行余额、加坏账准备余额。
-- 写入与 U8「坏账收回」界面执行的 SQL 一致（测试账套实测核对），由桥在请求连接、请求事务里整批执行（不分 GO）。
-- 参数取自调用方先建好的 #bad_args（ArapBadRecover.Prepare）：y 会计年度, m 会计期间, reg_date 登记日期 yyyy-MM-dd,
--   accounter 操作员姓名（处理人）, digest 摘要, dw 客户, para_id 桥查过的 Ar_BadPara 行（max(autoid)、登记年度）,
--   receipt_id 收款单 Ap_CloseBill.iID, receipt 收款单号, amt_f 原币（= 收款单金额）, amt_n 本币（= 收款单行本币余额）。
-- 与 U8 的出入：U8 的 9H 只写处理行、用 SQL 填审核人，收款单自己的审核行（贷方）没有，客户应收余额因此多出收回金额。
-- 桥在本段之前已在同一事务里用 U8 的收款单审核组件（UFAPBO clsCloseBill.Sign，同 vouchers/verify）审核了收款单，
-- 审核行（cProcStyle = 48、cCancelNo = AR48 + 单号）、cCheckMan、dverifydate 都由组件写；本段不再改审核人。
-- 9H 借方处理行（再确认这笔应收）与审核行（贷方，收款冲应收）相抵，客户应收余额不变，坏账准备余额加回。
-- 桥的闸门已查：审核前未审核、未核销、款项类型应收款（iType=0）、只有一行、手工录入、没有往来明细；这里带锁再查一遍
-- （已审核、除本单审核行外没有往来明细）。
-- 处理行挂在收款单上（cVouchType = cCoVouchType = 48，借方，iFlag=0，iBVid / iClosesID / iCoClosesID = 收款单行 ID）；
-- 科目 cCode 取收款单行的应收科目 Ap_CloseBills.cKm（与审核行贷方同一科目，借贷在同一往来科目上相抵），不取表头结算科目
-- （结算科目只在制单时作借方银行科目，见 ArapProcVoucherBad）。
-- 收款单行 iRAmt* 清零（整张收款单用于收回，U8 取消时恢复成 iAmt*）。坏账准备余额：U8 是 update Ar_BadPara set
-- iRemainAmount=iRemainAmount+(本币) where autoid=(select max(autoid) from Ar_BadPara)，桥另要求那一行仍是闸门查过的登记年度的那一行。
-- 编号 Ap_CancelNo（cType HZ、cFlag AR）→ HZAR + 13 位补零序号。拒绝时 THROW 50101–50139；最后一个结果集是 k、n 计数。
SET NOCOUNT ON;

DECLARE @y smallint, @m tinyint, @reg nvarchar(10), @accounter nvarchar(20), @digest nvarchar(255), @dw nvarchar(40);
DECLARE @para_id int, @iid int, @code nvarchar(60), @amt_f money, @amt_n money;
DECLARE @dReg datetime, @pid int, @pyear int, @before money, @after money, @n int, @no nvarchar(30);
DECLARE @line int, @rows int, @msg nvarchar(200), @own nvarchar(80);
SELECT @y = y, @m = m, @reg = reg_date, @accounter = accounter, @digest = digest, @dw = dw, @para_id = para_id,
  @iid = receipt_id, @code = receipt, @amt_f = CONVERT(money, amt_f), @amt_n = CONVERT(money, amt_n)
FROM #bad_args;

IF @y IS NULL OR @m IS NULL OR NULLIF(LTRIM(ISNULL(@reg, N'')), N'') IS NULL OR NULLIF(LTRIM(ISNULL(@dw, N'')), N'') IS NULL
  OR ISNULL(@iid, 0) <= 0 OR NULLIF(LTRIM(ISNULL(@code, N'')), N'') IS NULL OR ISNULL(@amt_f, 0) <= 0
  THROW 50101, N'9H args missing', 1;
SET @dReg = CONVERT(datetime, CONVERT(date, @reg, 23));
IF NULLIF(LTRIM(ISNULL(@digest, N'')), N'') IS NULL SET @digest = N'坏账收回';
SET @accounter = ISNULL(@accounter, N'');

IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear = @y AND iperiod = @m AND ISNULL(bflag_AR, 0) = 1)
  THROW 50102, N'AR period already closed (GL_mend)', 1;

SELECT TOP 1 @pid = autoid, @pyear = iYear, @before = ISNULL(iRemainAmount, 0)
FROM Ar_BadPara WITH (UPDLOCK, HOLDLOCK) ORDER BY autoid DESC;
IF @pid IS NULL OR @pid <> ISNULL(@para_id, 0) OR ISNULL(@pyear, 0) <> @y
  THROW 50103, N'Ar_BadPara max(autoid) row changed', 1;

-- 收款单（同 U8 坏账收回界面可选的范围，审核由桥先做）：已审核、本客户、48 / AR、未核销、唯一一行应收款且余额等于原金额
-- 等于本次原币；往来明细只有本单的审核行（至少一行、未制单）。
SELECT @line = MIN(b.ID), @rows = COUNT(*)
FROM Ap_CloseBill h WITH (UPDLOCK, HOLDLOCK)
INNER JOIN Ap_CloseBills b WITH (UPDLOCK, HOLDLOCK) ON b.iID = h.iID
WHERE h.iID = @iid AND h.cVouchID = @code AND h.cVouchType = N'48' AND h.cFlag = N'AR' AND h.cDwCode = @dw
  AND NULLIF(LTRIM(ISNULL(h.cCheckMan, N'')), N'') IS NOT NULL AND NULLIF(LTRIM(ISNULL(h.cCancelMan, N'')), N'') IS NULL
  AND ISNULL(b.iType, 0) = 0 AND ISNULL(b.iRAmt_f, 0) = ISNULL(b.iAmt_f, 0) AND ISNULL(b.iRAmt, 0) = ISNULL(b.iAmt, 0)
  AND b.iRAmt_f = @amt_f AND b.iRAmt = @amt_n;
SET @own = N'AR48' + @code;
IF @line IS NULL OR @rows <> 1
  OR (SELECT COUNT(*) FROM Ap_CloseBills WHERE iID = @iid) <> 1
  OR NOT EXISTS (SELECT 1 FROM Ar_Detail WHERE cFlag = N'AR' AND cVouchType = N'48' AND cVouchID = @code
    AND cProcStyle = N'48' AND cCancelNo = @own)
  OR EXISTS (SELECT 1 FROM Ar_Detail WHERE cFlag = N'AR'
    AND ((cVouchType = N'48' AND cVouchID = @code) OR (cCoVouchType = N'48' AND cCoVouchID = @code))
    AND NOT (ISNULL(cProcStyle, N'') = N'48' AND ISNULL(cCancelNo, N'') = @own AND ISNULL(cPZid, N'') = N''))
BEGIN
  SET @msg = N'9H receipt not usable after sign: ' + @code;
  THROW 50121, @msg, 1;
END

-- 编号：同坏账发生。
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

-- 9H 借方处理行，挂在收款单上（U8 AddNew 的赋值列）；部门、业务员、项目取收款单行，空时取表头。
INSERT INTO Ar_Detail (
  iPeriod, cVouchType, cVouchSType, cVouchID, dVouchDate, dRegDate, cDwCode, cDeptCode, cPerson,
  cInvCode, iBVid, cCode, cItem_Class, cItemCode, csign, isignseq, ino_id, cDigest, iPrice,
  cexch_name, iExchRate, iDAmount, iCAmount, iDAmount_f, iCAmount_f, iDAmount_s, iCAmount_s,
  cOrderNo, cSSCode, cPayCode, cProcStyle, cCancelNo, cPZid, bPrePay, iFlag, cCoVouchType,
  cCoVouchID, cFlag, iClosesID, iCoClosesID, cGLSign, iGLno_id, dPZDate, cOperator, cCheckMan)
SELECT
  @m, N'48', NULL, h.cVouchID, h.dVouchDate, @dReg, h.cDwCode,
  COALESCE(NULLIF(b.cDepCode, N''), h.cDeptCode), COALESCE(NULLIF(b.cPersonCode, N''), h.cPerson),
  NULL, b.ID, b.cKm, COALESCE(NULLIF(b.cXmClass, N''), h.cItem_Class), COALESCE(NULLIF(b.cXm, N''), h.cItemCode),
  NULL, NULL, NULL, @digest, NULL,
  h.cexch_name, ISNULL(h.iExchRate, 1), @amt_n, 0, @amt_f, 0, 0, 0,
  NULL, h.cSSCode, NULL, N'9H', @no, NULL, 0, 0, N'48',
  h.cVouchID, N'AR', b.ID, b.ID, NULL, NULL, NULL, @accounter, NULL
FROM Ap_CloseBill h
INNER JOIN Ap_CloseBills b ON b.iID = h.iID
WHERE h.iID = @iid AND b.ID = @line;
IF @@ROWCOUNT <> 1
BEGIN
  SET @msg = N'9H insert failed: ' + @code;
  THROW 50122, @msg, 1;
END

-- U8 在这里审核收款单（实测只填 cCheckMan、dverifydate、dverifysystime）；桥已经用审核组件做过，不再写。

-- 收款单行余额清零（整张收回）。
UPDATE Ap_CloseBills SET iRAmt = 0, iRAmt_f = 0, iRAmt_s = 0 WHERE ID = @line AND iID = @iid;
IF @@ROWCOUNT <> 1
BEGIN
  SET @msg = N'9H receipt remainder failed: ' + @code;
  THROW 50122, @msg, 1;
END

UPDATE Ar_BadPara SET iRemainAmount = ISNULL(iRemainAmount, 0) + @amt_n WHERE autoid = @pid;
SELECT @after = ISNULL(iRemainAmount, 0) FROM Ar_BadPara WHERE autoid = @pid;

SELECT k, n FROM (VALUES
  (N'cancel_no', @no),
  (N'rows', N'1'),
  (N'line', CONVERT(nvarchar(20), @line)),
  (N'remain_before', CONVERT(nvarchar(40), CONVERT(decimal(28, 2), @before))),
  (N'remain_after', CONVERT(nvarchar(40), CONVERT(decimal(28, 2), @after)))) v (k, n);
