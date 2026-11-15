namespace U8Co
{
    // qm_indicators 的 SQL：检验单（QM03 来料、QM04 产品）的表体逐行读出，统计在 C# 里算（ReportsQmStat）。
    // 指标名取检验指标档案 QMCHECKGUIDE；计量单位取表体上的，空时用档案上的。
    // {FILTER}：存货、分类、供应商、指标、审核状态、数据权限（ReportsQmFilter）。
    // 参数：单据类型 | 开始日期 | 结束日期（含当日）| 过滤…
    internal static class ReportsQmIndicatorsSql
    {
        internal const string Lines = "SELECT h.CINVCODE inv, i.cInvName inv_name, s.CCHKGUIDECODE guide,"
            + " g.CCHKGUIDENAME guide_name, ISNULL(NULLIF(s.CGUIDEUNIT, N''), g.CGUIDEUNIT) guide_unit,"
            + " s.CSTANDARD std, s.CCHECKVALUE val, s.CTARGETQJUG jug, h.DDATE dt, h.CCHECKCODE code, h.CBATCH batch"
            + " FROM QMCHECKVOUCHER h JOIN QMCHECKVOUCHERS s ON s.ID=h.ID"
            + " LEFT JOIN Inventory i ON i.cInvCode=h.CINVCODE"
            + " LEFT JOIN QMCHECKGUIDE g ON g.CCHKGUIDECODE=s.CCHKGUIDECODE"
            + " WHERE h.CVOUCHTYPE=? AND h.DDATE>=CONVERT(date, ?, 23) AND h.DDATE<DATEADD(day, 1, CONVERT(date, ?, 23))"
            + "{FILTER} ORDER BY h.DDATE, h.CCHECKCODE, s.AUTOID";
    }
}
