using System;
using System.Globalization;

namespace U8Co
{
    // 期初结存 EAI 报文里单个字段的值（StockOpeningEai.Fill 调用，纯函数）：数字按不变格式解析、规整为 "0.##########"，
    // 不收布尔；日期只收 yyyy-MM-dd；其余为字符串、数字或布尔。空值返回 null（当作没传）。
    internal static class StockOpeningValue
    {
        const string NumFormat = "0.##########";

        // 数字标签（storeqc 标签名）：数量、单价、金额、件数、换算率、保质期。
        static readonly string[] Numbers = new string[] { "iquantity", "iunitcost", "iprice", "inum", "irate", "imassDate" };
        static readonly string[] Dates = new string[] { "dmadedate", "dvdate" };

        internal static string Text(string tag, object value, string field)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            if (Array.IndexOf(Numbers, tag) >= 0)
            {
                return Number(value, field);
            }
            string text = Plain(value, field);
            if (text == null)
            {
                return null;
            }
            return Array.IndexOf(Dates, tag) >= 0 ? Day(text, field) : text;
        }

        internal static string Format(decimal value)
        {
            return value.ToString(NumFormat, CultureInfo.InvariantCulture);
        }

        static string Number(object value, string field)
        {
            if (value is bool)
            {
                throw Bad(field, "必须是数字");
            }
            string text = value is string ? ((string)value).Trim() : Plain(value, field);
            if (text == null || text.Length == 0)
            {
                return null;
            }
            decimal number;
            if (!decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out number))
            {
                throw Bad(field, "必须是数字");
            }
            return Format(number);
        }

        static string Day(string text, string field)
        {
            DateTime day;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw Bad(field, "必须是 yyyy-MM-dd 格式的日期");
            }
            return text;
        }

        // 字符串去掉首尾空白（空串为 null）；数字按不变格式；布尔为 1 / 0；其他类型 400。
        static string Plain(object value, string field)
        {
            if (value is string)
            {
                string text = ((string)value).Trim();
                return text.Length == 0 ? null : text;
            }
            if (value is bool)
            {
                return ((bool)value) ? "1" : "0";
            }
            if (IsNumber(value))
            {
                return Convert.ToString(value, CultureInfo.InvariantCulture);
            }
            throw BridgeException.BadField(field, "字段只能是字符串、数字或布尔");
        }

        static bool IsNumber(object value)
        {
            return value is int || value is long || value is short || value is decimal || value is double || value is float;
        }

        static BridgeException Bad(string field, string text)
        {
            int dot = field.LastIndexOf('.');
            return BridgeException.BadField(field, (dot < 0 ? field : field.Substring(dot + 1)) + " " + text);
        }
    }
}
