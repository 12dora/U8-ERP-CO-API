-- U8 存货核算 月末结账。由桥在请求连接、请求事务里整批执行（不分 GO）；参数取自调用方先建好的 #ia_args
-- （y、m、keep_date、accounter、on_uncosted）。拒绝时 THROW 50000–50099，桥按编号转成 409。
-- 依据：测试账套实测（回滚事务），见 docs/api-reference.md「存货核算记账与期末处理」。
-- 月末结账: DataCheck-equivalent guards + IA_Close + UPDATE GL_mend.bflag_IA.
-- ONE batch. No GO.
SET NOCOUNT ON;

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @m tinyint; SELECT @m = m FROM #ia_args;
DECLARE @cAccounter nvarchar(20); SELECT @cAccounter = accounter FROM #ia_args;
DECLARE @dS datetime, @dE datetime, @dSDate datetime, @dNext datetime;
DECLARE @WhStyle nvarchar(20);
DECLARE @iQuanPrec tinyint, @CostPrec tinyint;
DECLARE @bBusiness bit, @bDif bit, @bRed bit;
DECLARE @py smallint, @pm tinyint;
DECLARE @n int, @msg nvarchar(400);
DECLARE @bChk nvarchar(20);

SET @dS = DATEFROMPARTS(@y, @m, 1);
SET @dE = EOMONTH(@dS);
SET @dNext = DATEADD(day, 1, @dE);
SET @dSDate = DATEADD(month, 1, @dS); -- first day of next month (keep date for type-24; unused under 单到回冲)
SET @py = CASE WHEN @m=1 THEN @y-1 ELSE @y END;
SET @pm = CASE WHEN @m=1 THEN 12 ELSE @m-1 END;

SELECT @WhStyle = cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'cValueStyle';
SELECT @iQuanPrec = CONVERT(tinyint, cValue) FROM AccInformation WHERE cSysID=N'AA' AND cName=N'iStrsQuanDecDgt';
SELECT @CostPrec  = CONVERT(tinyint, cValue) FROM AccInformation WHERE cSysID=N'AA' AND cName=N'iStrsPriDecDgt';
SELECT @bChk = cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'bcheckedpribooking';
SET @WhStyle = ISNULL(@WhStyle, N'按仓库核算');
SET @iQuanPrec = ISNULL(@iQuanPrec, 2);
SET @CostPrec  = ISNULL(@CostPrec, 6);
SET @bBusiness = CASE WHEN EXISTS (
  SELECT 1 FROM AccInformation WHERE cName=N'cSysVersion' AND cValue LIKE N'%工业%') THEN 1 ELSE 0 END;
SET @bDif = CASE WHEN EXISTS (
  SELECT 1 FROM AccInformation WHERE cSysID=N'IA' AND cName=N'bDealDif' AND cValue IN (N'TRUE',N'True',N'true',N'1'))
  THEN 1 ELSE 0 END;
SET @bRed = 0; -- 单到回冲; IA_Close gates type-24 on cEstimate inside the proc.

-- Month already closed
IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=@m AND ISNULL(bflag_IA,0)=1)
  THROW 50040, N'IA month already closed (bflag_IA=1)', 1;

-- Prior month closed (Sep on 802). Period-0 is a separate Keep, not "prior month".
-- 上一期间没有行（账套第一个年度的 1 月）不拦；有行且未结账才拒绝。
IF EXISTS (
  SELECT 1 FROM GL_mend WHERE iyear=@py AND iperiod=@pm AND ISNULL(bflag_IA,0)=0)
BEGIN
  SET @msg = N'prior IA month not closed: ' + CONVERT(nvarchar(10),@py) + N'-' + CONVERT(nvarchar(10),@pm);
  THROW 50041, @msg, 1;
END


-- Unposted costing stock lines in the month window (bAccByVerDate → ISNULL(dVeriDate,dDate)).
IF OBJECT_ID(N'tempdb..#unposted') IS NOT NULL DROP TABLE #unposted;
CREATE TABLE #unposted(cv nvarchar(4), id int, autoid int, handler nvarchar(40));

INSERT INTO #unposted
SELECT N'01', a.id, b.autoid, a.cHandler
FROM rdrecord01 a INNER JOIN rdrecords01 b ON a.id=b.id
INNER JOIN Inventory i ON i.cInvCode=b.cInvCode
WHERE ISNULL(b.cbaccounter,N'')=N'' AND ISNULL(b.bCosting,1)=1 AND ISNULL(i.bService,0)=0
  AND ISNULL(a.dVeriDate,a.dDate)>=@dS AND ISNULL(a.dVeriDate,a.dDate)<@dNext
  AND (ISNULL(a.biafirst,0)=1 OR (ISNULL(a.bomfirst,0)<>1 AND ISNULL(a.bpufirst,0)<>1 AND ISNULL(a.bisstqc,0)<>1));
