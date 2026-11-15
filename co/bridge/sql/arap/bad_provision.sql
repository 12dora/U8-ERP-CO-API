-- U8 应收 计提坏账准备（9F）：按坏账准备参数算本次计提额、编号、更新当年的坏账准备参数行。
-- 写入与 U8「计提坏账准备」界面计提、保存时执行的 SQL 一致（测试账套实测核对），由桥在请求连接、请求事务里整批执行（不分 GO）。
-- 参数取自调用方先建好的 #badp_args（ArapBadProvisionSql.Prepare）：
--   y 会计年度、m 会计期间、d 计提日期 yyyy-MM-dd、ys 会计年度第一天 yyyy-MM-dd（销售收入的起点）、
--   payday 1 = 账龄按「单据日期 + 付款条件信用天数」算（AccInformation AR BadAgeAnalyseDate=1），0 = 按单据日期。
-- 只更新当年已有的一行 Ar_BadPara（U8 在没有当年行时会插一行，接口拒绝，请先在 U8 里设置坏账准备参数）；不写往来明细（U8 同）。
-- 计提基数按 iJtStyle：
--   1 应收余额百分比法：Ar_Detail 应收（iFlag<3、cBusType 为空）借 − 贷 × nJtRate（同 U8，不限日期）。
--   2 账龄分析法：按 Ar_BadAge 区间（iCount 为区间上限天数，0 = 以上；iRate 为百分比）分段余额 × iRate / 100。
--   3 销售收入百分比法：已复核、非期初、未作废的销售发票本币金额（会计年度第一天至计提日期，U8 的取数窗口未经实测）× nJtRate。
-- 本次计提 = round(目标余额 − 当前坏账准备余额, 2)，可为负（冲回），为 0 拒绝。
-- 拒绝时 THROW 50160–50179（中文由桥按编号给出）；第一个结果集是账龄区间（非账龄法为空），最后一个是 k、n 计数。
SET NOCOUNT ON;

DECLARE @y smallint, @m tinyint, @d nvarchar(10), @ys nvarchar(10), @payday int;
DECLARE @dReg datetime, @dStart datetime, @rows int, @id int, @style int, @rate decimal(28, 10);
DECLARE @hz nvarchar(40), @pz nvarchar(60), @remain money, @base money, @target money, @jt money;
DECLARE @n int, @no nvarchar(30), @msg nvarchar(200);
SELECT @y = y, @m = m, @d = d, @ys = ys, @payday = payday FROM #badp_args;

IF @y IS NULL OR ISNULL(@m, 0) NOT BETWEEN 1 AND 12 OR NULLIF(LTRIM(ISNULL(@d, N'')), N'') IS NULL
    OR NULLIF(LTRIM(ISNULL(@ys, N'')), N'') IS NULL
  THROW 50160, N'9F y/m/d/ys required', 1;
SET @dReg = CONVERT(datetime, CONVERT(date, @d, 23));
SET @dStart = CONVERT(datetime, CONVERT(date, @ys, 23));

IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear = @y AND iperiod = @m AND ISNULL(bflag_AR, 0) <> 0)
  THROW 50161, N'AR period already closed (GL_mend)', 1;

-- 当年的坏账准备参数行（U8 select top 1 * from ar_badpara where iYear=）：必须恰好一行，加锁读。
SELECT @rows = COUNT(*) FROM Ar_BadPara WITH (UPDLOCK, HOLDLOCK) WHERE iYear = @y;
IF @rows = 0 THROW 50162, N'UnSet_Bad_Debt_Mention_Param (no Ar_BadPara row for the year)', 1;
IF @rows > 1 THROW 50163, N'more than one Ar_BadPara row for the year', 1;
SELECT @id = autoid, @style = iJtStyle, @rate = CONVERT(decimal(28, 10), ROUND(CONVERT(float, ISNULL(nJtRate, 0)), 8)), @hz = cHzCode,
  @pz = cPZID, @remain = ISNULL(iRemainAmount, 0)
FROM Ar_BadPara WITH (UPDLOCK, HOLDLOCK) WHERE iYear = @y;

IF ISNULL(@style, 0) NOT IN (1, 2, 3) THROW 50164, N'iJtStyle not 1/2/3 (Directly_Sale_Method unresolved)', 1;
IF NULLIF(LTRIM(ISNULL(@hz, N'')), N'') IS NULL THROW 50168, N'Bad_Debt_Mention_Param_Empty (cHzCode)', 1;
IF NULLIF(LTRIM(ISNULL(@pz, N'')), N'') IS NOT NULL
BEGIN
  SET @msg = N'Ar_BadPara cPZID set: ' + @pz;
  THROW 50167, @msg, 1;
END
-- U8 对余额百分比法和账龄分析法都要求设置了账龄区间（UnSet_Bad_Debt_Age_Interval）。
IF @style IN (1, 2) AND NOT EXISTS (SELECT 1 FROM Ar_BadAge) THROW 50165, N'UnSet_Bad_Debt_Age_Interval', 1;

IF OBJECT_ID(N'tempdb..#badp_age') IS NOT NULL DROP TABLE #badp_age;
CREATE TABLE #badp_age (seq int IDENTITY(1, 1), cnum nvarchar(20), lo int, hi int NULL, rate decimal(28, 10),
  bal money, amt money);
IF OBJECT_ID(N'tempdb..#badp_bal') IS NOT NULL DROP TABLE #badp_bal;
CREATE TABLE #badp_bal (age int, amt money);

SET @base = 0;
IF @style = 1
  SELECT @base = ISNULL(SUM(iDAmount), 0) - ISNULL(SUM(iCAmount), 0)
  FROM Ar_Detail
  WHERE cFlag = N'AR' AND iFlag < 3 AND ISNULL(cBusType, N'') = N'';

