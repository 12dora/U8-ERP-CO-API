using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // reports/mgmt/pnl 的 SQL（只读，表名列名都是常量，调用方的值只进 ? 参数）。
    // 损益发生额取 GL_accvouch（不用 GL_accsum：月末「期间损益结转」把损益科目结平之后，余额表上本期借贷相等、看不出收入成本）。
    // 去掉结转凭证：整张凭证排除——U8 自动转账生成的期间损益结转凭证（coutsign = N'期间损益'，系统写入，不随摘要改动），
    // 或者手工做的结转（凭证里有本年利润科目、其余分录全是损益类科目）。损益类按科目档案的类型 code.cclass = N'损益'
    // 判断（与请求的 pl_accounts 无关，小企业等科目体系同样适用；6001–6901 都是该类型）。
    // 这两条合起来通常与按摘要「期间损益结转」筛的凭证一致；摘要为结转、却只在两个损益科目
    // 之间互转（本年利润分录为 0 被省掉）的凭证，按摘要会漏掉——这里按 coutsign 照样排除。
    // 「结转销售成本」「结转制造费用」「汇兑损益」等是经营业务，不排除。
    // 作废凭证（iflag=1）不计；缺省只计已记账（ibook=1），include_unposted 时含未记账。
    internal static class ReportsMgmtPnlSql
    {
        internal const string TransferSign = "期间损益";
        // 科目档案 code.cclass 里损益类的取值（资产、负债、权益、成本、损益）。
        internal const string PlClass = "损益";

        // ? 的顺序：年度、起、止、本年利润前缀 ×2 | 年度、起、止、损益前缀……、数据权限（科目、按部门时的部门、按项目时的项目）| TOP、年度、
        // 部门数据权限（部门名称）。
        internal static string Query(MgmtGlArgs m, int year, List<object> ps, WorkContext ctx)
        {
            string code = m.Detail == "leaf" ? "v.ccode" : "LEFT(v.ccode, 4)";
            string dims = DimExprs(m);
            StringBuilder sql = new StringBuilder(";WITH tr AS (SELECT t.iperiod, t.csign, t.ino_id FROM GL_accvouch t");
            sql.Append(" LEFT JOIN code tc ON tc.iyear = t.iyear AND tc.ccode = t.ccode");
            sql.Append(" WHERE t.iyear = ? AND t.iperiod BETWEEN ? AND ? AND (t.iflag IS NULL OR t.iflag <> 1)");
            Add(ps, year, m.From, m.To);
            sql.Append(" GROUP BY t.iperiod, t.csign, t.ino_id HAVING MAX(CASE WHEN t.coutsign = N'").Append(TransferSign);
            sql.Append("' THEN 1 ELSE 0 END) = 1 OR (SUM(CASE WHEN t.ccode LIKE ? THEN 1 ELSE 0 END) > 0");
            ps.Add(m.ProfitAccount + "%");
            sql.Append(" AND SUM(CASE WHEN t.ccode LIKE ? OR tc.cclass = N'").Append(PlClass).Append("' THEN 0 ELSE 1 END) = 0)),");
            ps.Add(m.ProfitAccount + "%");
            sql.Append(" g AS (SELECT v.iperiod AS period, ").Append(code).Append(" AS code");
            sql.Append(DimSelect(m)).Append(", SUM(ISNULL(v.md, 0)) AS debit, SUM(ISNULL(v.mc, 0)) AS credit,");
            sql.Append(" MIN(CASE WHEN ISNULL(v.ibook, 0) = 1 THEN 1 ELSE 0 END) AS posted FROM GL_accvouch v");
            sql.Append(" WHERE v.iyear = ? AND v.iperiod BETWEEN ? AND ? AND (v.iflag IS NULL OR v.iflag <> 1)");
            Add(ps, year, m.From, m.To);
            if (!m.Unposted)
            {
                sql.Append(" AND ISNULL(v.ibook, 0) = 1");
            }
            Reports.LikeAny(sql, ps, "v.ccode", m.PlAccounts, false);
            sql.Append(" AND NOT EXISTS (SELECT 1 FROM tr WHERE tr.iperiod = v.iperiod AND tr.csign = v.csign");
            sql.Append(" AND tr.ino_id = v.ino_id)");
            // 数据权限：科目（总账选项「明细账查询权限控制到科目」打开时）。
            PermHook.Where(sql, ps, ctx, "v");
            if (m.ByDept)
            {
                DeptRows(sql, ps, ReportsMgmtCashSql.ReadPerm(ctx));
            }
            if (m.ByItem)
            {
                ItemRows(sql, ps, ReportsMgmtCashSql.ReadPerm(ctx));
            }
            sql.Append(" GROUP BY v.iperiod, ").Append(code).Append(dims).Append(")");
            sql.Append(" SELECT TOP (?) g.period, g.code, c.ccode_name AS name, CONVERT(int, ISNULL(c.bproperty, 1)) AS bproperty");
            ps.Add(ReportsMgmtGlReq.MaxRows + 1);
            sql.Append(DimOut(m)).Append(", CONVERT(decimal(18,2), g.debit) AS debit, CONVERT(decimal(18,2), g.credit) AS credit,");
            sql.Append(" g.posted FROM g LEFT JOIN code c ON c.iyear = ? AND c.ccode = g.code");
            ps.Add(year);
            if (m.ByDept)
            {
                // 部门名称按部门数据权限显示（无权部门的分录已在聚合前去掉，这里的条件只作兜底；空部门的行没有名称）。
                sql.Append(" LEFT JOIN Department d ON d.cDepCode = g.dept");
                PermContext p = ReportsMgmtCashSql.ReadPerm(ctx);
                if (p != null)
                {
                    PermSql.AppendCode(sql, ps, p, PermObj.Department, "d.cDepCode", false);
                }
            }
            sql.Append(" ORDER BY g.period, g.code").Append(DimOrder(m));
            return sql.ToString();
        }

        // 按部门（dims 含 dept）：部门数据权限打开时，聚合前去掉部门不在授权内的分录，各行和调用方自己加出的合计
        // 都只含可见部门（同 U8 统计表先按权限过滤明细再汇总）。没有部门的分录（cdept_id 为空）不属于任何部门，照常计入。
        // 不按部门时口径同科目余额表，只按科目过滤。p 为 null（自检、非读路由）时不加条件。
        internal static void DeptRows(StringBuilder sql, List<object> ps, PermContext p)
        {
            if (p != null)
            {
                PermSql.AppendCode(sql, ps, p, PermObj.Department, "v.cdept_id", true);
            }
        }

        // 按项目（dims 含 item）：项目数据权限（fitem）打开时，聚合前去掉项目（大类 + 编码）不在授权内的分录，口径同
        // DeptRows。没有项目的分录（citem_id 为空）照常计入。开关关着、p 为 null（自检、非读路由）时不加条件。
        internal static void ItemRows(StringBuilder sql, List<object> ps, PermContext p)
        {
            if (p != null)
            {
                PermSql.AppendItem(sql, ps, p, "v.citem_class", "v.citem_id", true);
            }
        }

        // 每个期间：总账是否已结账、未记账凭证张数（不含作废）、是否已做期间损益结转。? 的顺序：年度、起、止。
        internal const string PeriodsSql = "SELECT m.iperiod AS period, CONVERT(int, ISNULL(m.bflag, 0)) AS gl_closed,"
            + " (SELECT COUNT(*) FROM (SELECT DISTINCT v.csign, v.ino_id FROM GL_accvouch v WHERE v.iyear = m.iyear"
            + " AND v.iperiod = m.iperiod AND ISNULL(v.ibook, 0) = 0 AND (v.iflag IS NULL OR v.iflag <> 1)) x) AS unposted,"
            + " CASE WHEN EXISTS (SELECT 1 FROM GL_accvouch w WHERE w.iyear = m.iyear AND w.iperiod = m.iperiod"
            + " AND w.coutsign = N'" + TransferSign + "' AND (w.iflag IS NULL OR w.iflag <> 1)) THEN 1 ELSE 0 END AS transferred"
            + " FROM GL_mend m WHERE m.iyear = ? AND m.iperiod BETWEEN ? AND ? ORDER BY m.iperiod";

        static string DimExprs(MgmtGlArgs m)
        {
            string text = m.ByDept ? ", ISNULL(v.cdept_id, N'')" : "";
            return text + (m.ByItem ? ", ISNULL(v.citem_class, N''), ISNULL(v.citem_id, N'')" : "");
        }

        static string DimSelect(MgmtGlArgs m)
        {
            string text = m.ByDept ? ", ISNULL(v.cdept_id, N'') AS dept" : "";
            return text + (m.ByItem ? ", ISNULL(v.citem_class, N'') AS item_class, ISNULL(v.citem_id, N'') AS item" : "");
        }

        static string DimOut(MgmtGlArgs m)
        {
            string text = m.ByDept ? ", g.dept, d.cDepName AS dept_name" : "";
            return text + (m.ByItem ? ", g.item_class, g.item" : "");
        }

        static string DimOrder(MgmtGlArgs m)
        {
            string text = m.ByDept ? ", g.dept" : "";
            return text + (m.ByItem ? ", g.item_class, g.item" : "");
        }

        static void Add(List<object> ps, params object[] values)
        {
            ps.AddRange(values);
        }
    }
}
