namespace U8Co
{
    // qm_pass_rate 的 SQL：一张检验单表头算一单，按月份、供应商或存货分组。
    // 不合格单：结论 CCHKCONCLUSION 含「不合格」或「不接受」，或结论为空且不合格数量 FDISQUANTITY 大于 0；
    // 让步单：含「让步」且不是不合格单（按合格算）；未判定单：结论为空且没有不合格数量（不算合格也不算不合格）。
    // 数量取表头：报检 FQUANTITY、合格 FREGQUANTITY、让步 FCONQUANTIY、不合格 FDISQUANTITY。
    // {SEL}：分组列（k1、k2、k3），{JOIN}：取名称的联接，{GRP}：分组表达式，{FILTER}：存货、分类、供应商、审核状态、数据权限。
    // 参数：单据类型 | 开始日期 | 结束日期（含当日）| 过滤…
    internal static class ReportsQmPassRateSql
    {
        const string Month = "CONVERT(char(7), h.DDATE, 23)";

        // 结论按空串处理 NULL，免得 LIKE 得 NULL 后 NOT 也得 NULL 而漏计。
        const string Text = "ISNULL(h.CCHKCONCLUSION, N'')";
        // 结论为空：NULL 或只有空白。
        const string Blank = "LTRIM(RTRIM(" + Text + ")) = N''";
        const string Reject = "(" + Text + " LIKE N'%不合格%' OR " + Text + " LIKE N'%不接受%'"
            + " OR (" + Blank + " AND ISNULL(h.FDISQUANTITY, 0) > 0))";

        internal const string Main = "SELECT {SEL}, COUNT(*) docs,"
            + " SUM(CASE WHEN " + Reject + " THEN 1 ELSE 0 END) fail_docs,"
            + " SUM(CASE WHEN " + Blank + " AND NOT " + Reject + " THEN 1 ELSE 0 END) undecided_docs,"
            + " SUM(CASE WHEN " + Text + " LIKE N'%让步%' AND NOT " + Reject + " THEN 1 ELSE 0 END) con_docs,"
            + " CONVERT(decimal(28,6), SUM(ISNULL(h.FQUANTITY, 0))) qty,"
            + " CONVERT(decimal(28,6), SUM(ISNULL(h.FREGQUANTITY, 0))) reg_qty,"
            + " CONVERT(decimal(28,6), SUM(ISNULL(h.FCONQUANTIY, 0))) con_qty,"
            + " CONVERT(decimal(28,6), SUM(ISNULL(h.FDISQUANTITY, 0))) dis_qty"
            + " FROM QMCHECKVOUCHER h{JOIN}"
            + " WHERE h.CVOUCHTYPE=? AND h.DDATE>=CONVERT(date, ?, 23) AND h.DDATE<DATEADD(day, 1, CONVERT(date, ?, 23))"
            + "{FILTER} GROUP BY {GRP} ORDER BY {GRP}";

        // 按 group_by 取 { 分组列, 联接, 分组表达式 }；group_by 已在登录前校验。
        internal static string[] Parts(string groupBy)
        {
            if (groupBy == "vendor")
            {
                return new string[]
                {
                    "h.CVENCODE k1, v.cVenName k2", " LEFT JOIN Vendor v ON v.cVenCode=h.CVENCODE",
                    "h.CVENCODE, v.cVenName"
                };
            }
            if (groupBy == "inventory")
            {
                return new string[]
                {
                    "h.CINVCODE k1, i.cInvName k2, u.cComUnitName k3",
                    " LEFT JOIN Inventory i ON i.cInvCode=h.CINVCODE"
                        + " LEFT JOIN ComputationUnit u ON u.cComunitCode=i.cComUnitCode",
                    "h.CINVCODE, i.cInvName, u.cComUnitName"
                };
            }
            return new string[] { Month + " k1", "", Month };
        }

        internal static string For(string groupBy, string filter)
        {
            string[] p = Parts(groupBy);
            return Main.Replace("{SEL}", p[0]).Replace("{JOIN}", p[1]).Replace("{GRP}", p[2]).Replace("{FILTER}", filter);
        }
    }
}
