using System;
using System.Globalization;

namespace U8Co
{
    // 核对字段数值列的取值范围（保存前、事务之外）：列表示不了的值在 Save 之前 400（field 为字段路径），
    // 不留到 Save 之后补写时才报 SQL 错误（那时已在事务里，只能 500）。整数类型按类型范围且不能带小数；
    // decimal / numeric 按 NUMERIC_PRECISION、NUMERIC_SCALE（小数位不超过 scale、整数位不超过 precision − scale）；
    // money / smallmoney 按范围、最多 4 位小数；float / real 只要能按十进制解析（decimal 的范围小于 real）。
    // 只走组件计算的字段（Calc：cunitid、inum、cgatheringplan）不在这里收紧，由组件和保存后核对处理。
    internal sealed partial class SalePins
    {
        // 桥写的数值（非 Calc）解析后改写成该列类型能隐式转换的纯文本（Canonical）：写入时值按 nvarchar 参数绑定，
        // SQL Server 不认 '3.0' → int、'1E+01' → decimal，留着原文会在 Save 之后报 SQL 错误。
        // 非 Calc 只收十进制写法（可带符号、小数点、指数），千分位、括号负数、货币符号 400。
        static void CheckNumber(SalePin pin)
        {
            decimal n;
            // Calc 字段与计算路径同一解析（SoUnits.Dec，认指数，如件数 "1E+01"），值原样交给组件。
            bool ok = pin.Calc ? SoUnits.Dec(pin.Value, out n) : PlainDec(pin.Value, out n);
            if (!ok)
            {
                throw BridgeException.BadField(pin.Path, pin.Field + " 必须是数值");
            }
            if (pin.Calc)
            {
                return;
            }
            string bad = NumberProblem(pin.Col, n);
            if (bad.Length > 0)
            {
                throw BridgeException.BadField(pin.Path, pin.Field + " " + bad);
            }
            pin.Value = Canonical(pin.Col, n);
        }

        internal static bool PlainDec(string text, out decimal n)
        {
            return decimal.TryParse((text ?? "").Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out n);
        }

        // 纯函数（--selftest 用）：整数类型写成整数文本（"3.000"、"1E+01" → "3"、"10"），其余写成不带指数、去尾零的十进制文本。
        internal static string Canonical(SaleCol col, decimal n)
        {
            if (IsWhole(col == null ? "" : col.Type))
            {
                return decimal.Truncate(n).ToString("0", CultureInfo.InvariantCulture);
            }
            return n.ToString("0.############################", CultureInfo.InvariantCulture);
        }

        static bool IsWhole(string type)
        {
            for (int i = 0; i < Bounds.Length; i++)
            {
                if (Bounds[i][0] == type && Bounds[i][3] == "0")
                {
                    return true;
                }
            }
            return false;
        }

        // {类型, 最小值, 最大值, 最多小数位}：整数类型 0 位，money / smallmoney 4 位。
        static readonly string[][] Bounds = new string[][] {
            new string[] { "tinyint", "0", "255", "0" },
            new string[] { "smallint", "-32768", "32767", "0" },
            new string[] { "int", "-2147483648", "2147483647", "0" },
            new string[] { "bigint", "-9223372036854775808", "9223372036854775807", "0" },
            new string[] { "money", "-922337203685477.5808", "922337203685477.5807", "4" },
            new string[] { "smallmoney", "-214748.3648", "214748.3647", "4" }
        };

        // 纯函数（--selftest 用）：列表示不了 n 时返回说明，否则空串。
        internal static string NumberProblem(SaleCol col, decimal n)
        {
            if (col == null)
            {
                return "";
            }
            if (col.Type == "decimal" || col.Type == "numeric")
            {
                return Fixed(n, col.Prec, col.Scale, col.Type);
            }
            for (int i = 0; i < Bounds.Length; i++)
            {
                if (Bounds[i][0] == col.Type)
                {
                    return Bounded(n, Bounds[i]);
                }
            }
            return "";
        }

        static string Bounded(decimal n, string[] b)
        {
            int scale = int.Parse(b[3], NumberStyles.None, CultureInfo.InvariantCulture);
            if (Decimals(n) > scale)
            {
                return scale == 0 ? "必须是整数（" + b[0] + " 列）" : "最多 " + b[3] + " 位小数（" + b[0] + " 列）";
            }
            decimal min = decimal.Parse(b[1], NumberStyles.Number, CultureInfo.InvariantCulture);
            decimal max = decimal.Parse(b[2], NumberStyles.Number, CultureInfo.InvariantCulture);
            return n < min || n > max ? "超出 " + b[0] + " 列的范围 " + b[1] + " 到 " + b[2] : "";
        }

        // decimal(p, s)：小数位不超过 s，整数位不超过 p − s。精度读不到（0）时不查。
        static string Fixed(decimal n, int prec, int scale, string type)
        {
            if (prec <= 0)
            {
                return "";
            }
            string shown = type + "(" + prec.ToString(CultureInfo.InvariantCulture) + ","
                + scale.ToString(CultureInfo.InvariantCulture) + ")";
            if (Decimals(n) > scale)
            {
                return "最多 " + scale.ToString(CultureInfo.InvariantCulture) + " 位小数（" + shown + " 列）";
            }
            return IntDigits(n) > prec - scale ? "超出 " + shown + " 列的范围" : "";
        }

        // 去尾零后的小数位数。
        internal static int Decimals(decimal n)
        {
            string text = Math.Abs(n).ToString(CultureInfo.InvariantCulture);
            int dot = text.IndexOf('.');
            return dot < 0 ? 0 : text.TrimEnd('0').Length - dot - 1;
        }

        // 整数部分的位数（0 为 0 位）。
        internal static int IntDigits(decimal n)
        {
            decimal whole = decimal.Truncate(Math.Abs(n));
            return whole == 0m ? 0 : whole.ToString(CultureInfo.InvariantCulture).Length;
        }
    }
}
