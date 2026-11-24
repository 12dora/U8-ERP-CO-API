using System;
using System.Globalization;

namespace U8Co
{
    // customer_fields 请求值的逐键规整与校验（按 ArcCusCol.Kind）：返回写入用的规范写法，不合规 400。
    // expect 的值同样走这里，但只核对不写入：库里的旧数据可以不满足写入规则（必填、字符集、区间、1900 年下限、小数位数 / 有效数字），
    // 这些对 expect 不适用，才能用 expect 把旧数据改成合规的值；格式（日期、数字、0/1、长度、控制字符）仍然校验。
    internal static class ArcCusValue
    {
        // 小数最多 8 位小数、15 位有效数字（Customer 的小数列是 float，再多回读对不上）。
        internal const int MaxFraction = 8;
        internal const int MaxDigits = 15;
        const string DateFormat = "yyyy-MM-dd";
        const string NumberFormat = "0.########";

        public static string Parse(ArcCusCol col, string text, string path, bool expect)
        {
            if (HasControl(text))
            {
                throw ArcReq.Bad(path + " 含有控制字符", path);
            }
            switch (col.Kind)
            {
                case ArcCusCol.KText:
                    return Text(col, text, path, expect);
                case ArcCusCol.KRef:
                    return Code(col, text, path, expect);
                case ArcCusCol.KDate:
                    return Date(text, path, expect);
                case ArcCusCol.KBit:
                    return Bit(text, path);
                default:
                    return Number(col, text, path, expect);
            }
        }

        // 数据库读出的值规整成与请求值相同的写法（小数去掉多余的 0）。
        public static string FromDb(ArcCusCol col, string raw)
        {
            if (col.Kind != ArcCusCol.KNum && col.Kind != ArcCusCol.KInt)
            {
                return raw;
            }
            decimal v;
            if (!decimal.TryParse(raw, NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
                CultureInfo.InvariantCulture, out v))
            {
                return raw;
            }
            return Canon(v, col.Kind == ArcCusCol.KInt);
        }

        // 前后空格去掉后不超过上限；required 时不能为空；限定字符集的（邮政编码）逐字符检查。
        static string Text(ArcCusCol col, string text, string path, bool expect)
        {
            string trimmed = text.Trim();
            if (trimmed.Length == 0 && col.Required && !expect)
            {
                throw ArcReq.Bad(path + " 不能为空", path);
            }
            if (trimmed.Length > col.Max)
            {
                throw ArcReq.Bad(path + " 长度不能超过 " + col.Max.ToString(CultureInfo.InvariantCulture), path);
            }
            if (col.Charset != null && !expect && !OnlyChars(trimmed, col.Charset))
            {
                throw ArcReq.Bad(path + " 含有不允许的字符：" + col.CharsetNote, path);
            }
            return trimmed;
        }

        // 引用的档案编码（是否存在、是否末级 / 停用在事务里查）：空串清空；非空不超过列宽、前后没有空格。
        static string Code(ArcCusCol col, string text, string path, bool expect)
        {
            if (text.Length > col.Max)
            {
                throw ArcReq.Bad(path + " 长度不能超过 " + col.Max.ToString(CultureInfo.InvariantCulture), path);
            }
            if (text.Trim().Length != text.Length)
            {
                throw ArcReq.Bad(path + " 前后不能有空格", path);
            }
            if (text.Length == 0 && col.Required && !expect)
            {
                throw ArcReq.Bad(path + " 不能为空", path);
            }
            return text;
        }

        // 日期：空串清空；否则严格 yyyy-MM-dd，且不早于 1900-01-01（datetime 列的合理下限）。
        static string Date(string text, string path, bool expect)
        {
            if (text.Length == 0)
            {
                return text;
            }
            DateTime day;
            if (!DateTime.TryParseExact(text, DateFormat, CultureInfo.InvariantCulture, DateTimeStyles.None, out day)
                || (day.Year < 1900 && !expect))
            {
                throw ArcReq.Bad(path + " 必须是 yyyy-MM-dd 日期或空串", path);
            }
            return text;
        }

        static string Bit(string text, string path)
        {
            if (text != "0" && text != "1")
            {
                throw ArcReq.Bad(path + " 必须是 0 或 1", path);
            }
            return text;
        }

        // 小数 / 整数：空串清空；不带指数、最多 8 位小数 15 位有效数字，整数列必须是 Int32 范围内的整数，有范围的列检查范围。
        static string Number(ArcCusCol col, string text, string path, bool expect)
        {
            string t = text.Trim();
            if (t.Length == 0)
            {
                return t;
            }
            decimal v = ParseDecimal(t, path, expect);
            bool whole = col.Kind == ArcCusCol.KInt;
            if (whole && (v != decimal.Truncate(v) || v < int.MinValue || v > int.MaxValue))
            {
                throw ArcReq.Bad(path + " 必须是整数", path);
            }
            if (col.HasRange && !expect)
            {
                CheckRange(col, v, path);
            }
            return Canon(v, whole);
        }

        static decimal ParseDecimal(string t, string path, bool expect)
        {
            decimal v;
            if (!decimal.TryParse(t, NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
                CultureInfo.InvariantCulture, out v))
            {
                throw ArcReq.Bad(path + " 必须是数字", path);
            }
            if (!expect && (Fraction(t) > MaxFraction || Significant(t) > MaxDigits))
            {
                throw ArcReq.Bad(path + " 最多 " + MaxFraction.ToString(CultureInfo.InvariantCulture) + " 位小数、"
                    + MaxDigits.ToString(CultureInfo.InvariantCulture) + " 位有效数字", path);
            }
            return v;
        }

        static void CheckRange(ArcCusCol col, decimal v, string path)
        {
            bool over = v > col.High || (col.HighOpen && v >= col.High);
            if (v < col.Low || over)
            {
                throw ArcReq.Bad(path + " 必须大于等于 " + Canon(col.Low, false) + " 且"
                    + (col.HighOpen ? "小于 " : "不大于 ") + Canon(col.High, false), path);
            }
        }

        // 规范写法：不带多余的 0、不带负零；整数不带小数点。
        internal static string Canon(decimal v, bool whole)
        {
            if (v == 0m)
            {
                v = 0m;
            }
            return whole ? decimal.Truncate(v).ToString(CultureInfo.InvariantCulture)
                : v.ToString(NumberFormat, CultureInfo.InvariantCulture);
        }

        // 小数点后的位数。
        internal static int Fraction(string text)
        {
            int dot = text.IndexOf('.');
            return dot < 0 ? 0 : text.Length - dot - 1;
        }

        // 有效数字个数（不计符号、小数点和前导 0）。
        internal static int Significant(string text)
        {
            string digits = text.Replace("-", "").Replace("+", "").Replace(".", "").TrimStart('0');
            return digits.Length;
        }

        static bool OnlyChars(string text, string set)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (set.IndexOf(text[i]) < 0)
                {
                    return false;
                }
            }
            return true;
        }

        static bool HasControl(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c < ' ' || c == '\x7f')
                {
                    return true;
                }
            }
            return false;
        }
    }
}
