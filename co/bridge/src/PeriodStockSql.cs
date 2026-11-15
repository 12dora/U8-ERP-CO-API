using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 库存月末结账的 SQL：与 U8 V18 库存月末结账生成的月结快照一致（已在测试账套逐表核对）。
    // 不要「简化」这里的过滤、符号、小数位、上月结转、分组：每一处都按 U8 月结的结果逐表核对过。
    // 调用方的值只经 #st_args（带参数的一句写入），其余各段是不带参数的批处理（直接执行，#临时表留在本连接上，见 UnwriteoffSql.Run）。
    // 每段开头从 #st_args 取 @y、@m、@py、@pm（上月）、@start、@end（月末那一天）、小数位和选项。
    internal static class PeriodStockSql
    {
        // U8 的 $rdrecord$：采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库、期初。
        internal static readonly string[] Types = new string[] { "01", "08", "09", "10", "11", "32", "34" };

        // 五张快照表，删除顺序同 U8。
        internal static readonly string[] Tables = new string[]
        {
            "ST_MonthAccounts", "ST_MonthAccount", "ST_MonthAccountVs", "ST_MonthAccountV", "ST_MonthAccountCheck"
        };

        static readonly string[] Temps = new string[]
        {
            "#st_args", "#src", "#RdMX", "#RdSums", "#RdSum", "#expA", "#expAs", "#expV", "#expVs", "#Ck", "#expCk"
        };

        internal const string ArgsTable = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#st_args') IS NOT NULL DROP TABLE #st_args;"
            + " CREATE TABLE #st_args (y int, m int, py int, pm int, dstart datetime, dend datetime, qdec int, ndec int,"
            + " useItem bit, useDep bit, keepZero bit); SET NOCOUNT OFF;";

        // 参数：年、月、上月的年、上月、月初、月末（yyyy-MM-dd）、数量小数位、件数小数位。
        // ST_DefineSet 的 item / cdepcode 打开时 PeriodCloseChecks 已拒绝；这里照 U8 原样取值，结果恒为 0。
        internal const string ArgsFill = "INSERT INTO #st_args (y,m,py,pm,dstart,dend,qdec,ndec,useItem,useDep,keepZero)"
            + " SELECT ?, ?, ?, ?, CONVERT(datetime, CONVERT(date, ?, 23)), CONVERT(datetime, CONVERT(date, ?, 23)), ?, ?,"
            + " CASE WHEN EXISTS (SELECT 1 FROM ST_DefineSet WHERE citemname=N'item' AND ISNULL(bSel,N'')=N'Y') THEN 1 ELSE 0 END,"
            + " CASE WHEN EXISTS (SELECT 1 FROM ST_DefineSet WHERE citemname=N'cdepcode' AND ISNULL(bSel,N'')=N'Y') THEN 1 ELSE 0 END,"
            + " CASE WHEN EXISTS (SELECT 1 FROM AccInformation WHERE cSysID=N'ST' AND cName=N'bKeepBlankMonthData'"
            + " AND cValue IN (N'True',N'true',N'1')) THEN 1 ELSE 0 END";

        // 存货数量小数位、件数小数位（U8 的 QuanDecDgt / NumDecDgt）；读不到按 2 / 4。
        internal const string DigitsSql = "SELECT cName, cValue FROM AccInformation WHERE cSysID=N'AA'"
            + " AND cName IN (N'iStrsQuanDecDgt',N'iNumDecDgt')";

        const string Head = "SET NOCOUNT ON; DECLARE @y int, @m int, @py int, @pm int, @start datetime, @end datetime,"
            + " @qdec int, @ndec int, @useItem bit, @useDep bit, @keepZero bit;"
            + " SELECT @y=y, @m=m, @py=py, @pm=pm, @start=dstart, @end=dend, @qdec=qdec, @ndec=ndec,"
            + " @useItem=useItem, @useDep=useDep, @keepZero=keepZero FROM #st_args; ";
        const string Tail = " SET NOCOUNT OFF;";

        const string Free = "cFree1,cFree2,cFree3,cFree4,cFree5,cFree6,cFree7,cFree8,cFree9,cFree10";
        const string FreeNull = "ISNULL(cFree1,N''),ISNULL(cFree2,N''),ISNULL(cFree3,N''),ISNULL(cFree4,N''),ISNULL(cFree5,N''),"
            + "ISNULL(cFree6,N''),ISNULL(cFree7,N''),ISNULL(cFree8,N''),ISNULL(cFree9,N''),ISNULL(cFree10,N'')";
        const string Mass = "dVDate,dmdate,iMassDate,cMassUnit,iExpiratDateCalcu,cExpirationdate,dExpirationdate";
        const string Sign = "CASE WHEN brdflag=1 THEN 1.00 ELSE -1.00 END*";
        const string Dep = "CASE WHEN @useDep=1 THEN ISNULL(cDepCode,N'') ELSE NULL END";
        const string Rd = "CASE WHEN @useItem=1 THEN crdcode ELSE NULL END, CASE WHEN @useItem=1 THEN cbustype ELSE NULL END";

        // 单据明细的承载表（U8 的 #RdMX 源），列序与下面的 INSERT 一致。
        internal const string SrcTable = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#src') IS NOT NULL DROP TABLE #src;"
            + " SELECT CONVERT(tinyint,NULL) brdflag, CONVERT(nvarchar(2),NULL) cvouchtype, CONVERT(nvarchar(12),NULL) cbustype,"
            + " CONVERT(nvarchar(10),NULL) crdcode, CONVERT(nvarchar(10),NULL) cWhCode, CONVERT(nvarchar(12),NULL) cDepCode,"
            + " CONVERT(datetime,NULL) dDate, CONVERT(datetime,NULL) dVeriDate, CONVERT(nvarchar(20),NULL) cHandler,"
            + " CONVERT(bit,NULL) bisstqc, CONVERT(decimal(38,10),NULL) iNum, CONVERT(decimal(38,10),NULL) iQuantity,"
            + " CONVERT(nvarchar(60),NULL) cInvCode,"
            + " CONVERT(nvarchar(20),NULL) cFree1, CONVERT(nvarchar(20),NULL) cFree2, CONVERT(nvarchar(20),NULL) cFree3,"
            + " CONVERT(nvarchar(20),NULL) cFree4, CONVERT(nvarchar(20),NULL) cFree5, CONVERT(nvarchar(20),NULL) cFree6,"
            + " CONVERT(nvarchar(20),NULL) cFree7, CONVERT(nvarchar(20),NULL) cFree8, CONVERT(nvarchar(20),NULL) cFree9,"
            + " CONVERT(nvarchar(20),NULL) cFree10, CONVERT(nvarchar(20),NULL) cvmivencode, CONVERT(nvarchar(20),NULL) cbvencode,"
            + " CONVERT(nvarchar(60),NULL) cBatch, CONVERT(datetime,NULL) dVDate, CONVERT(datetime,NULL) dmdate,"
            + " CONVERT(int,NULL) iMassDate, CONVERT(smallint,NULL) cMassUnit, CONVERT(smallint,NULL) iExpiratDateCalcu,"
            + " CONVERT(varchar(10),NULL) cExpirationdate, CONVERT(datetime,NULL) dExpirationdate,"
            + " CONVERT(nvarchar(2),NULL) cinvouchtype, CONVERT(bigint,NULL) cvouchcode, CONVERT(bigint,NULL) autoid,"
            + " CONVERT(int,NULL) iSoType, CONVERT(nvarchar(60),NULL) iSodid, CONVERT(bit,NULL) blpusefree,"
            + " CONVERT(datetime,NULL) dnmaketime, CONVERT(bit,NULL) btrack, CONVERT(nvarchar(2),NULL) cSRPolicy,"
            + " CONVERT(bit,NULL) bspecialorder"
            + " INTO #src FROM rdrecord01 a INNER JOIN rdrecords01 b ON a.id=b.id WHERE 1=0; SET NOCOUNT OFF;";

        // 单据日期或审核日期在本月、不是采购 / 委外 / 存货的期初；{T} 只来自 Types。
        const string SrcFillSql = "INSERT INTO #src SELECT a.bRdFlag,a.cVouchType,a.cBusType,a.cRdCode,a.cWhCode,a.cDepCode,"
            + " a.dDate,a.dVeriDate,a.cHandler,a.bIsSTQc,b.iNum,b.iQuantity,b.cInvCode,"
            + " ISNULL(b.cFree1,N''),ISNULL(b.cFree2,N''),ISNULL(b.cFree3,N''),ISNULL(b.cFree4,N''),ISNULL(b.cFree5,N''),"
            + " ISNULL(b.cFree6,N''),ISNULL(b.cFree7,N''),ISNULL(b.cFree8,N''),ISNULL(b.cFree9,N''),ISNULL(b.cFree10,N''),"
            + " ISNULL(b.cvmivencode,N''),b.cBVencode,b.cBatch,b.dVDate,b.dMadeDate,b.iMassDate,ISNULL(b.cMassUnit,0),"
            + " b.iExpiratDateCalcu,b.cExpirationdate,b.dExpirationdate,b.cinvouchtype,b.cVouchCode,b.AutoID,"
            + " b.iSoType,b.iSodid,b.bLPUseFree,a.dnmaketime,i.bTrack,i.cSRPolicy,i.bSpecialOrder"
            + " FROM rdrecord{T} a INNER JOIN rdrecords{T} b ON a.id=b.id"
            + " INNER JOIN Inventory i WITH (NOLOCK) ON i.cInvCode=b.cInvCode"
            + " WHERE (a.dDate BETWEEN @start AND @end OR a.dVeriDate BETWEEN @start AND @end)"
            + " AND ISNULL(a.bomfirst,0)=0 AND ISNULL(a.bpufirst,0)=0 AND ISNULL(a.biafirst,0)=0";
        // 材料出库排除假退料（U8 原句）。
        internal const string NotFakeReturn = " AND ISNULL(a.cbustype,N'')<>N'假退料'";

        internal const string MxTable = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#RdMX') IS NOT NULL DROP TABLE #RdMX;"
            + " SELECT * INTO #RdMX FROM #src WHERE 1=0;"
            + " ALTER TABLE #RdMX ADD iYear int, iMonth tinyint, sgn_num decimal(38,10), sgn_qty decimal(38,10),"
            + " ivnum decimal(38,10), ivqty decimal(38,10), xcrd nvarchar(10), xbus nvarchar(12),"
            + " xdep nvarchar(12), xbatch nvarchar(60), xcv nvarchar(2), xtrack bigint, xiso int, xisod nvarchar(60); SET NOCOUNT OFF;";

        const string MxCols = "iYear,iMonth,sgn_num,sgn_qty,cWhCode,xdep,cInvCode," + Free + ",cvmivencode,cbvencode,xbatch,"
            + Mass + ",xcrd,xbus,dnmaketime";

        // 字符串键列照 U8 写空串、不写 NULL：自由项、代管供应商、批号（cBatch）、供应商（cbvencode，Accounts / Vs）。
        // 本月单据（带符号：收 +1、发 -1）。{FILTER} 是 Account（单据日期）或 V（审核日期、已审核）的条件。
        const string FromSrc = "INSERT INTO #RdMX (" + MxCols + ") SELECT @y,@m, " + Sign + "iNum, " + Sign + "iQuantity,"
            + " cWhCode, " + Dep + ", cInvCode, " + Free + ",cvmivencode,cbvencode,"
            + " ISNULL(cBatch,N''), " + Mass + ", " + Rd + ", ISNULL(dnmaketime,'19000101')"
            + " FROM #src WHERE {FILTER}; ";
        // 上月结存（只取上一个月，它本身就是累计的）。{PREV} 是 ST_MonthAccounts 或 ST_MonthAccountVs。
        const string FromPrev = "INSERT INTO #RdMX (" + MxCols + ") SELECT @y,@m, iNum, iQuantity, cWhCode, " + Dep + ", cInvCode, "
            + FreeNull + ", ISNULL(cvmivencode,N''), cbvencode, ISNULL(cBatch,N''), dVDate, dmdate, iMassDate,"
            + " ISNULL(cMassUnit,0), iExpiratDateCalcu, cExpirationdate, dExpirationdate, " + Rd + ", '19000101'"
            + " FROM {PREV} WHERE iYear=@py AND iMonth=@pm; ";
        internal const string AccountFilter = "dDate BETWEEN @start AND @end";
        internal const string AuditedFilter = "dVeriDate BETWEEN @start AND @end AND ISNULL(cHandler,N'')<>N''";

        const string Sums = "IF OBJECT_ID('tempdb..#RdSums') IS NOT NULL DROP TABLE #RdSums;"
            + " SELECT iYear,iMonth, SUM(CONVERT(decimal(38,10), ROUND(sgn_num,@ndec))) inum,"
            + " SUM(CONVERT(decimal(38,10), ROUND(sgn_qty,@qdec))) iquantity,"
            + " xcrd crdcode, xbus cbustype, cWhCode, xdep cDepCode, cInvCode, " + Free + ", cvmivencode,"
            + " ISNULL(cbvencode,N'') cbvencode, xbatch cBatch,"
            + " MAX(dVDate) dVDate, MAX(dmdate) dmdate, MAX(iMassDate) iMassDate, MAX(cMassUnit) cMassUnit,"
            + " MAX(iExpiratDateCalcu) iExpiratDateCalcu, MAX(cExpirationdate) cExpirationdate, MAX(dExpirationdate) dExpirationdate,"
            + " MAX(ISNULL(dnmaketime,'19000101')) dnmaketime"
            + " INTO #RdSums FROM #RdMX"
            + " GROUP BY iYear,iMonth,xcrd,xbus,cWhCode,xdep,cInvCode," + Free + ",cvmivencode,ISNULL(cbvencode,N''),xbatch; ";
        const string Sum = "IF OBJECT_ID('tempdb..#RdSum') IS NOT NULL DROP TABLE #RdSum;"
            + " SELECT iYear,iMonth, SUM(inum) inum, SUM(iquantity) iquantity, cWhCode, cDepCode, cInvCode, " + Free + ","
            + " cvmivencode, cBatch, " + Mass
            + " INTO #RdSum FROM #RdSums"
            + " GROUP BY iYear,iMonth,cWhCode,cDepCode,cInvCode," + Free + ",cvmivencode,cBatch," + Mass + "; ";
        // 结存为 0 的行不写，除非库存选项 bKeepBlankMonthData（保留空月数据）打开。
        const string Exp = "IF OBJECT_ID('tempdb..{ONE}') IS NOT NULL DROP TABLE {ONE};"
            + " IF OBJECT_ID('tempdb..{MANY}') IS NOT NULL DROP TABLE {MANY};"
            + " SELECT * INTO {ONE} FROM #RdSum WHERE @keepZero=1 OR ISNULL(inum,0)<>0 OR ISNULL(iquantity,0)<>0;"
            + " SELECT * INTO {MANY} FROM #RdSums WHERE @keepZero=1 OR ISNULL(inum,0)<>0 OR ISNULL(iquantity,0)<>0;";

        // ST_MonthAccountCheck：按单据类型（出库跟踪入库的存货按来源单据类型 / 跟踪号），数量按单据日期、V 数量按审核日期。
        const string CheckRows = "IF OBJECT_ID('tempdb..#Ck') IS NOT NULL DROP TABLE #Ck;"
            + " SELECT @y iYear, @m iMonth,"
            + " CASE WHEN ISNULL(cHandler,N'')=N'' OR ISNULL(btrack,0)=0 THEN cvouchtype"
            + " WHEN ISNULL(cvouchcode,0)=0 THEN cvouchtype"
            + " WHEN dDate BETWEEN @start AND @end AND dVeriDate BETWEEN @start AND @end THEN ISNULL(NULLIF(cinvouchtype,N''),cvouchtype)"
            + " ELSE cvouchtype END cvouchtype,"
            + " CASE WHEN dDate BETWEEN @start AND @end THEN " + Sign + "iNum ELSE 0 END inum,"
            + " CASE WHEN dDate BETWEEN @start AND @end THEN " + Sign + "iQuantity ELSE 0 END iquantity,"
            + " CASE WHEN dVeriDate BETWEEN @start AND @end AND ISNULL(cHandler,N'')<>N'' THEN " + Sign + "iNum ELSE 0 END ivnum,"
            + " CASE WHEN dVeriDate BETWEEN @start AND @end AND ISNULL(cHandler,N'')<>N'' THEN " + Sign + "iQuantity ELSE 0 END ivquantity,"
            + " cWhCode, cInvCode, " + Free + ", cvmivencode,"
            + " ISNULL(cBatch,N'') cBatch, " + Mass + ","
            + " CASE WHEN ISNULL(btrack,0)=0 THEN CONVERT(nvarchar(2),N'')"
            + " WHEN ISNULL(cinvouchtype,N'')=N'' THEN cvouchtype ELSE cinvouchtype END cinvouchtype,"
            + " CASE WHEN ISNULL(btrack,0)=0 THEN CONVERT(bigint,NULL)"
            + " WHEN ISNULL(cvouchcode,0)=0 THEN autoid ELSE cvouchcode END itrackid,"
            + " CASE WHEN cSRPolicy=N'LP' AND bspecialorder=1 AND ISNULL(blpusefree,0)=0 THEN ISNULL(iSoType,0) ELSE 0 END isotype,"
            + " CASE WHEN cSRPolicy=N'LP' AND bspecialorder=1 AND ISNULL(blpusefree,0)=0 THEN ISNULL(iSodid,N'') ELSE N'' END isodid"
            + " INTO #Ck FROM #src; ";
        const string CheckPrev = "INSERT INTO #Ck"
            + " SELECT @y,@m, cVouchtype, iNum, iQuantity, iVNum, iVQuantity, cWhCode, cInvCode, " + FreeNull + ","
            + " ISNULL(cvmivencode,N''), ISNULL(cBatch,N''), " + Mass + ","
            + " ISNULL(cinvouchtype,N''), iTrackid, ISNULL(isotype,0), ISNULL(isodid,N'')"
            + " FROM ST_MonthAccountCheck WHERE iYear=@py AND iMonth=@pm; ";
        const string CheckSums = "IF OBJECT_ID('tempdb..#expCk') IS NOT NULL DROP TABLE #expCk;"
            + " SELECT iYear,iMonth,cvouchtype,"
            + " SUM(CONVERT(decimal(38,10), ROUND(inum,@ndec))) inum, SUM(CONVERT(decimal(38,10), ROUND(iquantity,@qdec))) iquantity,"
            + " SUM(CONVERT(decimal(38,10), ROUND(ivnum,@ndec))) ivnum, SUM(CONVERT(decimal(38,10), ROUND(ivquantity,@qdec))) ivquantity,"
            + " cWhCode,cInvCode," + Free + ",cvmivencode,cBatch,"
            + " MAX(dVDate) dVDate, MAX(dmdate) dmdate, MAX(iMassDate) iMassDate, MAX(cMassUnit) cMassUnit,"
            + " MAX(iExpiratDateCalcu) iExpiratDateCalcu, MAX(cExpirationdate) cExpirationdate, MAX(dExpirationdate) dExpirationdate,"
            + " cinvouchtype, itrackid, isotype, isodid"
            + " INTO #expCk FROM #Ck"
            + " GROUP BY iYear,iMonth,cvouchtype,cWhCode,cInvCode," + Free + ","
            + " cvmivencode,cBatch,cinvouchtype,itrackid,isotype,isodid;"
            + " DELETE FROM #expCk WHERE ISNULL(inum,0)=0 AND ISNULL(iquantity,0)=0 AND ISNULL(ivnum,0)=0"
            + " AND ISNULL(ivquantity,0)=0 AND ISNULL(itrackid,0)=0;";
        internal const string CheckPass = Head + CheckRows + CheckPrev + CheckSums + Tail;

        const string StockCols = "iYear,iMonth,iNum,iQuantity,cWhCode,cDepCode,cInvCode," + Free + ",cvmivencode,cBatch," + Mass;
        const string StockColsLow = "iYear,iMonth,inum,iquantity,cWhCode,cDepCode,cInvCode," + Free + ",cvmivencode,cBatch," + Mass;
        const string RdCols = "iYear,iMonth,iNum,iQuantity,crdcode,cbustype,cWhCode,cDepCode,cInvCode," + Free
            + ",cvmivencode,cbvencode,cBatch," + Mass;
        const string RdColsLow = "iYear,iMonth,inum,iquantity,crdcode,cbustype,cWhCode,cDepCode,cInvCode," + Free
            + ",cvmivencode,cbvencode,cBatch," + Mass;
        const string CkCols = "iYear,iMonth,cVouchtype,iNum,iQuantity,iVNum,iVQuantity,cWhCode,cInvCode," + Free
            + ",cvmivencode,cBatch," + Mass + ",cinvouchtype,iTrackid,isotype,isodid";
        const string CkColsLow = "iYear,iMonth,cvouchtype,inum,iquantity,ivnum,ivquantity,cWhCode,cInvCode," + Free
            + ",cvmivencode,cBatch," + Mass + ",cinvouchtype,itrackid,isotype,isodid";

        // 先删本月（重结时 U8 也先删），再写五张快照表。结账标志由 PeriodClose 另改。
        internal const string Write = Head
            + "DELETE FROM ST_MonthAccounts WHERE iYear=@y AND iMonth=@m;"
            + " DELETE FROM ST_MonthAccount WHERE iYear=@y AND iMonth=@m;"
            + " DELETE FROM ST_MonthAccountVs WHERE iYear=@y AND iMonth=@m;"
            + " DELETE FROM ST_MonthAccountV WHERE iYear=@y AND iMonth=@m;"
            + " DELETE FROM ST_MonthAccountCheck WHERE iYear=@y AND iMonth=@m;"
            + " INSERT INTO ST_MonthAccount (" + StockCols + ") SELECT " + StockColsLow + " FROM #expA;"
            + " INSERT INTO ST_MonthAccounts (" + RdCols + ") SELECT " + RdColsLow + " FROM #expAs;"
            + " INSERT INTO ST_MonthAccountV (" + StockCols + ") SELECT " + StockColsLow + " FROM #expV;"
            + " INSERT INTO ST_MonthAccountVs (" + RdCols + ") SELECT " + RdColsLow + " FROM #expVs;"
            + " INSERT INTO ST_MonthAccountCheck (" + CkCols + ") SELECT " + CkColsLow + " FROM #expCk;" + Tail;

        // 算出来的行数（写入前）与表里的行数（写入后）；列名 n_*，check 是保留字。
        internal const string ExpCountsSql = "SELECT (SELECT COUNT(*) FROM #expA) n_account, (SELECT COUNT(*) FROM #expAs) n_accounts,"
            + " (SELECT COUNT(*) FROM #expV) n_v, (SELECT COUNT(*) FROM #expVs) n_vs, (SELECT COUNT(*) FROM #expCk) n_check";
        // 参数：(年, 月) × 5。
        internal const string TableCountsSql = "SELECT"
            + " (SELECT COUNT(*) FROM ST_MonthAccount WHERE iYear=? AND iMonth=?) n_account,"
            + " (SELECT COUNT(*) FROM ST_MonthAccounts WHERE iYear=? AND iMonth=?) n_accounts,"
            + " (SELECT COUNT(*) FROM ST_MonthAccountV WHERE iYear=? AND iMonth=?) n_v,"
            + " (SELECT COUNT(*) FROM ST_MonthAccountVs WHERE iYear=? AND iMonth=?) n_vs,"
            + " (SELECT COUNT(*) FROM ST_MonthAccountCheck WHERE iYear=? AND iMonth=?) n_check";
        // 取消结账：删上面五张表该年该月的行（{TABLE} 只来自 Tables）。
        internal const string DeleteSql = "DELETE FROM {TABLE} WHERE iYear=? AND iMonth=?";

        // ---- 结账前检查（PeriodCloseChecks.Stock）----

        // 接口暂不支持的库存选项：月结按收发类别 / 业务类型（item）或按部门（cdepcode）汇总。
        internal const string DefineSetSql = "SELECT TOP 1 citemname FROM ST_DefineSet"
            + " WHERE citemname IN (N'item',N'cdepcode') AND ISNULL(bSel,N'')=N'Y' ORDER BY citemname";
        // 有保质期管理的存货时，U8 还要按最新入库改写快照的保质期信息，桥没有照搬。
        internal const string QualitySql = "SELECT TOP 1 'x' FROM Inventory WHERE ISNULL(bInvQuality,0)=1";

        // 未审核的单据（U8 库存结账窗体逐条查）：{ 名称, 范围, SQL }。范围 u：单据日期在月末及以前（参数：下月 1 日）；
        // m：单据日期在本月（参数：本月 1 日、下月 1 日）；a：不限日期（期初单据）。
        // 有意比 U8 严的两处：期初单不加 U8 的「单据日期早于启用日期」；材料出库的业务类型为 NULL 也算（U8 用 cBusType<>N'假退料'，NULL 不算）。
        internal static readonly string[][] Unaudited = new string[][]
        {
            new string[] { "盘点单", "u", "SELECT TOP 1 'x' FROM CheckVouch WHERE ISNULL(cAccounter,N'')=N'' AND dCVDate<CONVERT(date, ?, 23)" },
            new string[] { "调拨单", "u", "SELECT TOP 1 'x' FROM TransVouch WHERE ISNULL(cVerifyPerson,N'')=N'' AND dTVDate<CONVERT(date, ?, 23)" },
            new string[] { "组装 / 拆卸 / 形态转换单", "u",
                "SELECT TOP 1 'x' FROM AssemVouch WHERE ISNULL(cVerifyPerson,N'')=N'' AND dAVDate<CONVERT(date, ?, 23)" },
            new string[] { "货位调整单", "u", "SELECT TOP 1 'x' FROM AdjustPVouch WHERE ISNULL(chandler,N'')=N'' AND dDate<CONVERT(date, ?, 23)" },
            new string[] { "报废单", "u", "SELECT TOP 1 'x' FROM ScrapVouch WHERE ISNULL(cVerifyPerson,N'')=N'' AND dDate<CONVERT(date, ?, 23)" },
            new string[] { "库存期初单据", "a", "SELECT TOP 1 'x' FROM rdrecord34 WHERE ((cVouchType=N'34' AND biafirst<>1) OR bIsSTQc=1)"
                + " AND ISNULL(cHandler,N'')=N''" },
            new string[] { "本月采购入库单", "m", "SELECT TOP 1 'x' FROM RdRecord01 WHERE cVouchType=N'01' AND ISNULL(cHandler,N'')=N''"
                + " AND NOT ((ISNULL(bPuFirst,0)=1 OR ISNULL(bOMFirst,0)=1 OR ISNULL(bIAFirst,0)=1) AND bIsSTQc=0)" + InMonth },
            new string[] { "本月其他入库单", "m", Rd0("RdRecord08", "08") },
            new string[] { "本月其他出库单", "m", Rd0("RdRecord09", "09") },
            new string[] { "本月产成品入库单", "m", Rd0("rdrecord10", "10") },
            new string[] { "本月材料出库单（假退料除外）", "m", "SELECT TOP 1 'x' FROM rdrecord11 WHERE cVouchType=N'11'"
                + " AND ISNULL(bOMFirst,0)<>1 AND ISNULL(cBusType,N'')<>N'假退料' AND ISNULL(cHandler,N'')=N''" + InMonth },
            new string[] { "本月销售出库单", "m", Rd0("rdrecord32", "32") }
        };
        const string InMonth = " AND dDate>=CONVERT(date, ?, 23) AND dDate<CONVERT(date, ?, 23)";

        static string Rd0(string table, string type)
        {
            return "SELECT TOP 1 'x' FROM " + table + " WHERE cVouchType=N'" + type + "' AND ISNULL(cHandler,N'')=N''" + InMonth;
        }

        // 库存期初（bIsSTQc=1）的两种特殊情况：U8 的 V / Check 两趟对已审核的期初另走一支（按 bIsSTQc 拆开取数），
        // 桥照搬的算法只在审核日期与单据日期同月、日期不早于首个会计期间的期初上核对过，这两种先拒绝（PeriodCloseChecks）。
        static string QcRows()
        {
            List<string> parts = new List<string>();
            foreach (string type in Types)
            {
                parts.Add("SELECT dDate, dVeriDate, cHandler FROM rdrecord" + type + " WHERE bIsSTQc=1");
            }
            return "SELECT TOP 1 'x' FROM (" + string.Join(" UNION ALL ", parts.ToArray()) + ") q WHERE ISNULL(q.cHandler,N'')<>N''";
        }

        // 审核日期与单据日期不在同一个月、且有一个日期在本月末及以前。参数：下月 1 日 × 2。
        internal static string QcCrossMonthSql()
        {
            return QcRows() + " AND q.dVeriDate IS NOT NULL AND DATEDIFF(month, q.dDate, q.dVeriDate)<>0"
                + " AND (q.dDate<CONVERT(date, ?, 23) OR q.dVeriDate<CONVERT(date, ?, 23))";
        }

        // 单据日期早于 GL_mend 最早年度的 1 月 1 日，而本月之前还没有任何库存月结快照（首次库存结账）。参数：年*100+期。
        internal static string QcBeforeFirstSql()
        {
            return QcRows() + " AND q.dDate<(SELECT CONVERT(datetime, CONVERT(char(4), MIN(iyear)) + '0101', 112)"
                + " FROM GL_mend WHERE iperiod BETWEEN 1 AND 12)"
                + " AND NOT EXISTS (SELECT 1 FROM ST_MonthAccount WHERE iYear*100+iMonth<?)";
        }

        // 一种单据的明细进 #src。
        internal static string SrcFill(string type)
        {
            if (Array.IndexOf(Types, type) < 0)
            {
                throw new BridgeException(500, "internal", "库存单据类型无效");
            }
            string sql = SrcFillSql.Replace("{T}", type) + (type == "11" ? NotFakeReturn : "");
            return Head + sql + ";" + Tail;
        }

        // Account / Accounts（audited=false，单据日期）或 V / Vs（audited=true，审核日期、已审核）的一趟。
        internal static string Pass(bool audited)
        {
            string src = FromSrc.Replace("{FILTER}", audited ? AuditedFilter : AccountFilter);
            string prev = FromPrev.Replace("{PREV}", audited ? "ST_MonthAccountVs" : "ST_MonthAccounts");
            string exp = Exp.Replace("{ONE}", audited ? "#expV" : "#expA").Replace("{MANY}", audited ? "#expVs" : "#expAs");
            return Head + "TRUNCATE TABLE #RdMX; " + src + prev + Sums + Sum + exp + Tail;
        }

        internal static string DropTemps()
        {
            StringBuilder sb = new StringBuilder("SET NOCOUNT ON;");
            foreach (string name in Temps)
            {
                sb.Append(" IF OBJECT_ID('tempdb..").Append(name).Append("') IS NOT NULL DROP TABLE ").Append(name).Append(';');
            }
            return sb.Append(Tail).ToString();
        }

        internal static string DeleteOf(string table)
        {
            if (Array.IndexOf(Tables, table) < 0)
            {
                throw new BridgeException(500, "internal", "库存月结表名无效");
            }
            return DeleteSql.Replace("{TABLE}", table);
        }

        // TableCountsSql 的参数。
        internal static object[] CountArgs(int year, int period)
        {
            List<object> args = new List<object>(10);
            for (int i = 0; i < 5; i++)
            {
                args.Add(year);
                args.Add(period);
            }
            return args.ToArray();
        }
    }
}
