using System;

namespace U8Co
{
    // 销售发票表头上的数电 / 金税号列：按账套配置，列名只来自白名单。vouchers/list、vouchers/search 的
    // tax_invoice_no 和 code_like 的 OR 都读这里。
    internal static class TaxInvoiceNo
    {
        internal const string Alias = "tax_invoice_no";
        internal const string KindName = "sale_invoice";
        internal const string ExtraNull = "NULL AS " + Alias;

        // SaleBillVouch 表头文本列（与 vouchers/search defines 的文本自定义项一致，另加 cTaxBillCode）。
        internal static readonly string[] Allowed = new string[]
        {
            "cDefine1", "cDefine2", "cDefine3",
            "cDefine8", "cDefine9", "cDefine10", "cDefine11", "cDefine12", "cDefine13", "cDefine14",
            "cTaxBillCode"
        };

        internal static string Canonical(string column)
        {
            if (column == null)
            {
                return null;
            }
            string text = column.Trim();
            for (int i = 0; i < Allowed.Length; i++)
            {
                if (string.Equals(Allowed[i], text, StringComparison.OrdinalIgnoreCase))
                {
                    return Allowed[i];
                }
            }
            return null;
        }

        internal static string FieldOf(WorkContext ctx)
        {
            if (ctx == null)
            {
                return null;
            }
            BridgeConfig cfg = ctx.Config;
            WorkItem item = ctx.Item;
            if (cfg == null && item != null)
            {
                cfg = item.Config;
            }
            return FieldOf(item, cfg);
        }

        internal static string FieldOf(WorkItem item)
        {
            return item == null ? null : FieldOf(item, item.Config);
        }

        static string FieldOf(WorkItem item, BridgeConfig cfg)
        {
            return FieldOf(cfg, item == null ? null : item.Acc);
        }

        internal static string FieldOf(BridgeConfig cfg, string acc)
        {
            if (cfg == null || cfg.TaxInvoiceNoFields == null || acc == null)
            {
                return null;
            }
            string col;
            return cfg.TaxInvoiceNoFields.TryGetValue(acc, out col) ? col : null;
        }

        // code_like 可匹配该列：已配置且操作员对该列有字段权限。无权时只匹配 U8 单号，避免用隐藏列探数。
        internal static string SearchColumn(ListKind kind, WorkContext ctx)
        {
            if (kind == null || kind[ListKind.Name] != KindName)
            {
                return null;
            }
            string col = FieldOf(ctx);
            return col == null || ColumnDenied(ctx, col) ? null : col;
        }

        internal static bool ColumnDenied(WorkContext ctx, string column)
        {
            return ctx != null && FieldDenied(ctx.Perm, column);
        }

        internal static bool FieldDenied(PermContext p, string column)
        {
            return PermColumnMap.FieldDenied(p, KindName, column);
        }

        // Extra 里占位 NULL AS tax_invoice_no；有配置时换成表头列（空串当 null）。仍 SELECT 再遮，不从 SELECT 里拿掉。
        internal static string FillExtra(string extra, WorkContext ctx)
        {
            if (extra == null || extra.IndexOf(ExtraNull, StringComparison.Ordinal) < 0)
            {
                return extra;
            }
            string col = FieldOf(ctx);
            if (col == null)
            {
                return extra;
            }
            return extra.Replace(ExtraNull, "NULLIF(LTRIM(RTRIM(h." + col + ")), N'') AS " + Alias);
        }
    }
}
