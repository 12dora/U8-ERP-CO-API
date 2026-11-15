namespace U8Co
{
    // 月末结账单据检查里「FROM 之后」的部分（表、条件、分组）。结账检查（PeriodCloseChecks，取一行就够）
    // 和月末结账检查报表（ReportsAcctCheckSql，数个数、取示例单号）共用同一份条件，两边不会各改各的。
    // 全是常量，调用方的值只进 ? 参数。期间按自然月算：[本月 1 日, 下月 1 日)。
    internal static class PeriodCloseSql
    {
        // 采购发票：日期在本月及以前、采购复核人为空。参数：下月 1 日。
        internal const string PuInvoiceFrom = "PurBillVouch WHERE ISNULL(cVerifier,N'')=N'' AND dPBVDate<CONVERT(date, ?, 23)";

        // 发货单（退货单）：本月、审核人为空。参数：本月 1 日、下月 1 日。
        internal const string DispatchFrom = "DispatchList WHERE ISNULL(cVerifier,N'')=N'' AND dDate>=CONVERT(date, ?, 23)"
            + " AND dDate<CONVERT(date, ?, 23)";

        // 销售发票：本月、复核人为空。参数：本月 1 日、下月 1 日。
        internal const string SaleInvoiceFrom = "SaleBillVouch WHERE ISNULL(cChecker,N'')=N'' AND dDate>=CONVERT(date, ?, 23)"
            + " AND dDate<CONVERT(date, ?, 23)";

        // 总账未记账凭证（未作废）的条件，不含表名。参数：年度、期间。
        internal const string UnpostedWhere = "iyear=? AND iperiod=? AND ISNULL(ibook,0)=0 AND (iflag IS NULL OR iflag<>1)";

        // 损益类末级科目本期借贷不平（未作废的凭证）。参数：年度、期间。
        internal const string ProfitFrom = "GL_accvouch v JOIN code c ON c.iyear=v.iyear AND c.ccode=v.ccode"
            + " WHERE v.iyear=? AND v.iperiod=? AND c.bend=1 AND c.cclass=N'损益' AND (v.iflag IS NULL OR v.iflag<>1)"
            + " GROUP BY c.ccode HAVING ROUND(SUM(ISNULL(v.md,0)) - SUM(ISNULL(v.mc,0)), 2)<>0";

        // 本期已有期间损益结转凭证（未作废）。参数：年度、期间。
        internal const string CarryFrom = "GL_accvouch WHERE iyear=? AND iperiod=? AND coutsign=N'期间损益'"
            + " AND (iflag IS NULL OR iflag<>1)";

        // 应收、应付：本月审核登记的原始往来明细（cProcStyle=cVouchType）还没制单的条件，表名是 Ar_Detail / Ap_Detail。
        // 参数：期间、年度。
        internal const string UnvouchedWhere = "iPeriod=? AND YEAR(dRegDate)=? AND cProcStyle=cVouchType AND ISNULL(cPZid,N'')=N''";
    }
}
