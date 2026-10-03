using System;
using System.Collections.Generic;

namespace U8Co
{
    // 字段权限：桥的单据类型 / 报表段 → U8 字段权限对象（AA_ColumnAuth.cKey，即 iAuthType=1 的 cBusObId），
    // 以及字段名的规范化、金额组与别名。PermMask 按这里的范围遮字段。只有常量和纯函数。
    // 范围的 Keys 为 null 表示「类型没有登记对象」：从严，用操作员全部对象的拒绝字段逐个按名字比对（不展开金额组）。
    internal sealed class ColScope
    {
        // 只遮响应体里的这一段（如 purchases）；空串表示整个响应体。
        public string Section;
        public string[] Keys;

        public ColScope(string section, string[] keys)
        {
            Section = section ?? "";
            Keys = keys;
        }
    }

    internal static class PermColumnMap
    {
        static readonly string[] PurchaseIn = new string[] { "24", "pu[__]入库明细表", "puordergeneralqueryrkd" };
        static readonly string[] Arrival = new string[] { "26" };
        static readonly string[] Order = new string[] { "88" };
        static readonly string[] SaleOut = new string[] { "0303" };
        static readonly string[] DispatchKeys = new string[] { "01", "VCH_01", "dispatchpriceref", "SARefDispB" };
        // 期初结存、存货核算的金额没有自己的字段权限对象：从严取出入库单列表的并集。
        internal static readonly string[] RdUnion = new string[] { "24", "0301", "0302", "0303", "0411", "0412" };
        // U8 没有字段权限对象的模块（总账、应收应付、票据）：不遮，也不按名字比对（金额别名在这些模块里另有含义）。
        static readonly string[] NoObject = new string[0];

        static readonly Dictionary<string, string[]> TypeKeys = BuildTypes();

        // 金额组：活动对象上任一成员被拒绝时，响应里出现的全部成员一起置空（从严，防止从同行的其他金额反推）。数量不在组里。
        static readonly HashSet<string> Money = Set(
            "iUnitCost", "iPrice", "iAPrice", "iPUnitCost", "iPPrice", "iOriCost", "iOriTaxCost", "iOriMoney", "iOriSum",
            "iOriTaxPrice", "iTaxPrice", "iSum", "iMoney", "iTax", "iCost", "iDisCount", "iUnitPrice", "iTaxUnitPrice",
            "iQuotedPrice", "iNatUnitPrice", "iNatMoney", "iNatTax", "iNatSum", "iNatDisCount", "iOrderAmt", "iOrderAmt_f",
            "fSaleCost", "fSalePrice", "iTVACost", "iTVAPrice", "iTVPCost", "iTVPPrice", "iAVACost", "iAVAPrice",
            "iAVPCost", "iAVPPrice", "iArrMoney", "iArrNatMoney", "iInvMoney", "iOriTotal", "iTotal", "iFHMoney",
            "iKPMoney", "iExchSum", "iMoneySum", "iAOutPrice", "iAInPrice", "iProcessCost", "iProcessFee", "iMaterialFee",
            "iSettleCost", "iNatInvMoney", "iOriInvMoney", "iClaim", "fcusminprice");

        // 桥自己起的金额别名（列表、报表的汇总列）。只在有字段权限对象的范围里起作用。
        static readonly HashSet<string> Alias = Set(
            "amount", "amt", "nat_amount", "amount_tax_incl", "total_amount", "total_amount_tax_incl", "cogs", "gross",
            "gross_pct", "in_price", "price", "unit_price", "unit_cost", "cost");

        // 报表对象的中文列名 → 物理列名（如 pu[__]入库明细表）。没有对上的中文名不起作用（响应里没有这个键）。
        static readonly Dictionary<string, string> Titles = BuildTitles();

