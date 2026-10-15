using System;
using System.Collections.Generic;

namespace U8Co
{
    // 分族报表（ReportsFamilies）的字段权限范围：哪张报表的哪些金额字段按哪些 U8 字段权限对象（AA_ColumnAuth.cKey）遮蔽。
    // 对象 id 取自账套库 AA_BusObject_base（iAuthType=1）：销售订单 17 / VCH_17 / solist，销售发票 07 / VCH_07 / VCH_13 / 参照发票价格
    // invoicepriceref，采购发票 25，应收 / 应付收款单 VCH_AR48 / VCH_AR49 / VCH_AP48 / VCH_AP49，采购入库单 24 等（PermColumnMap），
    // 各报表自己的对象（销售订单执行统计表、销售统计表、采购综合统计表、库龄分析、收发存汇总表）一并算上。
    // 开关（bAuthControl）没开或没有拒绝行的对象不起作用，所以多列对象无副作用。
    // 没有字段权限对象的报表不登记，不遮：质量统计、生产三张（只有数量）、采购时效、到期票据、现金流量、月末检查（没有金额）；
    // 完工产品成本 cost_unit（U8 没有成本核算的字段权限对象，账套库 AA_BusObject_base 里没有 CA 对象）。
    // 报表自己的对象（Strict）上的拒绝是栏目标题（未发货金额、价税合计、Price1……），对不上响应的键，任何拒绝都按金额组处理。
    // 金额别名见 PermColumnMap.Alias；金额按金额组一起遮，已遮掉的数不能从同行的其他金额反推。
    internal static class PermColumnReports
    {
        internal static readonly string[] SaleOrder = new string[] { "17", "VCH_17", "solist" };
        internal static readonly string[] SaleInvoice = new string[] { "07", "VCH_07", "VCH_13", "invoicepriceref" };
        internal static readonly string[] PuInvoice = new string[] { "25" };
        static readonly string[] OrderExec = new string[] { "SA[__]订单执行统计表" };
        static readonly string[] SaleStat = new string[] { "SA[__]销售统计表" };
        static readonly string[] PuStat = new string[] { "PU[__]综合统计表" };
        internal static readonly string[] Receipts = new string[] { "VCH_AR48", "VCH_AR49" };
        internal static readonly string[] Payments = new string[] { "VCH_AP48", "VCH_AP49" };
        // 收发存汇总表、库龄分析的对象；金额来自存货核算，没有自己的明细对象，另从严并上出入库单列表（同期初余额）。
        static readonly string[] Summary = new string[] { "0c74b9f9-d4a0-43d5-ac1d-ba1c9cb26a69" };
        static readonly string[] Age = new string[] { "STInventoryAge", "STInventoryAgeSum", "ST[__]呆滞积压存货分析" };

        // 该报表要遮的范围；不遮（或不是这些报表）返回 null。
        internal static ColScope ScopeOf(WorkItem item, string name)
        {
            return SaleScope(item, name) ?? StockScope(item, name);
        }

        static ColScope SaleScope(WorkItem item, string name)
        {
            switch (name)
            {
                case "open_orders":
                    return new ColScope("", Join(SaleOrder, OrderExec), OrderExec);
                case "sales_price_history":
                    return new ColScope("", SaleInvoice);
                case "customer_churn":
                    return new ColScope("", Join(SaleInvoice, SaleStat), SaleStat);
                case "receipts_payments":
                    return new ColScope("", PermColumnMap.BodyText(item, "side") == "ap" ? Payments : Receipts);
                default:
                    return null;
            }
        }

        static ColScope StockScope(WorkItem item, string name)
        {
            switch (name)
            {
                case "purchase_analysis":
                    bool receipt = PermColumnMap.BodyText(item, "source") == "receipt";
                    return new ColScope("", Join(receipt ? PermColumnMap.PurchaseIn : PuInvoice, PuStat), PuStat);
                case "ia_anomalies":
                    return new ColScope("unbilled", PermColumnMap.PurchaseIn);
                case "stock_summary":
                    return new ColScope("", Join(Summary, PermColumnMap.RdUnion), Summary);
                case "stock_age":
                    return new ColScope("", Join(Age, PermColumnMap.RdUnion), Age);
                default:
                    return null;
            }
        }

        static string[] Join(string[] a, string[] b)
        {
            string[] all = new string[a.Length + b.Length];
            Array.Copy(a, all, a.Length);
            Array.Copy(b, 0, all, a.Length, b.Length);
            return all;
        }
    }
}
