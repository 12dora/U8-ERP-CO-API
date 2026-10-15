using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 到期票据 notes_due 的 SQL（AP_Note，cFlag 分 AR / AP）。口径见 ReportsFinNotes。
    // 金额取本币列（iAmount_Local、iRAmount_Local）。票据类型取结算方式档案 SettleStyle 的名称（cSettleCode，期初票据没有）。
    // 最近一次处理取 AP_Note_Sub 按处理日期、主键倒序的第一行（cLink 关联）。表名、列名全是常量，其余只进 ? 参数。
    internal static class ReportsFinNotesSql
    {
        // 参数：cFlag | 条件参数… | limit。{PARTNER} 是往来单位档案 join（客户或供应商），{NAME} 是它的名称列。
        const string ListSql = "SELECT TOP (?) h.cVouchID code, h.cSettleCode settle_code, ss.cSSName note_type,"
            + " h.cDwCode partner_code, COALESCE(NULLIF({NAME}, N''), h.cDWName) partner_name,"
            + " CONVERT(char(10), h.dSignDate, 23) sign_date, CONVERT(char(10), h.dExpireDate, 23) expire_date,"
            + " ISNULL(h.iAmount_Local, 0) amount, ISNULL(h.iRAmount_Local, 0) remainder, x.cProcStyle last_proc"
            + " FROM AP_Note h LEFT JOIN SettleStyle ss ON ss.cSSCode=h.cSettleCode {PARTNER}"
            + " OUTER APPLY (SELECT TOP 1 s.cProcStyle FROM AP_Note_Sub s WHERE s.cLink=h.cLink"
            + " ORDER BY s.dDate DESC, s.ID DESC) x {WHERE} ORDER BY h.dExpireDate, h.cVouchID";

        // 合计：同一套条件。参数：cFlag | 条件参数…。
        const string TotalSql = "SELECT COUNT(*) cnt, SUM(ISNULL(h.iAmount_Local, 0)) amount,"
            + " SUM(ISNULL(h.iRAmount_Local, 0)) remainder FROM AP_Note h {WHERE}";

        // 列表查询。ps 按 ? 出现的顺序装好：先是 TOP 行数，然后是条件。
        internal static string List(WorkContext ctx, FinNotesArgs q, string from, string to, int top, List<object> ps)
        {
            ps.Add(top);
            string where = Where(ctx, q, from, to, ps);
            bool ar = q.Side == "ar";
            return ListSql.Replace("{NAME}", ar ? "pc.cCusName" : "pc.cVenName")
                .Replace("{PARTNER}", ar ? "LEFT JOIN Customer pc ON pc.cCusCode=h.cDwCode"
                    : "LEFT JOIN Vendor pc ON pc.cVenCode=h.cDwCode")
                .Replace("{WHERE}", where);
        }

        internal static string Total(WorkContext ctx, FinNotesArgs q, string from, string to, List<object> ps)
        {
            return TotalSql.Replace("{WHERE}", Where(ctx, q, from, to, ps));
        }

        // WHERE：往来类别、到期日窗口（含两端），open_only 时余额不为 0，往来单位条件，数据权限在最后。
        // 参数：cFlag、起、止、编码…。
        static string Where(WorkContext ctx, FinNotesArgs q, string from, string to, List<object> ps)
        {
            StringBuilder sb = new StringBuilder("WHERE h.cFlag=?");
            ps.Add(q.Side == "ar" ? "AR" : "AP");
            sb.Append(" AND h.dExpireDate>=CONVERT(date, ?, 23) AND h.dExpireDate<DATEADD(day, 1, CONVERT(date, ?, 23))");
            ps.Add(from);
            ps.Add(to);
            if (q.OpenOnly)
            {
                sb.Append(" AND ISNULL(h.iRAmount_Local, 0)<>0");
            }
            if (q.Partners.Length > 0)
            {
                sb.Append(" AND h.cDwCode IN (");
                for (int i = 0; i < q.Partners.Length; i++)
                {
                    sb.Append(i == 0 ? "?" : ", ?");
                    ps.Add(q.Partners[i]);
                }
                sb.Append(")");
            }
            // 数据权限：往来单位（客户或供应商，可空）。
            PermHook.Where(sb, ps, ctx, "h");
            return sb.ToString();
        }
    }
}
