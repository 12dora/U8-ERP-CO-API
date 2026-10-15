using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 收付款汇总 receipts_payments 的 SQL（Ap_CloseBill）。口径见 ReportsFinRecPay。
    // 表名、列名、别名全是常量；编码、日期和单据类型只进 ? 参数。
    internal static class ReportsFinRecPaySql
    {
        // 收款单 48 / 付款单 49 是主单据，反向的 49 / 48 是退款。
        const string AmountCase = "SUM(CASE WHEN h.cVouchType=? THEN ISNULL(h.iAmount, 0) ELSE 0 END)";
        const string Verified = " AND NULLIF(LTRIM(RTRIM(h.cCheckMan)), N'') IS NOT NULL";

        // 参数：主类型, 退款类型 | 条件参数… | limit+1。{SEL}、{GRP} 是分组键，{CUT} 是截断排序（收款 / 付款合计从大到小），
        // {COLS}、{JOINS} 是名称，{ORD} 是输出排序。先按金额取前 limit+1 组，再给保留下来的组排输出顺序，大的往来单位不会被截掉。
        const string GroupSql = ";WITH g AS (SELECT {SEL}COUNT(*) docs, " + AmountCase + " amt, " + AmountCase + " refund"
            + " FROM Ap_CloseBill h {WHERE} GROUP BY {GRP}),"
            + " kept AS (SELECT TOP (?) g.* FROM g ORDER BY {CUT})"
            + " SELECT {COLS}g.docs, g.amt, g.refund FROM kept g{JOINS} ORDER BY {ORD}";

        // 合计：同一套条件，不分组。参数：主类型, 退款类型 | 条件参数…。
        const string TotalSql = "SELECT COUNT(*) docs, " + AmountCase + " amt, " + AmountCase + " refund"
            + " FROM Ap_CloseBill h {WHERE}";

        // 主单据类型：应收收款 48、应付付款 49。
        internal static string MainType(string side)
        {
            return side == "ar" ? "48" : "49";
        }

        internal static string RefundType(string side)
        {
            return side == "ar" ? "49" : "48";
        }

        // 分组查询。ps 按 SQL 里 ? 出现的顺序装好（含最后的 TOP 行数）。
        internal static string Group(WorkContext ctx, FinRecPayArgs q, string to, int top, List<object> ps)
        {
            ps.Add(MainType(q.Side));
            ps.Add(RefundType(q.Side));
            string where = Where(ctx, q, to, ps);
            ps.Add(top);
            return GroupSql.Replace("{SEL}", Keys(q.GroupBy, "{0} k_{1}, ")).Replace("{WHERE}", where)
                .Replace("{GRP}", Keys(q.GroupBy, "{0}", ", ")).Replace("{CUT}", Cut(q))
                .Replace("{COLS}", Names(q)).Replace("{JOINS}", Joins(q)).Replace("{ORD}", Order(q));
        }

        // 合计查询。
        internal static string Total(WorkContext ctx, FinRecPayArgs q, string to, List<object> ps)
        {
            ps.Add(MainType(q.Side));
            ps.Add(RefundType(q.Side));
            return TotalSql.Replace("{WHERE}", Where(ctx, q, to, ps));
        }

        // WHERE：往来类别、单据类型、期初单据、日期窗口，然后是审核状态、请求条件和数据权限（最后）。
        // 参数：cFlag、两个类型、起、止。
        static string Where(WorkContext ctx, FinRecPayArgs q, string to, List<object> ps)
        {
            StringBuilder sb = new StringBuilder("WHERE h.cFlag=? AND h.cVouchType IN (?, ?) AND ISNULL(h.bStartFlag, 0)=0");
            ps.Add(q.Side == "ar" ? "AR" : "AP");
            ps.Add(MainType(q.Side));
            ps.Add(RefundType(q.Side));
            sb.Append(" AND h.dVouchDate>=CONVERT(date, ?, 23) AND h.dVouchDate<DATEADD(day, 1, CONVERT(date, ?, 23))");
            ps.Add(q.DateFrom);
            ps.Add(to);
            if (!q.Unverified)
            {
                sb.Append(Verified);
            }
            In(sb, ps, "h.cDwCode", q.Partners);
            In(sb, ps, "h.cPerson", q.Persons);
            In(sb, ps, "h.cDeptCode", q.Depts);
            // 数据权限：往来单位（客户或供应商）、部门、业务员。
            PermHook.Where(sb, ps, ctx, "h");
            return sb.ToString();
        }

        static void In(StringBuilder sb, List<object> ps, string column, string[] codes)
        {
            if (codes.Length == 0)
            {
                return;
            }
            sb.Append(" AND ").Append(column).Append(" IN (");
            for (int i = 0; i < codes.Length; i++)
            {
                sb.Append(i == 0 ? "?" : ", ?");
                ps.Add(codes[i]);
            }
            sb.Append(")");
        }

        // 维度 → 分组表达式（k_ 列放在 CTE 里）。
        static string Expr(string dim)
        {
            switch (dim)
            {
                case "day":
                    return "CONVERT(char(10), h.dVouchDate, 23)";
                case "month":
                    return "CONVERT(char(7), h.dVouchDate, 23)";
                case "partner":
                    return "ISNULL(h.cDwCode, N'')";
                case "person":
                    return "ISNULL(h.cPerson, N'')";
                case "department":
                    return "ISNULL(h.cDeptCode, N'')";
                default:
                    return "ISNULL(h.cSSCode, N'')";
            }
        }

        // 把每个维度按模板展开：{0} 是表达式，{1} 是维度名；glue 是各段之间的连接。
        static string Keys(string[] dims, string template)
        {
            return Keys(dims, template, "");
        }

        static string Keys(string[] dims, string template, string glue)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < dims.Length; i++)
            {
                sb.Append(i == 0 ? "" : glue).Append(string.Format(template, Expr(dims[i]), dims[i]));
            }
            return sb.ToString();
        }

        // 外层 SELECT 的键列和名称列。
        static string Names(FinRecPayArgs q)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < q.GroupBy.Length; i++)
            {
                string dim = q.GroupBy[i];
                sb.Append("g.k_").Append(dim).Append(", ");
                string name = NameExpr(dim, q.Side);
                if (name != null)
                {
                    sb.Append(name).Append(" n_").Append(dim).Append(", ");
                }
            }
            return sb.ToString();
        }

        static string NameExpr(string dim, string side)
        {
            switch (dim)
            {
                case "partner":
                    return side == "ar" ? "pc.cCusName" : "pc.cVenName";
                case "person":
                    return "pp.cPersonName";
                case "department":
                    return "dd.cDepName";
                case "settle":
                    return "ss.cSSName";
                default:
                    return null;
            }
        }

        static string Joins(FinRecPayArgs q)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < q.GroupBy.Length; i++)
            {
                switch (q.GroupBy[i])
                {
                    case "partner":
                        sb.Append(q.Side == "ar" ? " LEFT JOIN Customer pc ON pc.cCusCode=g.k_partner"
                            : " LEFT JOIN Vendor pc ON pc.cVenCode=g.k_partner");
                        break;
                    case "person":
                        sb.Append(" LEFT JOIN Person pp ON pp.cPersonCode=g.k_person");
                        break;
                    case "department":
                        sb.Append(" LEFT JOIN Department dd ON dd.cDepCode=g.k_department");
                        break;
                    case "settle":
                        sb.Append(" LEFT JOIN SettleStyle ss ON ss.cSSCode=g.k_settle");
                        break;
                }
            }
            return sb.ToString();
        }

        // 截断顺序：收款 / 付款合计从大到小，同额按维度键，结果稳定。
        static string Cut(FinRecPayArgs q)
        {
            return (q.NoAmountOrder ? "g.docs DESC, " : "g.amt DESC, ") + KeyList(q.GroupBy, KeyAll);
        }

        // 输出顺序：带日、月维度时先按日期（按请求里的顺序），再按金额从大到小，最后是其余维度键；不带时就是金额从大到小。
        // 没有维度时不会走到这里（调用方不发分组查询）。
        // 字段权限会遮金额时，金额换成单据数（g.docs），行的取舍和先后不泄露金额。
        static string Order(FinRecPayArgs q)
        {
            string time = KeyList(q.GroupBy, KeyTime);
            string rest = KeyList(q.GroupBy, KeyOther);
            return (time.Length > 0 ? time + ", " : "") + (q.NoAmountOrder ? "g.docs DESC" : "g.amt DESC")
                + (rest.Length > 0 ? ", " + rest : "");
        }

        const int KeyAll = 0;
        const int KeyTime = 1;
        const int KeyOther = 2;

        // 维度键列表：kind 取全部、只取日 / 月、只取其余。
        static string KeyList(string[] dims, int kind)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < dims.Length; i++)
            {
                bool time = dims[i] == "day" || dims[i] == "month";
                if ((kind == KeyTime && !time) || (kind == KeyOther && time))
                {
                    continue;
                }
                sb.Append(sb.Length == 0 ? "" : ", ").Append("g.k_").Append(dims[i]);
            }
            return sb.ToString();
        }
    }
}
