using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 销售类报表共用的小函数：读数值、算单价、拼 IN 和日期条件。取值只进 ? 参数，列名只来自调用方的常量。
    internal static class SaCalc
    {
        // 读出来的单元格都是字符串（Rows 的约定）；读不出按 0。不做舍入，由调用方决定位数。
        internal static decimal Dec(Dictionary<string, object> row, string name)
        {
            decimal value;
            decimal.TryParse(GlSql.Col(row, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value);
            return value;
        }

        internal static decimal Money(decimal value)
        {
            return decimal.Round(value, 2, MidpointRounding.AwayFromZero);
        }

        // 金额除以数量，六位小数；数量为 0 时没有单价（null）。
        internal static object Ratio(decimal amount, decimal qty)
        {
            if (qty == 0m)
            {
                return null;
            }
            return decimal.Round(amount / qty, 6, MidpointRounding.AwayFromZero);
        }

        // " AND 列 IN (?, ?, …)"；values 为空不加条件。
        internal static void In(StringBuilder sb, List<object> ps, string column, string[] values)
        {
            if (values == null || values.Length == 0)
            {
                return;
            }
            sb.Append(" AND ").Append(column).Append(" IN (");
            for (int i = 0; i < values.Length; i++)
            {
                sb.Append(i == 0 ? "?" : ", ?");
                ps.Add(values[i]);
            }
            sb.Append(")");
        }

        // 日期列落在 [from, to] 内（含两端）；空串表示不限这一端。
        internal static void Between(StringBuilder sb, List<object> ps, string column, string from, string to)
        {
            if (from.Length > 0)
            {
                sb.Append(" AND ").Append(column).Append(" >= CONVERT(date, ?, 23)");
                ps.Add(from);
            }
            if (to.Length > 0)
            {
                sb.Append(" AND ").Append(column).Append(" < DATEADD(day, 1, CONVERT(date, ?, 23))");
                ps.Add(to);
            }
        }

        // 结果超过 limit 行时只留前 limit 行，返回是否截断。rows 是多取了一行（limit + 1）的查询结果。
        internal static bool Cut(List<Dictionary<string, object>> rows, int limit)
        {
            if (rows.Count <= limit)
            {
                return false;
            }
            rows.RemoveRange(limit, rows.Count - limit);
            return true;
        }

        // 分页响应的公共部分：items、count、truncated。
        internal static Dictionary<string, object> Body(List<object> items, bool truncated)
        {
            Dictionary<string, object> body = Reports.Body();
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = truncated;
            return body;
        }
    }
}
