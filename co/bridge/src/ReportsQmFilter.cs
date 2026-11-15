using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 质量统计报表共用的 WHERE 条件。表头别名固定为 h（QMCHECKVOUCHER），表体别名 s（QMCHECKVOUCHERS）。
    // 调用方的值只进参数；占位符的个数只由清单长度决定。
    internal static class ReportsQmFilter
    {
        // 存货、存货分类前缀、供应商、指标、审核状态，最后是数据权限（供应商、存货）。权限条件必须放在 WHERE 最后。
        internal static string Build(WorkContext ctx, QmReportArgs q, List<object> ps)
        {
            StringBuilder sb = new StringBuilder();
            In(sb, ps, "h.CINVCODE", q.Inv);
            if (q.InvClass.Length > 0)
            {
                sb.Append(" AND h.CINVCODE IN (SELECT ic.cInvCode FROM Inventory ic WHERE ic.cInvCCode LIKE ?)");
                ps.Add(q.InvClass + "%");
            }
            In(sb, ps, "h.CVENCODE", q.Vendor);
            In(sb, ps, "s.CCHKGUIDECODE", q.Guide);
            if (!q.Unverified)
            {
                sb.Append(" AND ISNULL(h.CVERIFIER, N'')<>N''");
            }
            PermHook.Where(sb, ps, ctx, "h");
            return sb.ToString();
        }

        internal static void In(StringBuilder sb, List<object> ps, string column, string[] codes)
        {
            if (codes == null || codes.Length == 0)
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
    }
}
