using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售 BodyCheck 只能对单行表体调用。多行时 U8 读第 R 行、写第 0 行。
    // 不用 CallRef：按引用传 DOM 会把文档换成无效对象。
    internal static class SaleCalc
    {
        public static void ForCreate(object co, object headDom, object bodyDom, object[] lines)
        {
            if (lines == null)
            {
                return;
            }
            int count = DomRows.RowsOf(bodyDom).Count;
            if (count != lines.Length)
            {
                throw new BridgeException(500, "internal", "模板行数不符");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> map = lines[i] as Dictionary<string, object>;
                object row = At(bodyDom, i);
                Recalc(co, headDom, bodyDom, row, Flags(Has(map, "itaxunitprice"), Has(map, "iunitprice"), Has(map, "itaxrate")));
            }
        }

        public static SalePrices Flags(bool taxPrice, bool unitPrice, bool taxRate)
        {
            SalePrices flags = new SalePrices();
            flags.TaxPrice = taxPrice;
            flags.UnitPrice = unitPrice;
            flags.TaxRate = taxRate;
            return flags;
        }

        // 顺序：iquantity，调用方给的价格键，若改了税率再 itaxrate 并再算一次价格。
        public static void Recalc(object co, object headDom, object bodyDom, object row, SalePrices flags)
        {
            Check(co, headDom, bodyDom, row, "iquantity", true);
            Prices(co, headDom, bodyDom, row, flags, false);
        }

        // 件数是数量键：BodyCheck(inum) 回写 iquantity，再按价格键算金额。
        public static void RecalcNum(object co, object headDom, object bodyDom, object row, SalePrices flags)
        {
            Check(co, headDom, bodyDom, row, "inum", true);
            Prices(co, headDom, bodyDom, row, flags, true);
        }

        // 表头汇率或币种变了：按行上已有的数量和价格重算，含未改动的行。
        public static void RecalcExisting(object co, object headDom, object bodyDom, object row)
        {
            Check(co, headDom, bodyDom, row, "iquantity", true);
            Prices(co, headDom, bodyDom, row, new SalePrices(), true);
        }

        static void Prices(object co, object headDom, object bodyDom, object row, SalePrices flags, bool force)
        {
            string price = "";
            if (flags.TaxPrice)
            {
                price = "itaxunitprice";
            }
            else if (flags.UnitPrice)
            {
                price = "iunitprice";
            }
            else if (force)
            {
                price = PriceKey(row);
            }
            if (price.Length > 0)
            {
                Check(co, headDom, bodyDom, row, price, true);
            }
            if (!flags.TaxRate)
            {
                return;
            }
            Check(co, headDom, bodyDom, row, "itaxrate", true);
            if (price.Length == 0)
            {
                price = PriceKey(row);
            }
            if (price.Length > 0)
            {
                Check(co, headDom, bodyDom, row, price, true);
            }
        }

        // strict 为假时，U8 返回非空则保留调用前的行，不抛错。不释放调用方持有的 row。
        public static string Check(object co, object headDom, object bodyDom, object row, string key, bool strict)
        {
            string current = DomRows.Get(row, key);
            object tmp = DomRows.SingleRowCopy(bodyDom, row);
            try
            {
                List<object> tmpRows = DomRows.RowsOf(tmp);
                if (tmpRows.Count < 1)
                {
                    throw new BridgeException(500, "internal", "重算后没有明细");
                }
                if (current.Length > 0)
                {
                    DomRows.Set(tmp, tmpRows[0], key, current);
                }
                object ret = ComUtil.Call(co, "BodyCheck", new object[] { key, tmp, headDom, 1 });
                string msg = ret == null ? "" : Convert.ToString(ret);
                if (msg.Length > 0)
                {
                    if (strict)
                    {
                        throw new BridgeException(409, "u8_rejected", msg);
                    }
                    return msg;
                }
                tmpRows = DomRows.RowsOf(tmp);
                if (tmpRows.Count > 0)
                {
                    DomRows.CopyAttrs(tmpRows[0], row);
                    return "";
                }
                // 按价格键调用时 U8 不改行，而是把 DOM 换成 <body><R K="imoney" V="486.73"/>…</body>。
                if (ApplyPairs(tmp, bodyDom, row) == 0)
                {
                    throw new BridgeException(500, "internal", "重算后没有明细");
                }
                return "";
            }
            finally
            {
                ComUtil.Final(tmp);
            }
        }

        static int ApplyPairs(object result, object bodyDom, object row)
        {
            List<string> schema = DomRows.Schema(bodyDom);
            object list = ComUtil.Call(result, "selectNodes", new object[] { "//R[@K]" });
            int applied = 0;
            try
            {
                int n = Convert.ToInt32(ComUtil.Get(list, "length"));
                for (int i = 0; i < n; i++)
                {
                    object node = ComUtil.Call(list, "item", new object[] { i });
                    try
                    {
                        string key = Values.Text(ComUtil.Call(node, "getAttribute", new object[] { "K" })).Trim();
                        string name = Known(schema, key);
                        if (name.Length == 0)
                        {
                            continue;
                        }
                        DomRows.Set(bodyDom, row, name, Values.Text(ComUtil.Call(node, "getAttribute", new object[] { "V" })));
                        applied++;
                    }
                    finally
                    {
                        ComUtil.ReleaseOne(node);
                    }
                }
                return applied;
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        static string Known(List<string> schema, string key)
        {
            if (key.Length == 0)
            {
                return "";
            }
            for (int i = 0; i < schema.Count; i++)
            {
                if (string.Equals(schema[i], key, StringComparison.OrdinalIgnoreCase))
                {
                    return schema[i];
                }
            }
            return "";
        }

        static string PriceKey(object row)
        {
            if (DomRows.Get(row, "itaxunitprice").Length > 0)
            {
                return "itaxunitprice";
            }
            if (DomRows.Get(row, "iunitprice").Length > 0)
            {
                return "iunitprice";
            }
            return "";
        }

        static object At(object bodyDom, int index)
        {
            List<object> rows = DomRows.RowsOf(bodyDom);
            if (index < 0 || index >= rows.Count)
            {
                throw new BridgeException(500, "internal", "模板行数不符");
            }
            return rows[index];
        }

        static bool Has(Dictionary<string, object> map, string name)
        {
            if (map == null || name == null)
            {
                return false;
            }
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (!string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                return pair.Value != null && !(pair.Value is DBNull);
            }
            return false;
        }
    }

    internal sealed class SalePrices
    {
        internal bool TaxPrice;
        internal bool UnitPrice;
        internal bool TaxRate;
    }
}
