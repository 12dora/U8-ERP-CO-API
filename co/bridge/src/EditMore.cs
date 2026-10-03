using System.Collections.Generic;

namespace U8Co
{
    // 生单来的单据和应收应付的修改分派。Dispatch.Update 先问这里，返回 null 再走原有分派。
    // 各领域类自己做闸门、白名单、重算和来源累计数核对：销售 SaleEditMore，库存 StockEditSrc（采购入库、销售出库来源为库存时
    // 它自己转回 StockEdit.Update），采购 PuEditMore，应收应付 ArapEdit。meta 的修改名单也从这里取（MetaWritable）。
    internal static class EditMore
    {
        static readonly HashSet<string> Sale = Names("dispatch", "sale_return", "sale_invoice");
        static readonly HashSet<string> Stock = Names("sale_out", "product_in", "material_out", "purchase_in");
        static readonly HashSet<string> Pu = Names("arrival", "purchase_return", "purchase_invoice");
        static readonly HashSet<string> Arap = Names("ar_receipt", "ap_payment", "ar_bill", "ap_bill", "ap_refund", "ar_refund");

        internal static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            switch (AreaOf(kind))
            {
                case "sa":
                    return SaleEditMore.Update(ctx, kind, id, head, lines);
                case "st":
                    return StockEditSrc.Update(ctx, kind, id, head, lines);
                case "pu":
                    return PuEditMore.Update(ctx, kind, id, head, lines);
                case "ar":
                    return ArapEdit.Update(ctx, kind, id, head, lines);
            }
            return null;
        }

        internal static bool Handles(VoucherKind kind)
        {
            return AreaOf(kind).Length > 0;
        }

        // lowerField 为小写字段名；true 表示修改时可写。
        internal static bool UpdateAllowed(VoucherKind kind, bool head, string lowerField)
        {
            switch (AreaOf(kind))
            {
                case "sa":
                    return SaleEditMore.MetaAllowed(kind, head, lowerField);
                case "st":
                    return StockEditSrc.MetaAllowed(kind, head, lowerField);
                case "pu":
                    return PuEditMore.MetaAllowed(kind, head, lowerField);
                case "ar":
                    return ArapEdit.MetaAllowed(kind, head, lowerField);
            }
            return false;
        }

        static string AreaOf(VoucherKind kind)
        {
            string name = kind == null || kind.Name == null ? "" : kind.Name;
            if (Sale.Contains(name))
            {
                return "sa";
            }
            if (Stock.Contains(name))
            {
                return "st";
            }
            if (Pu.Contains(name))
            {
                return "pu";
            }
            return Arap.Contains(name) ? "ar" : "";
        }

        static HashSet<string> Names(params string[] names)
        {
            return new HashSet<string>(names);
        }
    }
}
