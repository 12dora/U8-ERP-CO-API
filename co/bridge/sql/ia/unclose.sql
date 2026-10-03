-- U8 存货核算 取消月末结账。由桥在请求连接、请求事务里整批执行（不分 GO）；参数取自调用方先建好的 #ia_args
-- （y、m、keep_date、accounter、on_uncosted）。拒绝时 THROW 50000–50099，桥按编号转成 409。
-- 依据：测试账套实测（回滚事务），见 docs/api-reference.md「存货核算记账与期末处理」。
-- 反结账: IA_UnClose on M+1 (next-month shell) then unset bflag_IA for M.
-- IA_UnClose DELETEs IA_Summary/IA_Subsidiary for (@iYear,@iMonth) — UnClose(M) would destroy 期末处理 rows.
-- IA_Close writes shell as iMonth=@intMonth+1 same year (Dec → 13). INFERRED: UnClose(@y, @m+1).
SET NOCOUNT ON;

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @m tinyint; SELECT @m = m FROM #ia_args;
DECLARE @dSDate datetime;
DECLARE @iQuanPrec tinyint, @CostPrec tinyint;
DECLARE @nm int;

SET @nm = @m + 1; -- same calendar year; Dec close shell is iMonth=13
SET @dSDate = DATEADD(month, 1, DATEFROMPARTS(@y, @m, 1)); -- first of M+1; valuationass dKeepDate>=@dSDate
SELECT @iQuanPrec = CONVERT(tinyint, cValue) FROM AccInformation WHERE cSysID=N'AA' AND cName=N'iStrsQuanDecDgt';
SELECT @CostPrec  = CONVERT(tinyint, cValue) FROM AccInformation WHERE cSysID=N'AA' AND cName=N'iStrsPriDecDgt';
SET @iQuanPrec = ISNULL(@iQuanPrec, 2);
SET @CostPrec  = ISNULL(@CostPrec, 6);

IF NOT EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=@m AND ISNULL(bflag_IA,0)=1)
  THROW 50050, N'IA month is not closed (bflag_IA=0) — nothing to unclose', 1;

-- Next calendar month already closed? Refuse (would leave a hole). INFERRED.
IF @m < 12 AND EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=@m+1 AND ISNULL(bflag_IA,0)=1)
  THROW 50051, N'next IA month is closed — unclose that month first', 1;

EXEC IA_UnClose @y, @nm, @dSDate, @iQuanPrec, @CostPrec, 0;

UPDATE GL_mend SET bflag_IA=0 WHERE iyear=@y AND iperiod=@m;
IF @@ROWCOUNT=0
  THROW 50052, N'GL_mend row missing for this year/period', 1;

SELECT N'bflag_IA_M' k, CONVERT(int, bflag_IA) n FROM GL_mend WHERE iyear=@y AND iperiod=@m
UNION ALL SELECT N'summary_M', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'summary_Mplus1', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@nm
UNION ALL SELECT N'subsidiary_M', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m
UNION ALL SELECT N'subsidiary_Mplus1', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@nm
UNION ALL SELECT N'summary_iPeriod1_M', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iPeriod,0)=1;
