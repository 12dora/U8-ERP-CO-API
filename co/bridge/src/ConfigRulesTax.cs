using System;
using System.Collections.Generic;

namespace U8Co
{
    // config.json 的 taxInvoiceNoFields：账套 → 销售发票表头数电 / 金税号列。缺省未配置。
    internal static class ConfigRulesTax
    {
        public const string Key = "taxInvoiceNoFields";

        public static Dictionary<string, string> Load(Dictionary<string, object> map, string[] allowedAccounts)
        {
            Dictionary<string, string> fields = new Dictionary<string, string>(StringComparer.Ordinal);
            object raw;
            if (map == null || !map.TryGetValue(Key, out raw))
            {
                return fields;
            }
            Dictionary<string, object> obj = raw as Dictionary<string, object>;
            if (obj == null)
            {
                throw new InvalidOperationException(Key + " 必须是对象，形如 {\"801\":\"cDefine10\"}");
            }
            foreach (KeyValuePair<string, object> pair in obj)
            {
                Add(fields, pair.Key, pair.Value, allowedAccounts ?? new string[0]);
            }
            return fields;
        }

        static void Add(Dictionary<string, string> fields, string acc, object value, string[] allowed)
        {
            if (!ConfigRules.AccountDigits(acc))
            {
                throw new InvalidOperationException(Key + " 的键必须是 3 位数字的账套号：" + acc);
            }
            if (!Listed(allowed, acc))
            {
                throw new InvalidOperationException(Key + " 的账套必须在 allowedAccounts 里：" + acc);
            }
            string col = value as string;
            if (col == null)
            {
                throw new InvalidOperationException(Key + "." + acc + " 必须是列名字符串");
            }
            string canon = TaxInvoiceNo.Canonical(col);
            if (canon == null)
            {
                throw new InvalidOperationException(Key + "." + acc
                    + " 的列名必须是销售发票表头文本列（cDefine1–3、cDefine8–14、cTaxBillCode）：" + col.Trim());
            }
            fields[acc] = canon;
        }

        static bool Listed(string[] allowed, string acc)
        {
            for (int i = 0; i < allowed.Length; i++)
            {
                if (string.Equals(allowed[i], acc, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            return false;
        }

        public static string Describe(BridgeConfig cfg)
        {
            Dictionary<string, string> map = cfg == null ? null : cfg.TaxInvoiceNoFields;
            if (map == null || map.Count == 0)
            {
                return Key + "（销售发票数电号列）: （未配置）";
            }
            List<string> parts = new List<string>();
            foreach (KeyValuePair<string, string> pair in map)
            {
                parts.Add(pair.Key + "=" + pair.Value);
            }
            parts.Sort(StringComparer.Ordinal);
            return Key + "（销售发票数电号列）: " + string.Join(", ", parts.ToArray());
        }
    }
}
