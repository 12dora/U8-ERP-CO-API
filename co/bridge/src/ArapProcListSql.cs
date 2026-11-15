using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // arap/process/list 的 SQL。全部不加锁提示（读线程池、不开事务，READ COMMITTED）；表名、列名只来自这里和 WriteoffSql.Detail，
    // 调用方的值只进参数。往来明细没有 rowversion，增量按 Auto_ID（IDENTITY，聚集主键 aaaaaAr_Detail_PK / aaaaaAp_Detail_PK）。
    // 处理行 = 去掉单据本身审核（Sign）行的行，条件同取消核销的「之后的处理」（UnwriteoffSql.Blocker）；另要求处理号非空。
    internal static class ProcListSql
    {
        // 处理行的会计年度：往来明细没有年度列，登记日期可能是单据日期（如红票对冲取单据的最大日期），早于处理所在期间；
        // 期间小于登记月份说明是次年处理上年单据（1 月处理 12 月单据），年度加 1。期初行（期间 0）不加。
        internal const string Fy = "(CASE WHEN d.iPeriod>0 AND d.iPeriod<MONTH(d.dRegDate) THEN YEAR(d.dRegDate)+1"
            + " ELSE YEAR(d.dRegDate) END)";

        const string From = " FROM {D} d WHERE " + UnwriteoffSql.Blocker + " AND d.cFlag=? AND ISNULL(d.cCancelNo,N'')<>N''";

        // 明细一行。绑定顺序：TOP、flag、起点、水位、{PERM}。
        const string FullCols = "convert(varchar(20), d.Auto_ID) as id, d.cFlag as flag, d.cProcStyle as style, d.cCancelNo as code, "
            + "d.cVouchType as vouch_type, d.cVouchID as vouch_id, d.cCoVouchType as co_vouch_type, d.cCoVouchID as co_vouch_id, "
            + "d.cDwCode as partner, d.cDeptCode as dept, d.cPerson as person, convert(varchar(20), d.iBVid) as line_id, "
            + "{df} as debit_f, {cf} as credit_f, d.cPZid as pz_id, d.cGLSign as gl_sign, convert(varchar(10), d.iGLno_id) as gl_no, "
            + "convert(varchar(10), d.dRegDate, 23) as reg_date, convert(varchar(10), d.iPeriod) as period, "
            + "convert(varchar(10), " + Fy + ") as fiscal_year, convert(varchar(10), d.iFlag) as row_flag";
        const string KeyCols = "convert(varchar(20), d.Auto_ID) as id, d.cFlag as flag, d.cProcStyle as style, d.cCancelNo as code";

        // 只要未结账期间的行：会计年度（Fy）、期间在 GL_mend 上该侧未结账（{M} 是 bflag_AR / bflag_AP）。
        const string OpenCond = " AND EXISTS (SELECT 1 FROM GL_mend m WHERE m.iyear=" + Fy + " AND m.iperiod=d.iPeriod"
            + " AND ISNULL(m.{M},0)=0)";

        // 摘要一页批次。绑定顺序：TOP、flag、期间条件、{PERM}、游标。往来单位随批次一起汇总（最小、最大、去重个数，空串也计一个），
        // 超过两个的批次再按键补查（PartnerSql），不再为整页另扫一遍往来明细。
        const string DigestCols = "SELECT TOP (?) d.cProcStyle as style, d.cCancelNo as code, convert(varchar(20), MIN(d.Auto_ID)) as min_id, "
            + "convert(varchar(20), MAX(d.Auto_ID)) as max_id, MAX(ISNULL(d.cPZid,N'')) as pz, {sd} as sum_d_f, {sc} as sum_c_f, "
            + "convert(varchar(20), COUNT(*)) as row_count, convert(varchar(10), MIN(" + Fy + ")) as fiscal_year, "
            + "MIN(ISNULL(d.cDwCode,N'')) as p_min, MAX(ISNULL(d.cDwCode,N'')) as p_max, "
            + "convert(varchar(10), COUNT(DISTINCT ISNULL(d.cDwCode,N''))) as p_count";

        const string Group = " GROUP BY d.cProcStyle, d.cCancelNo ORDER BY d.cProcStyle, d.cCancelNo";

        // 水位：查询前取已提交可见的最大 Auto_ID，再取 IDENT_CURRENT（之后取，所以不小于水位）。
        const string MaxSql = "SELECT convert(varchar(20), ISNULL(MAX(Auto_ID),0)) FROM {D}";
        const string IdentSql = "SELECT convert(varchar(20), convert(bigint, IDENT_CURRENT(?)))";

        // 期间状态：1 到 12 期、年度不晚于登录年度，按年度、期间升序；c 为该侧结账标志。
        const string PeriodSql = "SELECT convert(varchar(10), iyear) as y, convert(varchar(10), iperiod) as p, "
            + "convert(varchar(1), CASE WHEN ISNULL({M},0)=0 THEN 0 ELSE 1 END) as c FROM GL_mend "
            + "WHERE iperiod BETWEEN 1 AND 12 AND iyear<=? ORDER BY iyear, iperiod";

        static string Mend(string flag)
        {
            return flag == "AP" ? "bflag_AP" : "bflag_AR";
        }

        internal static string Table(string flag)
        {
            return WriteoffSql.Detail(flag);
        }

        public static int Watermark(object conn, string flag)
        {
            return Int(Rows.Scalar(conn, MaxSql.Replace("{D}", Table(flag)), new object[0]));
        }

        // IDENT_CURRENT 读不到（权限或表名）时用水位代替。
        public static int Ident(object conn, string flag, int watermark)
        {
            string raw = Rows.Scalar(conn, IdentSql, new object[] { Table(flag) });
            int ident = Int(raw);
            return raw == null || ident < watermark ? watermark : ident;
        }

        // GL_mend 的 { 年度, 期间, 已结账 0/1 }，年度不晚于 maxYear。
        public static List<int[]> PeriodStates(object conn, string flag, int maxYear)
        {
            List<int[]> list = new List<int[]>();
            string sql = PeriodSql.Replace("{M}", Mend(flag));
            foreach (Dictionary<string, object> r in Rows.Query(conn, sql, new object[] { maxYear }, 1000))
            {
                list.Add(new int[] { Int(GlSql.Col(r, "y")), Int(GlSql.Col(r, "p")), Int(GlSql.Col(r, "c")) });
            }
            return list;
        }

        // 明细一页（多取一条判断是否有下一页）。
        public static string ListSql(WorkContext ctx, ProcListArgs a, int watermark, List<object> args)
        {
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ");
            args.Add(a.Limit + 1);
            sql.Append(a.KeysOnly ? KeyCols : FullCols.Replace("{df}", WriteoffSql.Dec("d.iDAmount_f", 2))
                .Replace("{cf}", WriteoffSql.Dec("d.iCAmount_f", 2)));
            sql.Append(From.Replace("{D}", Table(a.Flag)));
            args.Add(a.Flag);
            sql.Append(" AND d.Auto_ID>? AND d.Auto_ID<=?");
            args.Add(Math.Max(a.Since, a.After));
            args.Add(watermark);
            if (a.OpenOnly)
            {
                sql.Append(OpenCond.Replace("{M}", Mend(a.Flag)));
            }
            // 数据权限：往来单位、部门、业务员（往来明细行上的列）。
            PermHook.Where(sql, args, ctx, "d");
            sql.Append(" ORDER BY d.Auto_ID");
            return sql.ToString();
        }

        // 摘要一页批次。periods 是 { 年度, 期间 } 列表（非空）。
        public static string DigestSql(WorkContext ctx, ProcListArgs a, List<int[]> periods, List<object> args)
        {
            StringBuilder sql = new StringBuilder(DigestCols.Replace("{sd}", WriteoffSql.Dec("SUM(ISNULL(d.iDAmount_f,0))", 2))
                .Replace("{sc}", WriteoffSql.Dec("SUM(ISNULL(d.iCAmount_f,0))", 2)));
            args.Add(a.Limit + 1);
            Where(sql, args, ctx, a.Flag, periods);
            if (a.Cursor != null)
            {
                sql.Append(" AND (d.cProcStyle>? OR (d.cProcStyle=? AND d.cCancelNo>?))");
                args.Add(a.Cursor[0]);
                args.Add(a.Cursor[0]);
                args.Add(a.Cursor[1]);
            }
            sql.Append(Group);
            return sql.ToString();
        }

        // 指定批次（非空，{ 处理方式, 处理号 }）的往来单位：（处理方式、处理号、往来单位）去重。只给往来单位超过两个的批次用。
        public static string PartnerSql(WorkContext ctx, ProcListArgs a, List<int[]> periods, List<string[]> keys,
            List<object> args)
        {
            StringBuilder sql = new StringBuilder("SELECT DISTINCT d.cProcStyle as style, d.cCancelNo as code, d.cDwCode as partner");
            Where(sql, args, ctx, a.Flag, periods);
            sql.Append(" AND (");
            for (int i = 0; i < keys.Count; i++)
            {
                sql.Append(i == 0 ? "" : " OR ").Append("(d.cProcStyle=? AND d.cCancelNo=?)");
                args.Add(keys[i][0]);
                args.Add(keys[i][1]);
            }
            sql.Append(")");
            return sql.ToString();
        }

        // FROM、处理行条件、flag、期间（按年度分组：会计年度 Fy 是该年度且期间在列表里）、数据权限。登记日期范围只为走索引：
        // 年度 Y 的行登记在 Y 年内，或在 Y-1 年、月份大于期间（所以下限是 Y-1 年「最小期间 + 1」月 1 日，最小期间 12 时是 Y 年 1 月 1 日）。
        static void Where(StringBuilder sql, List<object> args, WorkContext ctx, string flag, List<int[]> periods)
        {
            sql.Append(From.Replace("{D}", Table(flag)));
            args.Add(flag);
            sql.Append(" AND (");
            int i = 0;
            bool firstYear = true;
            while (i < periods.Count)
            {
                int year = periods[i][0];
                sql.Append(firstYear ? "" : " OR ").Append("(d.dRegDate>=CONVERT(datetime, CONVERT(date, ?, 23))")
                    .Append(" AND d.dRegDate<CONVERT(datetime, CONVERT(date, ?, 23)) AND d.iPeriod IN (");
                args.Add(LowerBound(year, periods[i][1]));
                args.Add(YearStart(year + 1));
                bool firstPeriod = true;
                for (; i < periods.Count && periods[i][0] == year; i++)
                {
                    sql.Append(firstPeriod ? "?" : ",?");
                    args.Add(periods[i][1]);
                    firstPeriod = false;
                }
                sql.Append(") AND ").Append(Fy).Append("=?)");
                args.Add(year);
                firstYear = false;
            }
            sql.Append(")");
            PermHook.Where(sql, args, ctx, "d");
        }

        // 年度 year、最小期间 minPeriod（periods 升序，同年度第一项最小）的登记日期下限。
        internal static string LowerBound(int year, int minPeriod)
        {
            return minPeriod >= 12 ? YearStart(year)
                : (year - 1).ToString("0000", CultureInfo.InvariantCulture) + "-"
                    + (minPeriod + 1).ToString("00", CultureInfo.InvariantCulture) + "-01";
        }

        internal static string YearStart(int year)
        {
            return year.ToString("0000", CultureInfo.InvariantCulture) + "-01-01";
        }

        internal static int Int(string text)
        {
            int value;
            return text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                ? value : 0;
        }
    }
}
