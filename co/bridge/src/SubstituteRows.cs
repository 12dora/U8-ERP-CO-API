using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 替代料对外的一项（生产订单子件 mom_moallocatesub、物料清单子件 bom_opcomponentsub 共用，形状和日期规则一致）：
    // sequence、inv_code、factor（数）、replace_flag（布尔）、eff_begin、eff_end（yyyy-MM-dd），表体自定义项有值的放在
    // defines（键 define22…define37；float 给数、int 给整数、日期没有时分秒给 yyyy-MM-dd，否则到秒）。
    // 输入是 SQL 读出的原文：NULL 可以是 null、空串或 NullMark；日期可以是 style 23 / 120 / 121 的文本。纯函数，--selftest 覆盖。
    internal static class SubstituteRows
    {
        public const string NullMark = "<null>";
        // Define22–37 的列号和类型（s 文本、f float、i int、d 日期时间），两张表相同。
        internal static readonly string[][] Defines = new string[][]
        {
            new string[] { "22", "s" }, new string[] { "23", "s" }, new string[] { "24", "s" }, new string[] { "25", "s" },
            new string[] { "26", "f" }, new string[] { "27", "f" }, new string[] { "28", "s" }, new string[] { "29", "s" },
            new string[] { "30", "s" }, new string[] { "31", "s" }, new string[] { "32", "s" }, new string[] { "33", "s" },
            new string[] { "34", "i" }, new string[] { "35", "i" }, new string[] { "36", "d" }, new string[] { "37", "d" }
        };

        // 主体字段；cells[0..3] 依次是 Factor、ReplaceFlag、EffBegDate、EffEndDate 的原文。
        public static Dictionary<string, object> Item(int sequence, string invCode, string[] cells)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["sequence"] = sequence;
            item["inv_code"] = invCode ?? "";
            item["factor"] = Number(cells[0]);
            item["replace_flag"] = (cells[1] ?? "").Trim() == "1";
            item["eff_begin"] = Date(cells[2]);
            item["eff_end"] = Date(cells[3]);
            return item;
        }

        // defineOf：列号（"22"…"37"）→ 原文。有值的写进 item["defines"]，没有就不加这个键。
        public static void AddDefines(Dictionary<string, object> item, Func<string, string> defineOf)
        {
            Dictionary<string, object> defines = new Dictionary<string, object>();
            for (int i = 0; i < Defines.Length; i++)
            {
                object value = DefineValue(defineOf(Defines[i][0]), Defines[i][1]);
                if (value != null)
                {
                    defines["define" + Defines[i][0]] = value;
                }
            }
            if (defines.Count > 0)
            {
                item["defines"] = defines;
            }
        }

        internal static bool Missing(string text)
        {
            return text == null || text.Length == 0 || text == NullMark;
        }

        internal static object DefineValue(string text, string type)
        {
            if (Missing(text))
            {
                return null;
            }
            if (type == "f")
            {
                double d;
                return double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out d) ? (object)d : text;
            }
            if (type == "i")
            {
                int n;
                return int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) ? (object)n : text;
            }
            return type == "d" ? DateTimeText(text) : text;
        }

        internal static object Number(string text)
        {
            decimal d;
            if (Missing(text) || !decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out d))
            {
                return null;
            }
            return d;
        }

        // 日期：yyyy-MM-dd（时分秒舍去）。
        internal static string Date(string text)
        {
            return Missing(text) ? null : text.Substring(0, Math.Min(10, text.Length));
        }

        // 日期时间：时分秒都是 0 只给 yyyy-MM-dd，否则 yyyy-MM-dd HH:mm:ss。
        internal static string DateTimeText(string text)
        {
            if (Missing(text))
            {
                return null;
            }
            if (text.Length >= 19 && text.Substring(10).TrimEnd('0', '.', ':', ' ').Length > 0)
            {
                return text.Substring(0, 19);
            }
            return text.Substring(0, Math.Min(10, text.Length));
        }
    }
}