        static Dictionary<string, string[]> BuildTypes()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase);
            map["purchase_in"] = PurchaseIn;
            map["arrival"] = Arrival;
            map["purchase_return"] = Arrival;
            map["purchase_order"] = Order;
            map["purchase_requisition"] = Order;
            map["other_in"] = new string[] { "0301" };
            map["other_out"] = new string[] { "0302" };
            map["sale_out"] = SaleOut;
            map["transfer"] = new string[] { "0304" };
            map["transfer_request"] = new string[] { "0304" };
            map["shape_change"] = new string[] { "0305" };
            map["product_in"] = new string[] { "0411" };
            map["material_out"] = new string[] { "0412" };
            map["dispatch"] = DispatchKeys;
            map["sale_return"] = DispatchKeys;
            map["sale_return_apply"] = DispatchKeys;
            map["purchase_settle"] = PurchaseIn;
            map["stock_opening"] = RdUnion;
            map["stock_check"] = RdUnion;
            map["ia_adjust"] = RdUnion;
            map["inventory_price_adjust"] = RdUnion;
            string[] none = new string[]
            {
                "ar_bill", "ap_bill", "ar_receipt", "ap_payment", "ar_refund", "ap_refund", "ar_note", "ap_note"
            };
            for (int i = 0; i < none.Length; i++)
            {
                map[none[i]] = NoObject;
            }
            return map;
        }

        static Dictionary<string, string> BuildTitles()
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            map["原币无税金额"] = "iOriMoney";
            map["原币价税合计"] = "iOriSum";
            map["原币税额"] = "iOriTaxPrice";
            map["原币含税单价"] = "iOriTaxCost";
            map["原币无税单价"] = "iOriCost";
            map["本币价税合计"] = "iSum";
            map["本币税额"] = "iTaxPrice";
            map["本币无税金额"] = "iMoney";
            map["本币无税单价"] = "iUnitCost";
            return map;
        }

        static HashSet<string> Set(params string[] names)
        {
            return new HashSet<string>(names, StringComparer.OrdinalIgnoreCase);
        }

        // 单据类型 → 对象；没有登记返回 null（调用方从严按全部对象比对）。
        internal static string[] KeysOfType(string type)
        {
            string[] keys;
            if (type != null && TypeKeys.TryGetValue(type.Trim(), out keys))
            {
                return keys;
            }
            return null;
        }

        // 字段名规范化：去空格；带「B;」「T;」这类前缀时取最后一个分号之后；中文列名换成物理列名。比较一律不分大小写。
        internal static string Normalize(string fld)
        {
            string text = fld == null ? "" : fld.Trim();
            int cut = text.LastIndexOf(';');
            if (cut >= 0)
            {
                text = text.Substring(cut + 1).Trim();
            }
            string mapped;
            return Titles.TryGetValue(text, out mapped) ? mapped : text;
        }

        internal static bool IsMoney(string name)
        {
            return name != null && Money.Contains(name);
        }

        // 金额组成员或金额别名（含 *_amount，如 shipped_amount、paid_nat_amount）。
        internal static bool IsMoneyKey(string name)
        {
            if (name == null)
            {
                return false;
            }
            return Money.Contains(name) || Alias.Contains(name) || name.EndsWith("_amount", StringComparison.OrdinalIgnoreCase);
        }

        // 一个请求要遮的范围；空表示不遮（总账、档案、没有金额列的报表等）。
        internal static List<ColScope> Plan(WorkItem item)
        {
            List<ColScope> plan = new List<ColScope>();
            string path = item == null || item.Path == null ? "" : item.Path;
            if (path.Length == 0 || PermSnapshot.Owns(path) || path.StartsWith(Requests.GlRoot, StringComparison.Ordinal)
                || path.StartsWith(Requests.ArcRoot, StringComparison.Ordinal))
            {
                return plan;
            }
            if (path.StartsWith(Requests.ReportRoot, StringComparison.Ordinal))
            {
                ReportPlan(item, path.Substring(Requests.ReportRoot.Length), plan);
                return plan;
            }
            string type = TypeOf(item);
            if (type.Length > 0)
            {
                plan.Add(new ColScope("", KeysOfType(type)));
            }
            return plan;
        }

        // 报表：订单执行按订单类型；销售分析的成本按销售出库；资金存货只遮采购段与存货段；期初只遮库存期初。
        static void ReportPlan(WorkItem item, string name, List<ColScope> plan)
        {
            if (name == ReportsTraceReq.ExecName)
            {
                plan.Add(new ColScope("", KeysOfType(BodyText(item, "type"))));
            }
            else if (name == ReportsMgmtSalesReq.Name)
            {
                plan.Add(new ColScope("", SaleOut));
            }
            else if (name == ReportsMgmtGlReq.CashName)
            {
                plan.Add(new ColScope("purchases", PurchaseIn));
                plan.Add(new ColScope("inventory", RdUnion));
            }
            else if (name == ReportsOpeningReq.Name && BodyText(item, "module") == "stock")
            {
                plan.Add(new ColScope("", RdUnion));
            }
        }

        static string TypeOf(WorkItem item)
        {
            if (item.Type != null)
            {
                return item.Type.Name ?? "";
            }
            return BodyText(item, "type");
        }

        static string BodyText(WorkItem item, string key)
        {
            if (item == null || item.Body == null)
            {
                return "";
            }
            string text = Requests.Field(item.Body, key) as string;
            return text == null ? "" : text.Trim();
        }
    }
}
