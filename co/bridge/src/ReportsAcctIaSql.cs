using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 存货核算检查 ia_anomalies 的 SQL，月末结账检查（ReportsAcctCheckSql）的「存货核算未记账」共用 UnbookedUnion。
    // 全是常量，调用方的值只进 ? 参数；表名、列名只来自这里的常量。
    internal static class ReportsAcctIaSql
    {
        // 参与存货核算的库存单据：{ 响应里的 type, 存货核算单据类型（IA_Subsidiary.cVouType 的前缀）, 表头, 表体 }。
        // 销售出库单在存货核算里的类型是 3201，前缀 32 能对上。
        internal static readonly string[][] Types = new string[][]
        {
            new string[] { "purchase_in", "01", "RdRecord01", "rdrecords01" },
            new string[] { "other_in", "08", "RdRecord08", "rdrecords08" },
            new string[] { "other_out", "09", "RdRecord09", "rdrecords09" },
            new string[] { "product_in", "10", "rdrecord10", "rdrecords10" },
            new string[] { "material_out", "11", "rdrecord11", "rdrecords11" },
            new string[] { "sale_out", "32", "rdrecord32", "rdrecords32" }
        };

        // 未记账的库存单据行：已审核（cHandler 非空）、不是库存期初、日期在本期、数量不为 0、
        // 在 IA_Subsidiary 里没有行（IA_Subsidiary.ID 是单据行的 AutoID；数量为 0 的行存货核算本来就不记）；
        // 仓库不参与成本核算（Warehouse.bInCost = 0）的单据存货核算也不记，不算。
        // 每个类型一段，参数各两个：本月 1 日、下月 1 日。列：typ、code、dt、cVenCode、cWhCode、cInvCode、qty、aid。
        const string BlockHead = "SELECT N'{0}' typ, h.cCode code, h.dDate dt, h.cVenCode cVenCode, h.cWhCode cWhCode,"
            + " r.cInvCode cInvCode, r.iQuantity qty, r.AutoID aid FROM {1} h JOIN {2} r ON r.ID=h.ID"
            + " LEFT JOIN Warehouse w0 ON w0.cWhCode=h.cWhCode"
            + " WHERE ISNULL(w0.bInCost,1)=1 AND ISNULL(h.cHandler,N'')<>N'' AND ISNULL(h.bIsSTQc,0)=0 AND h.dDate>=CONVERT(date, ?, 23)"
            + " AND h.dDate<CONVERT(date, ?, 23) AND ISNULL(r.iQuantity,0)<>0"
            + " AND NOT EXISTS (SELECT 1 FROM IA_Subsidiary s WHERE s.ID=r.AutoID AND s.cVouType LIKE N'{3}%')";

        internal static readonly string UnbookedUnion = BuildUnion();

        static string BuildUnion()
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < Types.Length; i++)
            {
                sb.Append(i == 0 ? "" : " UNION ALL ");
                sb.Append(string.Format(System.Globalization.CultureInfo.InvariantCulture, BlockHead, Types[i][0], Types[i][2],
                    Types[i][3], Types[i][1]));
            }
            return sb.ToString();
        }

        // 暂估未回冲：已审核的采购入库单行（不含库存期初），日期在本期末及以前，数量不等于已开票数量。
        // 估价 = 未开票数量 × 入库单上的无税单价（iUnitCost）；红字入库（数量为负）未开红字发票时未开票数量、估价为负。
        // count、est_total 是全部行的个数和估价合计（不受 TOP 影响）。开票状态是现在（查询时）的，不是期末当天的。
        // 参数：行数, 期末日, 次月 1 日，再接数据权限（别名 x）；ORDER BY 由 UnbilledOrder 补上。
        internal const string Unbilled = "SELECT TOP (?) COUNT(*) OVER() n, SUM(x.est) OVER() est_total, x.code code,"
            + " CONVERT(char(10), x.dt, 23) d, x.vname vname, x.cInvCode inv_code, x.iname iname, x.unit unit, x.qty qty,"
            + " x.billed billed, x.est est, DATEDIFF(day, x.dt, CONVERT(date, ?, 23)) days"
            + " FROM (SELECT h.cCode code, h.dDate dt, h.cVenCode cVenCode, h.cWhCode cWhCode, r.cInvCode cInvCode,"
            + " v.cVenName vname, i.cInvName iname, cu.cComUnitName unit, r.iQuantity qty,"
            + " ISNULL(r.iSumBillQuantity,0) billed, (r.iQuantity-ISNULL(r.iSumBillQuantity,0))*ISNULL(r.iUnitCost,0) est,"
            + " r.AutoID aid FROM RdRecord01 h JOIN rdrecords01 r ON r.ID=h.ID"
            + " LEFT JOIN Vendor v ON v.cVenCode=h.cVenCode LEFT JOIN Inventory i ON i.cInvCode=r.cInvCode"
            + " LEFT JOIN ComputationUnit cu ON cu.cComUnitCode=i.cComUnitCode"
            + " WHERE ISNULL(h.cHandler,N'')<>N'' AND ISNULL(h.bIsSTQc,0)=0 AND h.dDate<CONVERT(date, ?, 23)"
            + " AND r.iQuantity-ISNULL(r.iSumBillQuantity,0)<>0) x WHERE 1=1";

        // 估价大的在前，再按日期、单号。
        internal const string UnbilledOrder = " ORDER BY x.est DESC, x.dt, x.code, x.aid";

        // 字段权限会遮估价时不能按估价排序、截断：按日期（旧的在前）、单号。
        internal const string UnbilledOrderByDate = " ORDER BY x.dt, x.code, x.aid";

        // 未记账单据：参数 行数, 12 个日期（UnbookedUnion），再接数据权限（别名 x）；ORDER BY 由 UnbookedOrder 补上。
        internal static readonly string Unbooked = "SELECT TOP (?) COUNT(*) OVER() n, x.typ typ, x.code code,"
            + " CONVERT(char(10), x.dt, 23) d, w.cWhName wname, x.cInvCode inv_code, i.cInvName iname,"
            + " cu.cComUnitName unit, x.qty qty FROM (" + UnbookedUnion + ") x"
            + " LEFT JOIN Warehouse w ON w.cWhCode=x.cWhCode LEFT JOIN Inventory i ON i.cInvCode=x.cInvCode"
            + " LEFT JOIN ComputationUnit cu ON cu.cComUnitCode=i.cComUnitCode WHERE 1=1";

        internal const string UnbookedOrder = " ORDER BY x.dt, x.typ, x.code, x.aid";

        // 12 个日期参数：6 段，每段本月 1 日、下月 1 日。
        internal static void AddUnbookedDates(List<object> ps, string first, string next)
        {
            for (int i = 0; i < Types.Length; i++)
            {
                ps.Add(first);
                ps.Add(next);
            }
        }
    }
}
