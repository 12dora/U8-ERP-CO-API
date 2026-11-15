-- U8 应收 / 应付 汇兑损益（9M）第一段：算差额、编号、建处理行、回写余额，填好销售发票回写临时表。
-- 写入与 U8「汇兑损益」界面执行的 SQL 一致（测试账套实测核对），由桥在请求连接、请求事务里整批执行（不分 GO）。
-- 参数取自调用方先建好的临时表（ArapExGainSql.Prepare）：
--   #exg_args (flag AR|AP, y 会计年度, m 会计期间, reg_date 登记日期 yyyy-MM-dd, accounter 操作员姓名,
--              cexch_name 币种, rate 调整汇率（文本，桥已取好：exch iType=3 或调用方指定）, digest 摘要, settle 1=含结清)
--   #exg_dw (cDwCode)：只处理这些往来单位；空表 = 全部。
--   #ap_SaleBillVouchHXdata (autoid, iexchsum, imoneysum)：本段填好后桥调 U8 的 clsWrite2Bill.UpdateBillForAR（同一连接），
--   再执行第二段 exgain_persist.sql 写入往来明细（U8 的顺序：余额回写 → UpdateBillForAR → 采购发票 → 写明细）。
-- 只做以下单据类型：应收 26 / 27 / R0–R9 / 48，应付 01 / 02 / P0–P9 / 49。应收侧的付款单 49、应付侧的收款单 48
-- （U8 对它们的金额取反写法没有实测）、RZ / 进出口 / 代理 / 合同类不做，不进差额表。
-- 表头登记（没有发票行，行号 0）的发票 26 / 27 / 01 / 02 整张跳过（取消时 U8 的发票回写对不上行），清单作为第一个结果集返回。
-- 拒绝时 THROW 50001–50099（中文由桥按编号给出）；最后一个结果集是 k、n 计数。
SET NOCOUNT ON;

DECLARE @flag nvarchar(4), @y smallint, @m tinyint, @reg nvarchar(10), @accounter nvarchar(20);
DECLARE @cexch nvarchar(60), @rate decimal(26, 10), @digest nvarchar(255), @settle int;
DECLARE @dReg datetime, @n int, @rows int, @prefix nvarchar(8), @ctype nvarchar(8);
DECLARE @gap money, @skipped int, @want int, @got int, @flip bit, @vpat nvarchar(10), @bad nvarchar(130), @msg nvarchar(200);
SELECT @flag = flag, @y = y, @m = m, @reg = reg_date, @accounter = accounter, @cexch = cexch_name,
  @rate = CONVERT(decimal(26, 10), rate), @digest = digest, @settle = settle
FROM #exg_args;

IF ISNULL(@flag, N'') NOT IN (N'AR', N'AP') THROW 50001, N'9M flag must be AR or AP', 1;
IF @y IS NULL OR @m IS NULL OR NULLIF(LTRIM(ISNULL(@reg, N'')), N'') IS NULL THROW 50002, N'9M y/m/reg_date required', 1;
IF ISNULL(@rate, 0) <= 0 THROW 50022, N'Exchange_Rate empty or <= 0', 1;
SET @dReg = CONVERT(datetime, CONVERT(date, @reg, 23));
IF NULLIF(LTRIM(ISNULL(@digest, N'')), N'') IS NULL
  SET @digest = CASE WHEN @flag = N'AR' THEN N'应收汇兑损益' ELSE N'应付汇兑损益' END;
SET @accounter = ISNULL(@accounter, N'');
SET @prefix = CASE WHEN @flag = N'AR' THEN N'SYRAR' ELSE N'SYPAP' END;
SET @ctype = CASE WHEN @flag = N'AR' THEN N'SYR' ELSE N'SYP' END;
-- 应收单 / 应付单：类型模式，以及表头哪个方向（Ap_Vouch.bd_c）与本侧余额方向相反（应收贷方 0、应付借方 1）。
SET @vpat = CASE WHEN @flag = N'AR' THEN N'R[0-9]' ELSE N'P[0-9]' END;
SET @flip = CASE WHEN @flag = N'AR' THEN 0 ELSE 1 END;

IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear = @y AND iperiod = @m AND (
    (@flag = N'AR' AND ISNULL(bflag_AR, 0) = 1) OR (@flag = N'AP' AND ISNULL(bflag_AP, 0) = 1)))
  THROW 50020, N'AR/AP period already closed (GL_mend)', 1;
IF NOT EXISTS (SELECT 1 FROM foreigncurrency WHERE cexch_name = @cexch AND ISNULL(bCal, 0) = 1)
  THROW 50021, N'currency has no bCal=1 (Select_One_Currency_At_Least)', 1;

IF OBJECT_ID(N'tempdb..#Hdsy') IS NOT NULL DROP TABLE #Hdsy;
CREATE TABLE #Hdsy (
  vtype nvarchar(10), vid nvarchar(60), dDate datetime, cDwCode nvarchar(40), cCode nvarchar(40),
  ID int, cInvCode nvarchar(60), cexch_name nvarchar(60),
  iRAmount_f money, iRAmount money, bd_c bit, iAjustAmount money, iDiffAmount money,
  cDeptCode nvarchar(40), cPerson nvarchar(40), cItem_Class nvarchar(10), cItemCode nvarchar(60), cItemName nvarchar(255),
  csign nvarchar(10), cContractType nvarchar(20), cContractID nvarchar(64), GUID nvarchar(100),
  iPrice float, cOrderNo nvarchar(60), cSSCode nvarchar(10), cPayCode nvarchar(10), bPrepay tinyint,
  cDefine1 nvarchar(20), cDefine2 nvarchar(20), cDefine3 nvarchar(20), cDefine4 datetime, cDefine5 int, cDefine6 datetime,
  cDefine7 float, cDefine8 nvarchar(4), cDefine9 nvarchar(8), cDefine10 nvarchar(60),
  cDefine11 nvarchar(120), cDefine12 nvarchar(120), cDefine13 nvarchar(120), cDefine14 nvarchar(120), cDefine15 int, cDefine16 float,
  cDefine22 nvarchar(60), cDefine23 nvarchar(60), cDefine24 nvarchar(60), cDefine25 nvarchar(60), cDefine26 float, cDefine27 float,
  cDefine28 nvarchar(120), cDefine29 nvarchar(120), cDefine30 nvarchar(120), cDefine31 nvarchar(120), cDefine32 nvarchar(120),
  cDefine33 nvarchar(120), cDefine34 int,
  cCheckMan nvarchar(40), iOrderType tinyint, cDLCode nvarchar(40), bCredit tinyint, dGatheringDate datetime, idlsid int,
  cFlag nvarchar(4), cBusType nvarchar(20), AutoID int, rid int IDENTITY(1, 1)
);
CREATE INDEX IDX_Hdsy ON #Hdsy (cDwCode, vtype, vid);

