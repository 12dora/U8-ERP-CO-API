using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 受控 SQL 写入的档案列（收付款协议、自定义项档案和客户存货对照的修改）：标签、类型、长度和取值校验，以及 SQL 的值表达式。
    // 类型：t 文本、b bit、s smallint、i int、n decimal(18,0)、f 浮点、d 日期（yyyy-MM-dd）。
    // 校验在登录前做（400），规范后的值（去首尾空白，bit 写成 0 / 1，空串表示 NULL）放回 req.Fields；SQL 只用 ? 参数。
    internal sealed class ArcCol
    {
        const int FloatChars = 30;
        const int DecimalDigits = 18;
        // 双精度能无损往返的十进制有效数字个数。
        const int MaxSignificant = 15;

        public readonly string Tag;
        public readonly char Type;
        public readonly int Max;
        // true：空串清为 NULL（Blank 时清为空串）；false：必须非空。
        public readonly bool Clearable;
        // 文本列不存 NULL：空串就写空串，新增没给时也写空串（收付款协议的说明）。
        public bool Blank { get; private set; }

        public ArcCol(string tag, char type, int max, bool nullable)
        {
            Tag = tag;
            Type = type;
            Max = max;
            Clearable = nullable;
        }

        public static ArcCol Text(string tag, int max, bool nullable)
        {
            return new ArcCol(tag, 't', max, nullable);
        }

        // 可清空、从不存 NULL 的文本列。
        public static ArcCol Memo(string tag, int max)
        {
            ArcCol col = new ArcCol(tag, 't', max, true);
            col.Blank = true;
            return col;
        }

        // 新增时调用方没给的缺省值：不可空的数值列 0，Blank 文本空串，其余没有（不写）。
        public string Default()
        {
            if (Blank)
            {
                return "";
            }
            return Clearable || Type == 't' ? null : "0";
        }

        public static ArcCol Of(string tag, char type, bool nullable)
        {
            return new ArcCol(tag, type, 0, nullable);
        }

        public static ArcCol Find(ArcCol[] cols, string tag)
        {
            for (int i = 0; i < cols.Length; i++)
            {
                if (string.Equals(cols[i].Tag, tag, StringComparison.OrdinalIgnoreCase))
                {
                    return cols[i];
                }
            }
            return null;
        }

        // 登录前：fields 里每个标签必须在列表里、取值合法；规范写法放回 req.Fields。
        public static void CheckAll(ArcReq req, ArcCol[] cols)
        {
            IList<string> tags = new List<string>(req.Fields.Tags);
            for (int i = 0; i < tags.Count; i++)
            {
                ArcCol col = Find(cols, tags[i]);
                string path = FieldPath.Join("fields", tags[i]);
                if (col == null)
                {
                    throw ArcReq.Bad("档案 " + req.Kind.Name + " 不能设置字段 " + tags[i], path).WithHint(FieldPath.WritableHint);
                }
                req.Fields.Put(tags[i], col.Clean(req.Fields.Get(tags[i])));
            }
        }

        // 取值校验并规范化；空串且可空返回 ""。
        public string Clean(string raw)
        {
            string text = (raw ?? "").Trim();
            string path = FieldPath.Join("fields", Tag);
            if (text.Length == 0)
            {
                if (!Clearable)
                {
                    throw ArcReq.Bad("字段 " + Tag + " 不能为空", path);
                }
                return "";
            }
            if (Type == 't')
            {
                if (text.Length > Max)
                {
                    throw ArcReq.Bad("字段 " + Tag + " 最长 " + Max.ToString(CultureInfo.InvariantCulture) + " 个字符", path);
                }
                return text;
            }
            return Typed(Tag, text, Type);
        }

        // 非文本类型的取值（text 已去空白、非空）。
        internal static string Typed(string tag, string text, char type)
        {
            string path = FieldPath.Join("fields", tag);
            switch (type)
            {
                case 'b':
                    return Flag(tag, text, path);
                case 'f':
                    return Float(tag, text, path);
                case 'd':
                    return Date(tag, text, path);
                case 'n':
                    return Whole(tag, text, path, 999999999999999999L, "最多 18 位");
                case 's':
                    return Whole(tag, text, path, short.MaxValue, "在 -32768 到 32767 之间");
                default:
                    return Whole(tag, text, path, int.MaxValue, "在 32 位范围内");
            }
        }

        // 校验过的值 → SQL 参数：整型列给 int，其余给文本，空串给 NULL。
        public object Param(string clean)
        {
            if (clean.Length == 0)
            {
                return Blank ? "" : null;
            }
            if (Type == 'b' || Type == 's' || Type == 'i')
            {
                return int.Parse(clean, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
            }
            return clean;
        }

        // 值表达式：参数是文本的数值、日期列要转换。
        public string Expr()
        {
            switch (Type)
            {
                case 'n':
                    return "CONVERT(decimal(18,0), ?)";
                case 'f':
                    return "CONVERT(float, ?)";
                case 'd':
                    return "CAST(CONVERT(date, ?, 23) AS datetime)";
                default:
                    return "?";
            }
        }

        // 表里读出的值是否就是 clean（回读核对）：数值按数值比，日期取前 10 位，文本逐字。
        public bool Same(string clean, string stored)
        {
            string got = (stored ?? "").Trim();
            if (clean.Length == 0 || got.Length == 0)
            {
                return clean.Length == got.Length;
            }
            if (Type == 'd')
            {
                return got.Length >= clean.Length && string.Equals(got.Substring(0, clean.Length), clean, StringComparison.Ordinal);
            }
            if (Type == 'f')
            {
                return SameDouble(clean, got);
            }
            if (Type == 'n')
            {
                return SameNumber(clean, got);
            }
            return string.Equals(got, clean, StringComparison.Ordinal);
        }

        // float 列存的是双精度：两边按双精度读回再比（有效数字不超过 15 位时往返不变，见 Float）。
        static bool SameDouble(string a, string b)
        {
            double x;
            double y;
            return double.TryParse(a, NumberStyles.Float, CultureInfo.InvariantCulture, out x)
                && double.TryParse(b, NumberStyles.Float, CultureInfo.InvariantCulture, out y) && x == y;
        }

        static bool SameNumber(string a, string b)
        {
            decimal x;
            decimal y;
            return decimal.TryParse(a, NumberStyles.Float, CultureInfo.InvariantCulture, out x)
                && decimal.TryParse(b, NumberStyles.Float, CultureInfo.InvariantCulture, out y) && x == y;
        }

        static string Flag(string tag, string text, string path)
        {
            if (text == "1" || string.Equals(text, "true", StringComparison.OrdinalIgnoreCase))
            {
                return "1";
            }
            if (text == "0" || string.Equals(text, "false", StringComparison.OrdinalIgnoreCase))
            {
                return "0";
            }
            throw ArcReq.Bad("字段 " + tag + " 必须是布尔或 0 / 1", path);
        }

        static string Float(string tag, string text, string path)
        {
            decimal number = 0m;
            bool ok = text.Length <= FloatChars
                && decimal.TryParse(text, NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out number);
            if (!ok)
            {
                throw ArcReq.Bad("字段 " + tag + " 必须是十进制小数", path);
            }
            string plain = number.ToString(CultureInfo.InvariantCulture);
            if (Significant(plain) > MaxSignificant)
            {
                throw ArcReq.Bad("字段 " + tag + " 最多 " + MaxSignificant.ToString(CultureInfo.InvariantCulture) + " 位有效数字（列是双精度）", path);
            }
            return plain;
        }

        // 有效数字个数：去掉符号、小数点、前导 0 和小数部分末尾的 0。
        static int Significant(string plain)
        {
            string digits = plain.TrimStart('-', '+').Replace(".", "");
            string trimmed = plain.IndexOf('.') >= 0 ? digits.TrimEnd('0') : digits;
            return trimmed.TrimStart('0').Length;
        }

        static string Whole(string tag, string text, string path, long limit, string range)
        {
            long whole = 0;
            bool ok = text.Length <= DecimalDigits + 1
                && long.TryParse(text, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)
                && whole <= limit && whole >= -limit - 1;
            if (!ok)
            {
                throw ArcReq.Bad("字段 " + tag + " 必须是整数，" + range, path);
            }
            return whole.ToString(CultureInfo.InvariantCulture);
        }

        static string Date(string tag, string text, string path)
        {
            DateTime day;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw ArcReq.Bad("字段 " + tag + " 必须是 yyyy-mm-dd 日期", path);
            }
            return text;
        }
    }
}
