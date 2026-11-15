using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // desc 游标上的一行：单据日期（null 表示该行日期为 NULL）和依次排在日期后面的整数键（表头 id，订单执行另有行 id）。
    internal sealed class DescCursor
    {
        public string Date;
        public int[] Ids;
    }

    // vouchers/search 与 reports/order_execution 的 order: "desc"（单据日期降序、主键降序）。
    // 分页是 keyset：游标记住上一页最后一行的（单据日期，主键…），下一页取排在它之后的行，不用 OFFSET，也不重读前面的页。
    // 排序 ORDER BY 日期 DESC, 主键 DESC；续读条件 (日期 <= D AND (日期 < D OR 主键 < ID)) OR 日期 IS NULL，
    // 与「日期 < D OR 日期 IS NULL OR (日期 = D AND 主键 < ID)」是同一个全序（订单执行的主键是（表头 id，行 id），按字典序再展开一层）。
    // 写成 日期 <= D 加上其余条件，是为了给优化器一个可用日期索引的区间条件；是否 seek 由优化器决定，
    // 没有日期索引时每页要扫一遍筛选后的集合再取前 N 行。
    // 日期为 NULL 的行排在最后（SQL Server 的 NULL 最小），所以游标日期非 NULL 时 NULL 日期的行仍在其后；游标日期是 NULL 时只剩 NULL 日期的行。
    // 日期按 datetime 全精度比较，游标里的日期文本是 CONVERT(varchar(23), CONVERT(datetime, 列), 121)，来回不丢精度；
    // 游标日期必须在 datetime 的范围内（1753-01-01 到 9999-12-31 23:59:59.997），否则 400。
    // 游标是 Reports.Cursor 的不透明文本（日期文本、各整数键，用 \u001f 分隔后 base64url）。
    internal static class DescPaging
    {
        const string DateFormat = "yyyy-MM-dd HH:mm:ss.fff";
        static readonly DateTime MinDate = new DateTime(1753, 1, 1);
        static readonly DateTime MaxDate = new DateTime(9999, 12, 31, 23, 59, 59, 997);

        // order 字段：缺省或 "asc" 为升序（返回 false），"desc" 为降序（返回 true），其余 400。
        public static bool ParseOrder(Dictionary<string, object> body)
        {
            object raw = GlReq.Field(body, "order");
            if (raw == null)
            {
                return false;
            }
            string text = raw as string;
            if (text == "asc")
            {
                return false;
            }
            if (text == "desc")
            {
                return true;
            }
            throw BridgeException.BadField("order", "order 只能是 asc 或 desc");
        }

        // 游标里的日期文本：和排序用的日期同一个表达式。
        public static string DateSql(string dateExpr)
        {
            return "CONVERT(varchar(23), CONVERT(datetime, " + dateExpr + "), 121)";
        }

        public static string Encode(string date, params int[] ids)
        {
            string[] parts = new string[ids.Length + 1];
            parts[0] = date ?? "";
            for (int i = 0; i < ids.Length; i++)
            {
                parts[i + 1] = ids[i].ToString(CultureInfo.InvariantCulture);
            }
            return Reports.Cursor(parts);
        }

        // 没给游标返回 null；格式不对 400（field 为 after）。idCount 是日期后面的整数键个数。
        public static DescCursor Parse(string text, int idCount)
        {
            string[] parts;
            try
            {
                parts = Reports.Uncursor(text, idCount + 1);
            }
            catch (BridgeException ex)
            {
                throw ex.WithField("after");
            }
            if (parts == null)
            {
                return null;
            }
            DescCursor cursor = new DescCursor();
            cursor.Ids = new int[idCount];
            cursor.Date = parts[0].Length == 0 ? null : parts[0];
            if (cursor.Date != null && !ValidDate(cursor.Date))
            {
                throw Invalid();
            }
            for (int i = 0; i < idCount; i++)
            {
                int id;
                if (!int.TryParse(parts[i + 1], NumberStyles.None, CultureInfo.InvariantCulture, out id) || id < 1)
                {
                    throw Invalid();
                }
                cursor.Ids[i] = id;
            }
            return cursor;
        }

        // 固定格式，且在 SQL Server datetime 的范围内。
        static bool ValidDate(string text)
        {
            DateTime parsed;
            if (!DateTime.TryParseExact(text, DateFormat, CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                return false;
            }
            return parsed >= MinDate && parsed <= MaxDate;
        }

        static BridgeException Invalid()
        {
            return BridgeException.BadField("after", "after 游标无效");
        }

        // 续读条件（不带前导 AND）；参数顺序就是占位符在文本里出现的顺序。
        public static void AppendAfter(StringBuilder sb, List<object> ps, string dateExpr, string[] keyExprs, DescCursor cursor)
        {
            sb.Append("(");
            if (cursor.Date == null)
            {
                sb.Append(dateExpr).Append(" IS NULL AND ");
                AppendKeys(sb, ps, keyExprs, cursor.Ids, 0);
            }
            else
            {
                sb.Append("(").Append(dateExpr).Append(" <= CONVERT(datetime, ?, 121) AND (").Append(dateExpr)
                    .Append(" < CONVERT(datetime, ?, 121) OR ");
                ps.Add(cursor.Date);
                ps.Add(cursor.Date);
                AppendKeys(sb, ps, keyExprs, cursor.Ids, 0);
                sb.Append(")) OR ").Append(dateExpr).Append(" IS NULL");
            }
            sb.Append(")");
        }

        // 主键按字典序比较：(k0 < ? OR (k0 = ? AND (k1 < ?)))，最后一个键只写 k < ?。
        static void AppendKeys(StringBuilder sb, List<object> ps, string[] keyExprs, int[] ids, int at)
        {
            if (at == keyExprs.Length - 1)
            {
                sb.Append(keyExprs[at]).Append(" < ?");
                ps.Add(ids[at]);
                return;
            }
            sb.Append("(").Append(keyExprs[at]).Append(" < ? OR (").Append(keyExprs[at]).Append(" = ? AND ");
            ps.Add(ids[at]);
            ps.Add(ids[at]);
            AppendKeys(sb, ps, keyExprs, ids, at + 1);
            sb.Append("))");
        }

        public static void AppendOrderBy(StringBuilder sb, string dateExpr, string[] keyExprs)
        {
            sb.Append(" ORDER BY ").Append(dateExpr).Append(" DESC");
            for (int i = 0; i < keyExprs.Length; i++)
            {
                sb.Append(", ").Append(keyExprs[i]).Append(" DESC");
            }
        }
    }
}