-- 未结清的外币余额（同 U8 界面的取数结果）：按（往来单位、单据类型、单号、币种、行、存货、BalancesGUID）分组，
-- 应收：收付款类（4%）余额 = 贷 − 借，其余 = 借 − 贷；应付相反。原币或本币余额不为 0 的留下。
IF @flag = N'AR'
BEGIN
  INSERT INTO #Hdsy (
    vtype, vid, dDate, cDwCode, cCode, ID, cInvCode, cexch_name, iRAmount_f, iRAmount, bd_c,
    cDeptCode, cPerson, cItem_Class, cItemCode, cItemName, csign, cContractType, cContractID, GUID,
    iPrice, cOrderNo, cSSCode, cPayCode, bPrepay,
    cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7, cDefine8, cDefine9, cDefine10,
    cDefine11, cDefine12, cDefine13, cDefine14, cDefine15, cDefine16,
    cDefine22, cDefine23, cDefine24, cDefine25, cDefine26, cDefine27, cDefine28, cDefine29, cDefine30,
    cDefine31, cDefine32, cDefine33, cDefine34,
    cCheckMan, iOrderType, cDLCode, bCredit, dGatheringDate, idlsid, cFlag, cBusType, AutoID)
  SELECT
    a.cCoVouchType, a.cCoVouchID, MIN(a.dVouchDate), a.cDwCode, MAX(a.cCode),
    CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iCoClosesID ELSE a.iBVid END, a.cInvCode, MAX(a.cexch_name),
    SUM(CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iCAmount_f - a.iDAmount_f ELSE a.iDAmount_f - a.iCAmount_f END),
    SUM(CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iCAmount - a.iDAmount ELSE a.iDAmount - a.iCAmount END),
    CASE WHEN a.cCoVouchType LIKE N'4%' THEN 0 ELSE 1 END,
    MAX(a.cDeptCode), MAX(a.cPerson), MAX(a.cItem_Class), MAX(a.cItemCode), MAX(a.cItemName), MAX(a.cSign),
    MAX(a.cContractType), MAX(a.cContractID), a.BalancesGUID,
    MAX(a.iPrice), MAX(a.cOrderNo), MAX(a.cSSCode), MAX(a.cPayCode), MAX(CONVERT(tinyint, a.bPrepay)),
    MAX(a.cDefine1), MAX(a.cDefine2), MAX(a.cDefine3), MAX(a.cDefine4), MAX(a.cDefine5), MAX(a.cDefine6),
    MAX(a.cDefine7), MAX(a.cDefine8), MAX(a.cDefine9), MAX(a.cDefine10),
    MAX(a.cDefine11), MAX(a.cDefine12), MAX(a.cDefine13), MAX(a.cDefine14), MAX(a.cDefine15), MAX(a.cDefine16),
    MIN(a.cDefine22), MIN(a.cDefine23), MIN(a.cDefine24), MIN(a.cDefine25), MIN(a.cDefine26), MIN(a.cDefine27),
    MIN(a.cDefine28), MIN(a.cDefine29), MIN(a.cDefine30), MIN(a.cDefine31), MIN(a.cDefine32), MIN(a.cDefine33),
    MIN(a.cDefine34),
    MAX(a.cCheckMan), MAX(a.iOrderType), MAX(a.cDLCode), MAX(CONVERT(tinyint, a.bCredit)), MAX(a.dGatheringDate),
    MAX(a.idlsid), a.cFlag, MAX(a.cBusType), MAX(a.Auto_ID)
  FROM Ar_V_Detail a
  INNER JOIN Customer ON a.cDwCode = Customer.cCusCode
  WHERE a.iFlag <= 2 AND a.cFlag = N'AR' AND a.cexch_name = @cexch
    AND (a.cCoVouchType IN (N'26', N'27', N'48') OR a.cCoVouchType LIKE N'R[0-9]')
    AND EXISTS (SELECT 1 FROM Ap_VouchType_base t WHERE t.cTypeCode = a.cCoVouchType AND t.LocaleID = N'zh-CN')
    AND (NOT EXISTS (SELECT 1 FROM #exg_dw) OR a.cDwCode IN (SELECT cDwCode FROM #exg_dw))
  GROUP BY a.cDwCode, a.cFlag, a.cCoVouchType, a.cCoVouchID, a.cexch_name,
    CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iCoClosesID ELSE a.iBVid END, a.cInvCode, a.BalancesGUID
  HAVING SUM(a.iDAmount_f - a.iCAmount_f) <> 0 OR SUM(a.iDAmount - a.iCAmount) <> 0;
END
ELSE
BEGIN
  INSERT INTO #Hdsy (
    vtype, vid, dDate, cDwCode, cCode, ID, cInvCode, cexch_name, iRAmount_f, iRAmount, bd_c,
    cDeptCode, cPerson, cItem_Class, cItemCode, cItemName, csign, cContractType, cContractID, GUID,
    iPrice, cOrderNo, cSSCode, cPayCode, bPrepay,
    cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7, cDefine8, cDefine9, cDefine10,
    cDefine11, cDefine12, cDefine13, cDefine14, cDefine15, cDefine16,
    cDefine22, cDefine23, cDefine24, cDefine25, cDefine26, cDefine27, cDefine28, cDefine29, cDefine30,
    cDefine31, cDefine32, cDefine33, cDefine34,
    cCheckMan, iOrderType, cDLCode, bCredit, dGatheringDate, idlsid, cFlag, cBusType, AutoID)
  SELECT
    a.cCoVouchType, a.cCoVouchID, MIN(a.dVouchDate), a.cDwCode, MAX(a.cCode),
    CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iCoClosesID ELSE a.iBVid END, a.cInvCode, MAX(a.cexch_name),
    SUM(CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iDAmount_f - a.iCAmount_f ELSE a.iCAmount_f - a.iDAmount_f END),
    SUM(CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iDAmount - a.iCAmount ELSE a.iCAmount - a.iDAmount END),
    CASE WHEN a.cCoVouchType LIKE N'4%' THEN 1 ELSE 0 END,
    MAX(a.cDeptCode), MAX(a.cPerson), MAX(a.cItem_Class), MAX(a.cItemCode), MAX(a.cItemName), MAX(a.cSign),
    MAX(a.cContractType), MAX(a.cContractID), a.BalancesGUID,
    MAX(a.iPrice), MAX(a.cOrderNo), MAX(a.cSSCode), MAX(a.cPayCode), MAX(CONVERT(tinyint, a.bPrepay)),
    MAX(a.cDefine1), MAX(a.cDefine2), MAX(a.cDefine3), MAX(a.cDefine4), MAX(a.cDefine5), MAX(a.cDefine6),
    MAX(a.cDefine7), MAX(a.cDefine8), MAX(a.cDefine9), MAX(a.cDefine10),
    MAX(a.cDefine11), MAX(a.cDefine12), MAX(a.cDefine13), MAX(a.cDefine14), MAX(a.cDefine15), MAX(a.cDefine16),
    MIN(a.cDefine22), MIN(a.cDefine23), MIN(a.cDefine24), MIN(a.cDefine25), MIN(a.cDefine26), MIN(a.cDefine27),
    MIN(a.cDefine28), MIN(a.cDefine29), MIN(a.cDefine30), MIN(a.cDefine31), MIN(a.cDefine32), MIN(a.cDefine33),
    MIN(a.cDefine34),
    MAX(a.cCheckMan), MAX(a.iOrderType), MAX(a.cDLCode), MAX(CONVERT(tinyint, a.bCredit)), MAX(a.dGatheringDate),
    MAX(a.idlsid), a.cFlag, MAX(a.cBusType), MAX(a.Auto_ID)
  FROM Ap_Detail a
  INNER JOIN Vendor ON a.cDwCode = Vendor.cVenCode
  WHERE a.iFlag <= 2 AND a.cFlag = N'AP' AND a.cexch_name = @cexch
    AND (a.cCoVouchType IN (N'01', N'02', N'49') OR a.cCoVouchType LIKE N'P[0-9]')
    AND EXISTS (SELECT 1 FROM Ap_VouchType_base t WHERE t.cTypeCode = a.cCoVouchType AND t.LocaleID = N'zh-CN')
    AND (NOT EXISTS (SELECT 1 FROM #exg_dw) OR a.cDwCode IN (SELECT cDwCode FROM #exg_dw))
  GROUP BY a.cDwCode, a.cFlag, a.cCoVouchType, a.cCoVouchID, a.cexch_name,
    CASE WHEN a.cCoVouchType LIKE N'4%' THEN a.iCoClosesID ELSE a.iBVid END, a.cInvCode, a.BalancesGUID
  HAVING SUM(a.iDAmount_f - a.iCAmount_f) <> 0 OR SUM(a.iDAmount - a.iCAmount) <> 0;
END

UPDATE #Hdsy SET iRAmount = ROUND(iRAmount, 2);

-- 表头登记的发票（行号 0 或空）：整张跳过，不做汇兑损益。
IF OBJECT_ID(N'tempdb..#exg_skip') IS NOT NULL DROP TABLE #exg_skip;
CREATE TABLE #exg_skip (cDwCode nvarchar(40), vtype nvarchar(10), vid nvarchar(60));
INSERT INTO #exg_skip (cDwCode, vtype, vid)
SELECT DISTINCT cDwCode, vtype, vid FROM #Hdsy WHERE vtype IN (N'26', N'27', N'01', N'02') AND ISNULL(ID, 0) <= 0;
SET @skipped = @@ROWCOUNT;
DELETE h FROM #Hdsy h
INNER JOIN #exg_skip s ON s.cDwCode = h.cDwCode AND s.vtype = h.vtype AND s.vid = h.vid;

-- 不含结清：原币余额已为 0、只剩本币尾差的单据整张不处理。
IF ISNULL(@settle, 1) = 0
  DELETE h FROM #Hdsy h
  INNER JOIN (SELECT cDwCode, vtype, vid FROM #Hdsy GROUP BY cDwCode, vtype, vid HAVING SUM(iRAmount_f) = 0) z
    ON z.cDwCode = h.cDwCode AND z.vtype = h.vtype AND z.vid = h.vid;

-- 调整后本币 = 原币余额 × 汇率（两位小数），差额 = 调整后本币 − 本币余额；结清：整张单据原币余额为 0 时差额 = −本币余额。
UPDATE #Hdsy SET iAjustAmount = CONVERT(decimal(18, 2), iRAmount_f * @rate);
UPDATE #Hdsy SET iDiffAmount = ROUND(iAjustAmount - iRAmount, 2);
UPDATE h SET iDiffAmount = -h.iRAmount
FROM #Hdsy h
INNER JOIN (SELECT cDwCode, vtype, vid FROM #Hdsy GROUP BY cDwCode, vtype, vid HAVING SUM(iRAmount_f) = 0) z
  ON z.cDwCode = h.cDwCode AND z.vtype = h.vtype AND z.vid = h.vid;

-- 尾差（本脚本只有一个币种）：差额合计应为 round(调整后合计 − 余额合计, 2)。结清单据的行原币不为 0、合计为 0 时
-- （如 +10 / −10）差额取 −本币余额，与调整后本币之和差几分；U8 在原币余额不为 0 的行里按差额从大到小取前 N 行各调 ±0.01，
-- N = |尾差| / 0.01（推断）。同差额时按取数顺序定序。
SELECT @gap = ROUND(SUM(iAjustAmount) - SUM(iRAmount), 2) - SUM(iDiffAmount) FROM #Hdsy;
IF ISNULL(@gap, 0) <> 0
  UPDATE #Hdsy SET iDiffAmount = iDiffAmount + CASE WHEN @gap > 0 THEN 0.01 ELSE -0.01 END
  WHERE rid IN (
    SELECT TOP (CONVERT(int, ROUND(ABS(@gap) * 100, 0))) rid FROM #Hdsy
    WHERE iRAmount_f <> 0 ORDER BY iDiffAmount DESC, rid);

DELETE FROM #Hdsy WHERE ISNULL(iDiffAmount, 0) = 0;
SELECT @rows = COUNT(*) FROM #Hdsy;
IF @rows = 0 AND @skipped > 0
BEGIN
  SELECT TOP 1 @bad = vtype + N' ' + vid FROM #exg_skip ORDER BY cDwCode, vtype, vid;
  SET @msg = N'only head-level invoices left: ' + @bad;
  THROW 50034, @msg, 1;
END
IF @rows = 0 THROW 50023, N'no 9M rows (iDiffAmount all 0)', 1;

-- 编号：Ap_CancelNo（SYR/AR、SYP/AP）带锁取当前号，每个（往来单位、单据类型、单号）一个处理号，
-- 处理号 = SYRAR / SYPAP + 10 位补零序号；Ap_CancelNo 推进到最大号。账套里还没做过这一侧的汇兑损益（没有这一行）时补一行（未经实测）。
IF NOT EXISTS (SELECT 1 FROM Ap_CancelNo WITH (UPDLOCK, HOLDLOCK) WHERE cType = @ctype AND cFlag = @flag)
  INSERT INTO Ap_CancelNo (cType, cFlag, iCancelNo) VALUES (@ctype, @flag, 0);
SELECT @n = ISNULL(iCancelNo, 0) FROM Ap_CancelNo WITH (UPDLOCK, HOLDLOCK) WHERE cType = @ctype AND cFlag = @flag;

IF OBJECT_ID(N'tempdb..#Num') IS NOT NULL DROP TABLE #Num;
CREATE TABLE #Num (autoid int IDENTITY(1, 1), cDwCode nvarchar(40), vtype nvarchar(10), vid nvarchar(60), cCancelNo nvarchar(30));
INSERT INTO #Num (cDwCode, vtype, vid)
SELECT cDwCode, vtype, vid FROM #Hdsy GROUP BY cDwCode, vtype, vid ORDER BY cDwCode, vtype, vid;
UPDATE #Num SET cCancelNo = @prefix + REPLICATE(N'0', 10 - LEN(CONVERT(nvarchar(12), autoid + @n)))
  + CONVERT(nvarchar(12), autoid + @n);
UPDATE Ap_CancelNo SET iCancelNo = (SELECT MAX(autoid) FROM #Num) + @n WHERE cType = @ctype AND cFlag = @flag;

-- 处理行：9M、本期、登记日期、汇率 0、原币 0、iFlag 0；iAmount 暂存差额（不写进明细）。
IF OBJECT_ID(N'tempdb..#HdsyNew') IS NOT NULL DROP TABLE #HdsyNew;
CREATE TABLE #HdsyNew (
  ID int, bd_c bit, iAmount money, cDwCode nvarchar(40), iPeriod tinyint, dRegDate datetime, dPZDate datetime, dVouchDate datetime,
  cVouchType nvarchar(10), cVouchID nvarchar(60), cCoVouchType nvarchar(10), cCoVouchID nvarchar(60), cProcStyle nvarchar(10),
  cDigest nvarchar(255), cexch_name nvarchar(60), iExchRate float, cDeptCode nvarchar(40), cPerson nvarchar(40),
  cInvCode nvarchar(60), cCode nvarchar(40), cItem_Class nvarchar(10), cItemCode nvarchar(60), cItemName nvarchar(255),
  cContractType nvarchar(20), cContractID nvarchar(64), cSign nvarchar(10),
  iPrice float, cOrderNo nvarchar(60), cSSCode nvarchar(10), cPayCode nvarchar(10), bPrepay tinyint,
  cDefine1 nvarchar(20), cDefine2 nvarchar(20), cDefine3 nvarchar(20), cDefine4 datetime, cDefine5 int, cDefine6 datetime,
  cDefine7 float, cDefine8 nvarchar(4), cDefine9 nvarchar(8), cDefine10 nvarchar(60),
  cDefine11 nvarchar(120), cDefine12 nvarchar(120), cDefine13 nvarchar(120), cDefine14 nvarchar(120), cDefine15 int, cDefine16 float,
  cDefine22 nvarchar(60), cDefine23 nvarchar(60), cDefine24 nvarchar(60), cDefine25 nvarchar(60), cDefine26 float, cDefine27 float,
  cDefine28 nvarchar(120), cDefine29 nvarchar(120), cDefine30 nvarchar(120), cDefine31 nvarchar(120), cDefine32 nvarchar(120),
  cDefine33 nvarchar(120), cDefine34 int,
  cOperator nvarchar(40), cCheckMan nvarchar(40), iOrderType tinyint, cDLCode nvarchar(40), bCredit tinyint,
  dGatheringDate datetime, idlsid int, iClosesID int, iCoClosesID int, BalancesGUID nvarchar(100), iBVid int,
  iDAmount money, iDAmount_f money, iDAmount_s float, iCAmount money, iCAmount_f money, iCAmount_s float,
  cCancelNo nvarchar(30), cFlag nvarchar(4), cBusType nvarchar(20), iFlag tinyint
);

INSERT INTO #HdsyNew (
  ID, bd_c, iAmount, cDwCode, iPeriod, dRegDate, dPZDate, dVouchDate, cVouchType, cVouchID, cCoVouchType, cCoVouchID, cProcStyle,
  cDigest, cexch_name, iExchRate, cDeptCode, cPerson, cInvCode, cCode, cItem_Class, cItemCode, cItemName,
  cContractType, cContractID, cSign, iPrice, cOrderNo, cSSCode, cPayCode, bPrepay,
  cDefine1, cDefine2, cDefine3, cDefine4, cDefine5, cDefine6, cDefine7, cDefine8, cDefine9, cDefine10,
  cDefine11, cDefine12, cDefine13, cDefine14, cDefine15, cDefine16,
  cDefine22, cDefine23, cDefine24, cDefine25, cDefine26, cDefine27, cDefine28, cDefine29, cDefine30,
  cDefine31, cDefine32, cDefine33, cDefine34,
  cOperator, cCheckMan, iOrderType, cDLCode, bCredit, dGatheringDate, idlsid, iClosesID, iCoClosesID, BalancesGUID, iBVid,
  iDAmount, iDAmount_f, iDAmount_s, iCAmount, iCAmount_f, iCAmount_s, cCancelNo, cFlag, cBusType, iFlag)
SELECT
  h.ID, h.bd_c, h.iDiffAmount, h.cDwCode, @m, @dReg, @dReg, h.dDate, h.vtype, h.vid, h.vtype, h.vid, N'9M',
  @digest, h.cexch_name, 0, h.cDeptCode, h.cPerson, h.cInvCode, h.cCode, h.cItem_Class, h.cItemCode, h.cItemName,
  h.cContractType, h.cContractID, h.csign, h.iPrice, h.cOrderNo, h.cSSCode, h.cPayCode, h.bPrepay,
  h.cDefine1, h.cDefine2, h.cDefine3, h.cDefine4, h.cDefine5, h.cDefine6, h.cDefine7, h.cDefine8, h.cDefine9, h.cDefine10,
  h.cDefine11, h.cDefine12, h.cDefine13, h.cDefine14, h.cDefine15, h.cDefine16,
  h.cDefine22, h.cDefine23, h.cDefine24, h.cDefine25, h.cDefine26, h.cDefine27, h.cDefine28, h.cDefine29, h.cDefine30,
  h.cDefine31, h.cDefine32, h.cDefine33, h.cDefine34,
  @accounter, h.cCheckMan, h.iOrderType, h.cDLCode, h.bCredit, h.dGatheringDate, h.idlsid,
  CASE WHEN h.vtype LIKE N'4%' THEN h.ID ELSE 0 END, CASE WHEN h.vtype LIKE N'4%' THEN h.ID ELSE 0 END,
  NULL, h.ID,
  0, 0, 0, 0, 0, 0, n.cCancelNo, h.cFlag, h.cBusType, 0
FROM #Hdsy h
INNER JOIN #Num n ON n.cDwCode = h.cDwCode AND n.vtype = h.vtype AND n.vid = h.vid
ORDER BY h.cDwCode, h.vtype, h.vid;

-- 借贷方向：应收 48 记贷方、26 / 27 / R* 记借方；应付 49 记借方、01 / 02 / P* 记贷方。
IF @flag = N'AR'
BEGIN
  UPDATE #HdsyNew SET iCAmount = iAmount WHERE cVouchType = N'48';
  UPDATE #HdsyNew SET iDAmount = iAmount WHERE cVouchType IN (N'26', N'27') OR cVouchType LIKE N'R[0-9]';
END
ELSE
BEGIN
  UPDATE #HdsyNew SET iDAmount = iAmount WHERE cVouchType = N'49';
  UPDATE #HdsyNew SET iCAmount = iAmount WHERE cVouchType IN (N'01', N'02') OR cVouchType LIKE N'P[0-9]';
END

-- 应收单 / 应付单表头的本币余额（原币余额不动）。按单据自己的方向（Ap_Vouch.bd_c）定符号，与取消（exgain_cancel.sql）互逆：
-- 借方差额在余额方向相反的单据上（应收贷方 bd_c=0、应付借方 bd_c=1）是减少。每张单据都要改到一行，否则回滚（50033）。
SELECT @want = COUNT(*) FROM (SELECT DISTINCT cVouchType, cVouchID FROM #HdsyNew WHERE cVouchType LIKE @vpat) t;
UPDATE v SET iRAmount = v.iRAmount + CASE WHEN v.bd_c = @flip THEN -t.amt ELSE t.amt END
FROM Ap_Vouch v
INNER JOIN (SELECT cVouchType, cVouchID, SUM(iAmount) AS amt
            FROM #HdsyNew WHERE cVouchType LIKE @vpat GROUP BY cVouchType, cVouchID) t
  ON v.cVouchType = t.cVouchType AND v.cVouchID = t.cVouchID AND v.cFlag = @flag;
SET @got = @@ROWCOUNT;
IF @got <> @want
BEGIN
  SELECT TOP 1 @bad = h.cVouchType + N' ' + h.cVouchID FROM #HdsyNew h
  WHERE h.cVouchType LIKE @vpat
    AND NOT EXISTS (SELECT 1 FROM Ap_Vouch v WHERE v.cVouchType = h.cVouchType AND v.cVouchID = h.cVouchID AND v.cFlag = @flag)
  ORDER BY h.cVouchType, h.cVouchID;
  SET @msg = N'Ap_Vouch remainder rows ' + CONVERT(nvarchar(12), @got) + N'/' + CONVERT(nvarchar(12), @want)
    + N': ' + ISNULL(@bad, N'');
  THROW 50033, @msg, 1;
END

-- 收付款单行的本币余额。
SELECT @want = COUNT(DISTINCT ID) FROM #HdsyNew WHERE cVouchType IN (N'48', N'49');
UPDATE c SET iRAmt = c.iRAmt + t.amt
FROM Ap_CloseBills c
INNER JOIN (SELECT ID, SUM(iAmount) AS amt FROM #HdsyNew WHERE cVouchType IN (N'48', N'49') GROUP BY ID) t ON c.ID = t.ID;
SET @got = @@ROWCOUNT;
IF @got <> @want
BEGIN
  SELECT TOP 1 @bad = h.cVouchType + N' ' + h.cVouchID FROM #HdsyNew h
  WHERE h.cVouchType IN (N'48', N'49') AND NOT EXISTS (SELECT 1 FROM Ap_CloseBills c WHERE c.ID = h.ID)
  ORDER BY h.cVouchType, h.cVouchID;
  SET @msg = N'Ap_CloseBills remainder rows ' + CONVERT(nvarchar(12), @got) + N'/' + CONVERT(nvarchar(12), @want)
    + N': ' + ISNULL(@bad, N'');
  THROW 50033, @msg, 1;
END

-- 销售发票：U8 交给 clsWrite2Bill.UpdateBillForAR（iexchsum 0，imoneysum = −差额）；桥在本段之后调用。
TRUNCATE TABLE #ap_SaleBillVouchHXdata;
IF @flag = N'AR'
  INSERT INTO #ap_SaleBillVouchHXdata (autoid, iexchsum, imoneysum)
  SELECT ID, 0, CONVERT(decimal(29, 6), SUM(iAmount) * -1) FROM #HdsyNew
  WHERE cVouchType IN (N'26', N'27') GROUP BY ID;

-- 采购发票：U8 这里直接改累计（iTotal −= 差额），不调 UpdateBillForAP。每个发票行都要改到，否则回滚（50033）。
IF @flag = N'AP'
BEGIN
  SELECT @want = COUNT(DISTINCT ID) FROM #HdsyNew WHERE cVouchType IN (N'01', N'02');
  UPDATE p SET iTotal = ISNULL(p.iTotal, 0) - t.amt
  FROM PurBillVouchs p
  INNER JOIN (SELECT ID, SUM(iAmount) AS amt FROM #HdsyNew WHERE cVouchType IN (N'01', N'02') GROUP BY ID) t ON p.ID = t.ID;
  SET @got = @@ROWCOUNT;
  IF @got <> @want
  BEGIN
    SELECT TOP 1 @bad = h.cVouchType + N' ' + h.cVouchID FROM #HdsyNew h
    WHERE h.cVouchType IN (N'01', N'02') AND NOT EXISTS (SELECT 1 FROM PurBillVouchs p WHERE p.ID = h.ID)
    ORDER BY h.cVouchType, h.cVouchID;
    SET @msg = N'PurBillVouchs remainder rows ' + CONVERT(nvarchar(12), @got) + N'/' + CONVERT(nvarchar(12), @want)
      + N': ' + ISNULL(@bad, N'');
    THROW 50033, @msg, 1;
  END
END

-- 跳过的表头登记发票（每张一行，最多 200 张；总数见计数 skipped）。
SELECT TOP 200 cDwCode AS partner, vtype, vid FROM #exg_skip ORDER BY cDwCode, vtype, vid;

SELECT k, n FROM (VALUES
  (N'rows', CONVERT(nvarchar(20), @rows)),
  (N'skipped', CONVERT(nvarchar(20), @skipped)),
  (N'batches', CONVERT(nvarchar(20), (SELECT COUNT(*) FROM #Num))),
  (N'com_ar', CONVERT(nvarchar(20), (SELECT COUNT(*) FROM #ap_SaleBillVouchHXdata)))) v (k, n);
