using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 行上的 bsaleprice（报价含税）。U8 单行 BodyCheck 按它决定：1 从含税单价算（isum = round(数量 × 含税单价, 2)），
    // 0 / 空从无税单价算。照 U8 界面写，都在第一次 BodyCheck 之前：
    // - 新增行（销售订单、无来源发货单 / 发票、修改时新增的行）：Fresh，写账套选项「销售报价是否含税」（SA.bSalePrice）；
    // - 参照 / 冲销行（生单、退货、退货申请、红字发票、红冲蓝字）：Keep，来源行有明确值（0 / 1 / True / False）照抄，
    //   空时才写账套选项；来源行没有含税单价（空或 0）时不写（同旧行为，免得按 0 含税单价算出 0 金额）。
    // 选项读不到、表体模板没有该列时不写，每个请求各记一次审计。schema 每个 DOM 只读一次。
    internal static class SaleTaxFlag
    {
        internal const string Name = "bsaleprice";

        public static void FreshAll(WorkContext ctx, object bodyDom)
        {
            List<object> rows = DomRows.RowsOf(bodyDom);
            for (int i = 0; i < rows.Count; i++)
            {
                Write(ctx, bodyDom, rows[i], null);
            }
        }

        public static void Fresh(WorkContext ctx, object bodyDom, object row)
        {
            Write(ctx, bodyDom, row, null);
        }

        public static void Keep(WorkContext ctx, object bodyDom, object row)
        {
            Keep(ctx, bodyDom, row, null);
        }

        // schema 为 null 时按 DOM 取本请求缓存的 schema。
        public static void Keep(WorkContext ctx, object bodyDom, object row, List<string> schema)
        {
            if (DomRows.Get(row, Name).Trim().Length > 0 || !HasTaxPrice(row))
            {
                return;
            }
            Write(ctx, bodyDom, row, schema);
        }

        internal static bool IsOn(string value)
        {
            string text = value == null ? "" : value.Trim();
            return text == "1" || string.Equals(text, "true", StringComparison.OrdinalIgnoreCase);
        }

        static void Write(WorkContext ctx, object bodyDom, object row, List<string> schema)
        {
            string want = ctx.SalePriceTaxed;
            if (want.Length == 0)
            {
                NoteOnce(ctx, ref ctx.SaleTaxNotedOption, "账套销售选项 SA.bSalePrice 读不到或认不出，未写 " + Name);
                return;
            }
            List<string> names = schema ?? SchemaOf(ctx, bodyDom);
            if (!InSchema(names))
            {
                NoteOnce(ctx, ref ctx.SaleTaxNotedSchema, "表体模板没有 " + Name + "，未写报价含税");
                return;
            }
            DomRows.Set(bodyDom, row, Name, want, names);
        }

        static List<string> SchemaOf(WorkContext ctx, object bodyDom)
        {
            if (ctx.SaleTaxSchema == null || !ReferenceEquals(ctx.SaleTaxDom, bodyDom))
            {
                ctx.SaleTaxDom = bodyDom;
                ctx.SaleTaxSchema = DomRows.Schema(bodyDom);
            }
            return ctx.SaleTaxSchema;
        }

        static void NoteOnce(WorkContext ctx, ref bool noted, string text)
        {
            if (noted)
            {
                return;
            }
            noted = true;
            CoRows.Note(ctx.Item, text);
        }

        static bool HasTaxPrice(object row)
        {
            decimal price;
            string text = DomRows.Get(row, "itaxunitprice").Trim();
            return decimal.TryParse(text, NumberStyles.Any, CultureInfo.InvariantCulture, out price) && price != 0m;
        }

        static bool InSchema(List<string> schema)
        {
            for (int i = 0; schema != null && i < schema.Count; i++)
            {
                if (string.Equals(schema[i], Name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