INSERT INTO #unposted
SELECT N'08', a.id, b.autoid, a.cHandler
FROM rdrecord08 a INNER JOIN rdrecords08 b ON a.id=b.id
INNER JOIN Inventory i ON i.cInvCode=b.cInvCode
WHERE ISNULL(b.cbaccounter,N'')=N'' AND ISNULL(b.bCosting,1)=1 AND ISNULL(i.bService,0)=0
  AND ISNULL(a.dVeriDate,a.dDate)>=@dS AND ISNULL(a.dVeriDate,a.dDate)<@dNext;
INSERT INTO #unposted
SELECT N'09', a.id, b.autoid, a.cHandler
FROM rdrecord09 a INNER JOIN rdrecords09 b ON a.id=b.id
INNER JOIN Inventory i ON i.cInvCode=b.cInvCode
WHERE ISNULL(b.cbaccounter,N'')=N'' AND ISNULL(b.bCosting,1)=1 AND ISNULL(i.bService,0)=0
  AND ISNULL(a.dVeriDate,a.dDate)>=@dS AND ISNULL(a.dVeriDate,a.dDate)<@dNext;
INSERT INTO #unposted
SELECT N'10', a.id, b.autoid, a.cHandler
FROM rdrecord10 a INNER JOIN rdrecords10 b ON a.id=b.id
INNER JOIN Inventory i ON i.cInvCode=b.cInvCode
WHERE ISNULL(b.cbaccounter,N'')=N'' AND ISNULL(b.bCosting,1)=1 AND ISNULL(i.bService,0)=0
  AND ISNULL(a.dVeriDate,a.dDate)>=@dS AND ISNULL(a.dVeriDate,a.dDate)<@dNext;
INSERT INTO #unposted
SELECT N'11', a.id, b.autoid, a.cHandler
FROM rdrecord11 a INNER JOIN rdrecords11 b ON a.id=b.id
INNER JOIN Inventory i ON i.cInvCode=b.cInvCode
WHERE ISNULL(b.cbaccounter,N'')=N'' AND ISNULL(b.bCosting,1)=1 AND ISNULL(i.bService,0)=0
  AND ISNULL(a.dVeriDate,a.dDate)>=@dS AND ISNULL(a.dVeriDate,a.dDate)<@dNext;
INSERT INTO #unposted
SELECT N'32', a.id, b.autoid, a.cHandler
FROM rdrecord32 a INNER JOIN rdrecords32 b ON a.id=b.id
INNER JOIN Inventory i ON i.cInvCode=b.cInvCode
WHERE ISNULL(b.cbaccounter,N'')=N'' AND ISNULL(b.bCosting,1)=1 AND ISNULL(i.bService,0)=0
  AND ISNULL(a.dVeriDate,a.dDate)>=@dS AND ISNULL(a.dVeriDate,a.dDate)<@dNext;

SELECT @n = COUNT(*) FROM #unposted WHERE ISNULL(handler,N'')<>N'';
IF @n > 0
BEGIN
  SET @msg = N'unposted verified costing lines: ' + CONVERT(nvarchar(20),@n);
  THROW 50043, @msg, 1;
END

IF @bChk IN (N'True',N'TRUE',N'true',N'1')
BEGIN
  SELECT @n = COUNT(*) FROM #unposted WHERE ISNULL(handler,N'')=N'';
  IF @n > 0
  BEGIN
    SET @msg = N'bcheckedpribooking: unaudited costing lines in month: ' + CONVERT(nvarchar(20),@n);
    THROW 50044, @msg, 1;
  END
END

-- All month IA_Summary keys ended (iPeriod=1). Empty month is allowed (flag-only close).
IF EXISTS (
  SELECT 1 FROM IA_Summary
  WHERE iYear=@y AND iMonth=@m AND ISNULL(iDirect,0)=0 AND ISNULL(iPeriod,0)<>1)
  THROW 50045, N'IA_Summary keys with iPeriod<>1 — run ia/period_end first', 1;

EXEC IA_Close
  @y, @m, @dSDate, @WhStyle, @iQuanPrec, @CostPrec,
  @cAccounter, @bBusiness, @bDif, @bRed, 0;

UPDATE GL_mend SET bflag_IA=1 WHERE iyear=@y AND iperiod=@m;
IF @@ROWCOUNT=0
  THROW 50046, N'GL_mend row missing for this year/period — bflag_IA not set', 1;

SELECT N'bflag_IA' k, CONVERT(int, bflag_IA) n FROM GL_mend WHERE iyear=@y AND iperiod=@m
UNION ALL SELECT N'summary_M', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'summary_Mplus1', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m+1
UNION ALL SELECT N'type24_next', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m+1 AND cVouType=N'24';