IF @style = 3
  SELECT @base = ISNULL(SUM(a.iNatMoney), 0)
  FROM SaleBillVouchs a
  INNER JOIN SaleBillVouch b ON a.SBVID = b.SBVID
  WHERE b.bFirst = 0 AND b.cInvalider IS NULL AND b.cVerifier <> N''
    AND b.dDate >= @dStart AND b.dDate <= @dReg;

IF @style IN (1, 3)
  SET @target = ROUND(@base * @rate, 2);

IF @style = 2
BEGIN
  -- 区间按上限天数排序，「以上」区间（iCount=0）最后；下限 = 上一区间上限 + 1，第一个区间从 0 天起（与 U8 界面的区间边界一致）。
  INSERT INTO #badp_age (cnum, lo, hi, rate)
  SELECT cNum, 0, CASE WHEN ISNULL(iCount, 0) = 0 THEN NULL ELSE iCount END, CONVERT(decimal(28, 10), ROUND(CONVERT(float, ISNULL(iRate, 0)), 8))
  FROM Ar_BadAge
  ORDER BY CASE WHEN ISNULL(iCount, 0) = 0 THEN 1 ELSE 0 END, iCount, cNum;
  IF (SELECT COUNT(*) FROM #badp_age WHERE hi IS NULL) > 1
    THROW 50170, N'Ar_BadAge has more than one open bucket (iCount=0)', 1;
  IF EXISTS (SELECT 1 FROM #badp_age a INNER JOIN #badp_age b ON a.seq < b.seq AND a.hi = b.hi)
    THROW 50170, N'Ar_BadAge has duplicate iCount', 1;
  UPDATE a SET lo = ISNULL((SELECT MAX(p.hi) FROM #badp_age p WHERE p.seq < a.seq), -1) + 1 FROM #badp_age a;

  -- 账龄 = 计提日期 − 账龄起算日（单据日期，BadAgeAnalyseDate=1 时加付款条件信用天数）；起算日晚于计提日期的不计。
  INSERT INTO #badp_bal (age, amt)
  SELECT DATEDIFF(day, DATEADD(day, CASE WHEN @payday = 1 THEN ISNULL(p.iPayCreDays, 0) ELSE 0 END, d.dRegDate), @dReg),
    ISNULL(d.iDAmount, 0) - ISNULL(d.iCAmount, 0)
  FROM Ar_Detail d
  LEFT JOIN PayCondition p ON d.cPayCode = p.cPayCode
  WHERE d.cFlag = N'AR' AND d.iFlag < 3;

  UPDATE a SET bal = ISNULL((SELECT SUM(b.amt) FROM #badp_bal b WHERE b.age >= a.lo AND (a.hi IS NULL OR b.age <= a.hi)), 0)
  FROM #badp_age a;
  UPDATE #badp_age SET amt = ROUND(bal * rate / 100, 2);
  SELECT @base = ISNULL(SUM(bal), 0), @target = ISNULL(SUM(amt), 0) FROM #badp_age;
END

SET @jt = ROUND(ISNULL(@target, 0) - @remain, 2);
IF @jt = 0 THROW 50166, N'Bad_Debt_Mention_Amount_Not_Equal_Zero', 1;

-- 编号：Ap_CancelNo（HZ/AR）带锁取当前号 + 1，处理号 = HZAR + 13 位补零序号（如 HZAR0000000000001）。没有这一行时补一行（同汇兑损益，未经实测）。
IF NOT EXISTS (SELECT 1 FROM Ap_CancelNo WITH (UPDLOCK, HOLDLOCK) WHERE cType = N'HZ' AND cFlag = N'AR')
  INSERT INTO Ap_CancelNo (cType, cFlag, iCancelNo) VALUES (N'HZ', N'AR', 0);
SELECT @n = ISNULL(iCancelNo, 0) + 1 FROM Ap_CancelNo WITH (UPDLOCK, HOLDLOCK) WHERE cType = N'HZ' AND cFlag = N'AR';
SET @no = N'HZAR' + REPLICATE(N'0', 13 - LEN(CONVERT(nvarchar(13), @n))) + CONVERT(nvarchar(13), @n);
UPDATE Ap_CancelNo SET iCancelNo = @n WHERE cType = N'HZ' AND cFlag = N'AR';

-- 写入（U8 按 iyear 更新；这里按已锁定的那一行 autoid）。FormatXML / DataXML 是 U8 界面的表格快照，接口不改。
UPDATE Ar_BadPara SET
  dJtDate = @dReg,
  iRemainAmount = ISNULL(iRemainAmount, 0) + @jt,
  iJtAmount = ISNULL(iJtAmount, 0) + @jt,
  cProcStyle = N'9F',
  cCancelNo = @no
WHERE autoid = @id AND iYear = @y;
IF @@ROWCOUNT <> 1 THROW 50169, N'Ar_BadPara row changed', 1;

SELECT cnum, lo AS bucket_from, hi AS bucket_to, bal AS balance, rate, amt AS amount FROM #badp_age ORDER BY seq;

SELECT k, n FROM (VALUES
  (N'style', CONVERT(nvarchar(40), @style)),
  (N'base', CONVERT(nvarchar(40), @base)),
  (N'rate', CONVERT(nvarchar(40), @rate)),
  (N'target', CONVERT(nvarchar(40), @target)),
  (N'remain_before', CONVERT(nvarchar(40), @remain)),
  (N'jt', CONVERT(nvarchar(40), @jt)),
  (N'remain_after', CONVERT(nvarchar(40), @remain + @jt)),
  (N'cancel_no', @no),
  (N'row_id', CONVERT(nvarchar(40), @id))) v (k, n);
