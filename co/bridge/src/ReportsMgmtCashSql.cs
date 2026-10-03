using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // reports/mgmt/cash_stock 的 SQL（只读，表名列名都是常量，调用方的值只进 ? 参数）。
    // 日期区间按会计期间（ReportsMgmtSales.Bounds：UA_Period，查不到按自然月）；起止日期只进参数，按 CONVERT 样式 23 转换。
    // 数据权限按段挂：科目（资金、应收票据科目余额）、客户（未处理应收票据）、存货与仓库（存货结存、产成品入库）、
    // 供应商（采购）。各段的受控对象不同，只追加本段用到的对象（Perm），不整条规则一起挂。
    internal static class ReportsMgmtCashSql
    {
        // 该期间各模块的结账标志（总账列是 bflag）。? 的顺序：年度、期间。
        internal const string CloseSql = "SELECT CONVERT(int, ISNULL(bflag, 0)) GL, CONVERT(int, ISNULL(bflag_IA, 0)) IA,"
            + " CONVERT(int, ISNULL(bflag_ST, 0)) ST, CONVERT(int, ISNULL(bflag_PU, 0)) PU FROM GL_mend WHERE iyear = ? AND iperiod = ?";
        // 应收票据（cFlag AR）的未处理余额：iRAmount_Local 是票据剩余的本币金额，结算、贴现、背书、退回都会减它；
        // 是查询时点的状态，不是期末数（期末数看总账应收票据科目，见 notes_receivable.total）。
        // 客户取 cDwCode，为空时取 cEndorser（同 mgmt/arap_terms 的未结票据）。
        const string NotesOpenSql = "SELECT COUNT(*) AS n, CONVERT(decimal(18,2), SUM(ISNULL(x.amt, 0))) AS amount,"
            + " CONVERT(varchar(10), MIN(x.dExpireDate), 23) AS first_due FROM (SELECT"
            + " COALESCE(NULLIF(LTRIM(RTRIM(cDwCode)), N''), NULLIF(LTRIM(RTRIM(cEndorser)), N'')) AS cCusCode,"
            + " iRAmount_Local AS amt, dExpireDate FROM Ap_Note WHERE cFlag = N'AR' AND ISNULL(iRAmount_Local, 0) <> 0) x WHERE 1 = 1";
        // 存货核算汇总表的期末结存（存货核算按仓库或按存货记账都只有一层行，直接合计）。
        const string InventorySql = "SELECT COUNT(*) AS n, CONVERT(decimal(20,6), SUM(ISNULL(s.iNum, 0))) AS qty,"
            + " CONVERT(decimal(18,2), SUM(ISNULL(s.iMoney, 0))) AS amount FROM IA_Summary s WHERE s.iYear = ? AND s.iMonth = ?";
        internal const string HasInvoiceSql = "SELECT TOP 1 1 AS x FROM PurBillVouch";
        const string Verified = " AND NULLIF(LTRIM(RTRIM(h.cHandler)), N'') IS NOT NULL";
        const string DateFrom = " >= CONVERT(date, ?, 23) AND ";
        const string DateTo = " < DATEADD(day, 1, CONVERT(date, ?, 23))";
        const string InvoiceCols = "CONVERT(decimal(20,6), SUM(ISNULL(b.iPBVQuantity, 0))) AS qty,"
            + " CONVERT(decimal(18,2), SUM(ISNULL(b.iMoney, 0))) AS amount, CONVERT(decimal(18,2), SUM(ISNULL(b.iSum, 0))) AS amount_tax_incl,"
            + " COUNT(DISTINCT h.PBVID) AS docs";
        const string ReceiptCols = "CONVERT(decimal(20,6), SUM(ISNULL(b.iQuantity, 0))) AS qty,"
            + " CONVERT(decimal(18,2), SUM(ISNULL(b.iPrice, 0))) AS amount, NULL AS amount_tax_incl, COUNT(DISTINCT h.ID) AS docs";

        // 末级科目的期末余额（借正贷负，各币种行合计本币）。? 的顺序：年度、期间、前缀……、数据权限。
        internal static string Balances(int year, int period, string[] prefixes, List<object> ps, WorkContext ctx)
        {
            StringBuilder sql = new StringBuilder("SELECT s.ccode AS code, c.ccode_name AS name, CONVERT(decimal(18,2), SUM(CASE s.cendd_c");
            sql.Append(" WHEN N'借' THEN ISNULL(s.me, 0) WHEN N'贷' THEN -ISNULL(s.me, 0) ELSE 0 END)) AS balance FROM GL_accsum s");
            sql.Append(" JOIN code c ON c.iyear = s.iyear AND c.ccode = s.ccode AND c.bend = 1 WHERE s.iyear = ? AND s.iperiod = ?");
            ps.Add(year);
            ps.Add(period);
            Reports.LikeAny(sql, ps, "s.ccode", prefixes, false);
            // 数据权限：科目。
            Perm(sql, ps, ctx, PermObj.Account, "s");
            sql.Append(" GROUP BY s.ccode, c.ccode_name ORDER BY s.ccode");
            return sql.ToString();
        }

        // 未处理应收票据合计。? 的顺序：数据权限（客户）。
        internal static string NotesOpen(List<object> ps, WorkContext ctx)
        {
            StringBuilder sql = new StringBuilder(NotesOpenSql);
            Perm(sql, ps, ctx, PermObj.Customer, "x");
            return sql.ToString();
        }

        // 存货结存合计。? 的顺序：年度、期间、数据权限（存货、仓库；按存货核算时仓库为空，空值放行）。
        internal static string Inventory(int year, int period, List<object> ps, WorkContext ctx)
        {
            StringBuilder sql = new StringBuilder(InventorySql);
            ps.Add(year);
            ps.Add(period);
            Perm(sql, ps, ctx, PermObj.Inventory, "s");
            Perm(sql, ps, ctx, PermObj.Warehouse, "s");
            return sql.ToString();
        }

        // 产成品入库按存货的数量排行（缺省只计已审核）。totals 为真时只出合计行。? 的顺序：[TOP]、起、止、数据权限。
        internal static string Production(bool totals, MgmtGlArgs m, string[] range, List<object> ps, WorkContext ctx)
        {
            StringBuilder sql = new StringBuilder("SELECT ");
            if (totals)
            {
                sql.Append("CONVERT(decimal(20,6), SUM(ISNULL(b.iQuantity, 0))) AS qty, COUNT(DISTINCT b.cInvCode) AS n,");
            }
            else
            {
                sql.Append("TOP (?) b.cInvCode AS code, i.cInvName AS name, u.cComUnitName AS unit,");
                sql.Append(" CONVERT(decimal(20,6), SUM(ISNULL(b.iQuantity, 0))) AS qty,");
                ps.Add(m.Top);
            }
            sql.Append(" COUNT(DISTINCT h.ID) AS docs FROM rdrecord10 h JOIN rdrecords10 b ON b.ID = h.ID");
            if (!totals)
            {
                sql.Append(" LEFT JOIN Inventory i ON i.cInvCode = b.cInvCode LEFT JOIN ComputationUnit u ON u.cComunitCode = i.cComUnitCode");
            }
            Dates(sql, ps, "h.dDate", range);
            sql.Append(m.Unverified ? "" : Verified);
            // 数据权限：存货（表体行）、仓库（表头）。
            Perm(sql, ps, ctx, PermObj.Inventory, "b");
            Perm(sql, ps, ctx, PermObj.Warehouse, "h");
            if (!totals)
            {
                sql.Append(" GROUP BY b.cInvCode, i.cInvName, u.cComUnitName ORDER BY SUM(ISNULL(b.iQuantity, 0)) DESC, b.cInvCode");
            }
            return sql.ToString();
        }

        // 采购按供应商的金额排行：invoice 取采购发票（本币无税 iMoney、含税 iSum，不论是否复核），receipt 取采购入库
        // （金额 iPrice，缺省只计已审核）。totals 为真时只出合计行。? 的顺序：[TOP]、起、止、数据权限（供应商）。
        internal static string Purchases(bool invoice, bool totals, MgmtGlArgs m, string[] range, List<object> ps, WorkContext ctx)
        {
            StringBuilder sql = new StringBuilder("SELECT ");
            if (!totals)
            {
                sql.Append("TOP (?) h.cVenCode AS code, v.cVenName AS name, ");
                ps.Add(m.Top);
            }
            sql.Append(invoice ? InvoiceCols : ReceiptCols);
            sql.Append(totals ? ", COUNT(DISTINCT h.cVenCode) AS n" : "");
            sql.Append(PurchaseFrom(invoice, totals));
            Dates(sql, ps, invoice ? "h.dPBVDate" : "h.dDate", range);
            sql.Append(invoice || m.Unverified ? "" : Verified);
            Perm(sql, ps, ctx, PermObj.Vendor, "h");
            if (!totals)
            {
                sql.Append(" GROUP BY h.cVenCode, v.cVenName ORDER BY SUM(ISNULL(").Append(invoice ? "b.iMoney" : "b.iPrice");
                sql.Append(", 0)) DESC, h.cVenCode");
            }
            return sql.ToString();
        }

        static string PurchaseFrom(bool invoice, bool totals)
        {
            string from = invoice ? " FROM PurBillVouch h JOIN PurBillVouchs b ON b.PBVID = h.PBVID"
                : " FROM RdRecord01 h JOIN rdrecords01 b ON b.ID = h.ID";
            return totals ? from : from + " LEFT JOIN Vendor v ON v.cVenCode = h.cVenCode";
        }

        static void Dates(StringBuilder sql, List<object> ps, string col, string[] range)
        {
            sql.Append(" WHERE ").Append(col).Append(DateFrom).Append(col).Append(DateTo);
            ps.Add(range[0]);
            ps.Add(range[1]);
        }

        // 追加 cash_stock 规则里一个受控对象的条件（列名取自规则，alias 是该段的表别名）。只在读路由上过滤；
        // ctx 为 null（自检）或不在读路由上时不过滤。没有授权的对象受控时该段为空（合计为 0），不报错。
        internal static void Perm(StringBuilder sql, List<object> ps, WorkContext ctx, string obj, string alias)
        {
            PermContext p = ReadPerm(ctx);
            if (p == null)
            {
                return;
            }
            PermRule rule = PermRegistry.ForKey(ReportsMgmtGlReq.CashRule);
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                if (rule.Objs[i].Obj == obj)
                {
                    PermSql.AppendObj(sql, ps, p, rule.Objs[i], alias);
                }
            }
        }

        // 读路由上的权限快照；自检（ctx 为 null）或不在读路由上时为 null（不过滤）。mgmt/pnl 的部门名称也用。
        internal static PermContext ReadPerm(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || !PermRegistry.IsRead(ctx.Item.Path))
            {
                return null;
            }
            return PermCheck.Of(ctx);
        }
    }
}
