using System;
using System.Globalization;

namespace U8Co
{
    // 预演 docs 里单元格的 JSON 写法（纯函数，--selftest 覆盖）：
    // NULL 跳过；二进制、timestamp / rowversion（ufts）跳过；口令类列跳过（同 Rows.Hidden）；
    // 数值给 JSON 数（decimal 转 double，bit 给 1 / 0）；日期时间零点给 yyyy-MM-dd，否则 yyyy-MM-ddTHH:mm:ss；字符串去右侧空格。
    internal static class DryRunValue
    {
        const int AdBinary = 128;
        const int AdVarBinary = 204;
        const int AdLongVarBinary = 205;

        public static bool Keep(string name, int adoType)
        {
            if (name == null || name.Length == 0 || Rows.Hidden(name))
            {
                return false;
            }
            if (adoType == AdBinary || adoType == AdVarBinary || adoType == AdLongVarBinary)
            {
                return false;
            }
            return !string.Equals(name, "ufts", StringComparison.OrdinalIgnoreCase);
        }

        public static bool TryJson(object raw, out object value)
        {
            value = null;
            if (raw == null || raw is DBNull || raw is byte[])
            {
                return false;
            }
            string text = raw as string;
            if (text != null)
            {
                value = text.TrimEnd();
                return true;
            }
            if (raw is DateTime)
            {
                value = DateText((DateTime)raw);
                return true;
            }
            value = Number(raw);
            return true;
        }

        public static string DateText(DateTime dt)
        {
            string pattern = dt.TimeOfDay.Ticks == 0 ? "yyyy-MM-dd" : "yyyy-MM-ddTHH:mm:ss";
            return dt.ToString(pattern, CultureInfo.InvariantCulture);
        }

        static object Number(object raw)
        {
            TypeCode code = Convert.GetTypeCode(raw);
            if (code == TypeCode.Boolean)
            {
                return ((bool)raw) ? 1 : 0;
            }
            if (code == TypeCode.Decimal || code == TypeCode.Single || code == TypeCode.Double)
            {
                return Convert.ToDouble(raw, CultureInfo.InvariantCulture);
            }
            return Integer(raw, code);
        }

        static object Integer(object raw, TypeCode code)
        {
            if (code >= TypeCode.SByte && code <= TypeCode.Int32)
            {
                return Convert.ToInt32(raw, CultureInfo.InvariantCulture);
            }
            if (code >= TypeCode.UInt32 && code <= TypeCode.UInt64)
            {
                return Convert.ToInt64(raw, CultureInfo.InvariantCulture);
            }
            return Convert.ToString(raw, CultureInfo.InvariantCulture).TrimEnd();
        }
    }
}
