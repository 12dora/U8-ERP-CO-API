using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // vouchers/list 的 SQL。标识符和表达式只来自 ListKind；调用方的值只进 ? 参数。
    internal static class ListSql
    {
        // 文本筛选：键、列、比较片段。
        static readonly string[] TextKeys = new string[]
        {
            "code", "code_from", "code_to", "cus_code", "ven_code", "wh_code", "dep_code", "person_code", "maker"
        };
        static readonly int[] TextCols = new int[]
        {
            ListKind.Code, ListKind.Code, ListKind.Code, ListKind.Cus, ListKind.Ven, ListKind.Wh, ListKind.Dep,
            ListKind.Person, ListKind.Maker
        };
        static readonly string[] TextOps = new string[]
        {
            " = ?", " >= ?", " <= ?", " = ?", " = ?", " = ?", " = ?", " = ?", " = ?"
        };
        static readonly string[] DateKeys = new string[] { "date_from", "date_to" };
        static readonly string[] DateOps = new string[]
        {
            " >= CONVERT(date, ?, 23)", " < DATEADD(day, 1, CONVERT(date, ?, 23))"
        };
        static readonly string[] FlagKeys = new string[] { "verified", "closed", "red" };

        // 公共列：响应键与 ListKind 列一一对应（id、verified、closed、red、ufts 另算）。
        static readonly string[] CommonKeys = new string[]
        {
            "code", "doc_date", "cus_code", "ven_code", "wh_code", "dep_code", "person_code", "maker",
            "verifier", "verified_at", "closer"
        };
        static readonly int[] CommonCols = new int[]
        {
            ListKind.Code, ListKind.Date, ListKind.Cus, ListKind.Ven, ListKind.Wh, ListKind.Dep, ListKind.Person,
            ListKind.Maker, ListKind.Verifier, ListKind.VerifiedAt, ListKind.Closer
        };

        public static readonly string[] KeyOnlyKeys = new string[] { "id", "code", "ufts" };

        public static string[] FullKeys(ListKind kind)
        {
            List<string> keys = new List<string>();
            keys.Add("id");
            keys.AddRange(CommonKeys);
            keys.Add("verified");
            keys.Add("closed");
            keys.Add("red");
            keys.Add("ufts");
            keys.AddRange(kind.ExtraKeys);
            return keys.ToArray();
        }

        // 校验 filter 对象；返回只含非 null 键的副本。该类型没有对应列的键 400。
        public static Dictionary<string, object> CheckFilters(ListKind kind, Dictionary<string, object> filter)
        {
            Dictionary<string, object> clean = new Dictionary<string, object>(StringComparer.Ordinal);
            if (filter == null)
            {
                return clean;
            }
            foreach (KeyValuePair<string, object> pair in filter)
            {
                if (pair.Value == null)
                {
                    continue;
                }
                clean[pair.Key] = CheckOne(kind, pair.Key, filter);
            }
            return clean;
        }

        static object CheckOne(ListKind kind, string key, Dictionary<string, object> filter)
        {
            string label = "filter." + key;
            int at = Array.IndexOf(TextKeys, key);
            if (at >= 0)
            {
                RequireColumn(kind.Has(TextCols[at]), key);
                return ListArgs.OptText(filter, key, label);
            }
            if (Array.IndexOf(DateKeys, key) >= 0)
            {
                return CheckDate(ListArgs.OptText(filter, key, label), label);
            }
            if (Array.IndexOf(FlagKeys, key) >= 0)
            {
                RequireColumn(FlagSql(kind, key) != null, key);
                return ListArgs.OptBool(filter, key, label);
            }
            throw new BridgeException(400, "bad_request", "未知筛选字段 " + ListArgs.Clip(key));
        }

        static void RequireColumn(bool present, string key)
        {
            if (!present)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持筛选字段 " + key);
            }
        }

        internal static string CheckDate(string text, string label)
        {
            DateTime day;
            if (text == null || text.Length != 10 || !DateTime.TryParseExact(text, "yyyy-MM-dd",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw new BridgeException(400, "bad_request", label + " 必须是 yyyy-MM-dd 日期");
            }
            return text;
        }

        static string FlagSql(ListKind kind, string key)
        {
            if (key == "verified")
            {
                return kind.VerifiedSql();
            }
            if (key == "closed")
            {
                return kind.ClosedSql();
            }
            return kind[ListKind.Red];
        }

        // SELECT TOP (?) … FROM head h [APPLY] WHERE [类型条件 AND] h.id > ? [筛选] [变更] ORDER BY h.id
        public static string Vouchers(ListArgs args, List<object> ps, WorkContext ctx)
        {
            ListKind kind = args.Kind;
            string id = "h." + kind[ListKind.Id];
            ps.Add(args.Limit + 1);
            StringBuilder sb = new StringBuilder("SELECT TOP (?) ");
            sb.Append(args.KeysOnly ? KeySelect(kind) : FullSelect(kind));
            sb.Append(" FROM ").Append(kind[ListKind.Head]).Append(" h").Append(ApplyOf(kind));
            sb.Append(" WHERE ");
            if (kind.Has(ListKind.Cond))
            {
                sb.Append(kind[ListKind.Cond]).Append(" AND ");
            }
            sb.Append(id).Append(" > ?");
            ps.Add(args.After);
            AppendFilters(sb, kind, args.Filters, ps);
            AppendSince(sb, kind, args.Since, ps);
            // 数据权限：受控对象的条件，表体对象按「表体为空或有一行放行」。
            PermHook.Where(sb, ps, ctx, "h");
            sb.Append(" ORDER BY ").Append(id);
            return sb.ToString();
        }

        static string KeySelect(ListKind kind)
        {
            return "h." + kind[ListKind.Id] + " AS id, " + kind[ListKind.Code] + " AS code, "
                + UftsSql(kind) + " AS ufts";
        }

        internal static string FullSelect(ListKind kind)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("h.").Append(kind[ListKind.Id]).Append(" AS id");
            for (int i = 0; i < CommonKeys.Length; i++)
            {
                sb.Append(", ").Append(kind[CommonCols[i]] ?? "NULL").Append(" AS ").Append(CommonKeys[i]);
            }
            sb.Append(", ").Append(kind.VerifiedSql()).Append(" AS verified");
            sb.Append(", ").Append(kind.ClosedSql() ?? "NULL").Append(" AS closed");
            sb.Append(", ").Append(kind[ListKind.Red] ?? "NULL").Append(" AS red");
            sb.Append(", ").Append(UftsSql(kind)).Append(" AS ufts");
            if (kind.Has(ListKind.Extra))
            {
                sb.Append(", ").Append(kind[ListKind.Extra]);
            }
            return sb.ToString();
        }

        // 表体有 rowversion 的类型取 max(表头, 表体)，下游只回写表体时也能看到变化。
        static string UftsSql(ListKind kind)
        {
            string head = "h." + kind[ListKind.Ufts];
            if (!kind.HasBodyUfts)
            {
                return "CONVERT(varchar(20), CONVERT(bigint, " + head + "))";
            }
            return "CONVERT(varchar(20), CONVERT(bigint, CASE WHEN b.bu > " + head + " THEN b.bu ELSE "
                + head + " END))";
        }

        internal static string ApplyOf(ListKind kind)
        {
            if (kind.Has(ListKind.Apply))
            {
                return kind[ListKind.Apply];
            }
            if (!kind.HasBodyUfts)
            {
                return "";
            }
            return " OUTER APPLY (SELECT MAX(d." + kind[ListKind.BodyUfts] + ") AS bu FROM " + kind[ListKind.Body]
                + " d WHERE d." + kind[ListKind.BodyFk] + " = h." + kind[ListKind.Id] + ") b";
        }

        internal static void AppendFilters(StringBuilder sb, ListKind kind, Dictionary<string, object> filters, List<object> ps)
        {
            for (int i = 0; i < TextKeys.Length; i++)
            {
                object value;
                if (filters.TryGetValue(TextKeys[i], out value))
                {
                    sb.Append(" AND ").Append(kind[TextCols[i]]).Append(TextOps[i]);
                    ps.Add(value);
                }
            }
            for (int i = 0; i < DateKeys.Length; i++)
            {
                object value;
                if (filters.TryGetValue(DateKeys[i], out value))
                {
                    sb.Append(" AND ").Append(kind[ListKind.Date]).Append(DateOps[i]);
                    ps.Add(value);
                }
            }
            for (int i = 0; i < FlagKeys.Length; i++)
            {
                object value;
                if (filters.TryGetValue(FlagKeys[i], out value))
                {
                    sb.Append(" AND ").Append(FlagWhere(FlagSql(kind, FlagKeys[i]), (bool)value));
                }
            }
        }

        // 红字列可能为 NULL，按 0 算。
        static string FlagWhere(string expr, bool wanted)
        {
            if (wanted)
            {
                return expr + " = 1";
            }
            return "ISNULL(" + expr + ", 0) = 0";
        }

        static void AppendSince(StringBuilder sb, ListKind kind, string since, List<object> ps)
        {
            if (since == null)
            {
                return;
            }
            const string Bin = " > CONVERT(binary(8), CONVERT(bigint, ?))";
            sb.Append(" AND (h.").Append(kind[ListKind.Ufts]).Append(Bin);
            ps.Add(since);
            if (kind.HasBodyUfts)
            {
                sb.Append(" OR EXISTS (SELECT 1 FROM ").Append(kind[ListKind.Body]).Append(" d2 WHERE d2.")
                    .Append(kind[ListKind.BodyFk]).Append(" = h.").Append(kind[ListKind.Id]).Append(" AND d2.")
                    .Append(kind[ListKind.BodyUfts]).Append(Bin).Append(")");
                ps.Add(since);
            }
            sb.Append(")");
        }
    }
}
