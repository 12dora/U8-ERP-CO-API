using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 档案读取：纯 SQL，只用 ctx.Conn（读线程没有 Session）。表名列名来自 ArcKind。
    internal static class ArcRead
    {
        internal const string WatermarkSql = "SELECT CONVERT(varchar(20), CONVERT(bigint, MIN_ACTIVE_ROWVERSION()) - 1) AS watermark";

        public static ApiResult Get(WorkContext ctx, ArcReq req)
        {
            // 开户银行、项目可写，但 get 照旧按表列名返回（ArcKind.RoRead）。
            if (req.Kind.RsFile == null || req.Kind.RoRead)
            {
                return ArcReadRo.Get(ctx, req);
            }
            Dictionary<string, string> row = Row(ctx.Conn, req.Kind, req.Code);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            PermHook.Archive(ctx, req.Code);
            Dictionary<string, object> fields = new Dictionary<string, object>();
            IList<string> tags = req.Map.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string value;
                if (!row.TryGetValue(req.Map.Column(tags[i]), out value))
                {
                    continue;
                }
                fields[tags[i]] = value;
            }
            Dictionary<string, object> body = Head(req);
            body["fields"] = fields;
            return ApiResult.Ok(body);
        }

        public static ApiResult List(WorkContext ctx, ArcReq req)
        {
            // keys_only（ArcListExtra）对所有档案都只留 code、ufts。
            if (req.Kind.RsFile == null || req.Kind.RoRead)
            {
                return ArcListExtra.Keys(ArcReadRo.List(ctx, req), req);
            }
            object conn = ctx.Conn;
            string watermark = Rows.Scalar(conn, WatermarkSql, null);
            List<object> args = new List<object>();
            string sql = ListSql(req, args, ctx);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, args.ToArray(), req.Limit + 1);
            List<object> items = new List<object>();
            string next = null;
            for (int i = 0; i < rows.Count && i < req.Limit; i++)
            {
                items.Add(Item(req.Kind, rows[i]));
            }
            if (rows.Count > req.Limit)
            {
                next = Cell(rows[req.Limit - 1], "code");
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["archive"] = req.Kind.Name;
            body["items"] = items;
            body["next"] = next;
            body["watermark"] = watermark;
            return ArcListExtra.Keys(ApiResult.Ok(body), req);
        }

        // 主表一行合并第二张表（同名列以主表为准），列名不分大小写；没有返回 null。值里没有 NULL 列。
        // 两列主键的档案按编码的两段定位（ArcPair.KeyWhere）。
        internal static Dictionary<string, string> Row(object conn, ArcKind kind, string code)
        {
            Dictionary<string, object> main = Rows.One(conn, "SELECT * FROM " + kind.Table + " WHERE " + ArcPair.KeyWhere(kind),
                ArcPair.KeyArgs(kind, code));
            if (main == null)
            {
                return null;
            }
            Dictionary<string, string> row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            Merge(row, main);
            if (kind.Sub != null)
            {
                Merge(row, Rows.One(conn, "SELECT * FROM " + kind.Sub + " WHERE " + kind.SubKey + "=?", new object[] { code }));
            }
            return row;
        }

        internal static bool Exists(object conn, ArcKind kind, string code)
        {
            string sql = "SELECT " + kind.Key + " FROM " + kind.Table + " WHERE " + ArcPair.KeyWhere(kind);
            return Rows.One(conn, sql, ArcPair.KeyArgs(kind, code)) != null;
        }

        internal static Dictionary<string, object> Head(ArcReq req)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["archive"] = req.Kind.Name;
            body["code"] = req.Code;
            return body;
        }

        static void Merge(Dictionary<string, string> row, Dictionary<string, object> part)
        {
            if (part == null)
            {
                return;
            }
            foreach (KeyValuePair<string, object> pair in part)
            {
                string text = pair.Value as string;
                if (text != null && !row.ContainsKey(pair.Key))
                {
                    row[pair.Key] = text;
                }
            }
        }

        static string ListSql(ArcReq req, List<object> args, WorkContext ctx)
        {
            ArcKind k = req.Kind;
            string ts = TsExpr(k);
            StringBuilder sql = new StringBuilder("SELECT TOP (?) h.");
            args.Add(req.Limit + 1);
            sql.Append(k.Key).Append(" AS code, h.").Append(k.NameCol).Append(" AS name");
            if (k.ClassCol != null)
            {
                sql.Append(", h.").Append(k.ClassCol).Append(" AS class_code");
            }
            ArcListExtra.AppendSelect(sql, req);
            sql.Append(", CONVERT(varchar(20), CONVERT(bigint, ").Append(ts).Append(")) AS ufts FROM ").Append(k.Table).Append(" h");
            if (k.SubTs != null)
            {
                sql.Append(" LEFT JOIN ").Append(k.Sub).Append(" s ON s.").Append(k.SubKey).Append(" = h.").Append(k.Key);
            }
            sql.Append(" WHERE 1=1");
            AddFilter(sql, args, " AND h." + k.Key + " > ?", req.After);
            AddFilter(sql, args, " AND h." + k.Key + " LIKE ? ESCAPE '\\'", Like(req.Prefix, false));
            AddFilter(sql, args, " AND h." + k.NameCol + " LIKE ? ESCAPE '\\'", Like(req.NameLike, true));
            AddFilter(sql, args, " AND " + ts + " > CONVERT(binary(8), CONVERT(bigint, ?))", req.Since);
            // 数据权限。
            PermHook.Where(sql, args, ctx, "h");
            sql.Append(" ORDER BY h.").Append(k.Key);
            return sql.ToString();
        }

        // 人员的变化分在 hr_hi_person 和 Person 两张表，取两者较大的 rowversion。
        static string TsExpr(ArcKind k)
        {
            if (k.SubTs == null)
            {
                return "h." + k.Ts;
            }
            return "(CASE WHEN s." + k.SubTs + " > h." + k.Ts + " THEN s." + k.SubTs + " ELSE h." + k.Ts + " END)";
        }

        internal static void AddFilter(StringBuilder sql, List<object> args, string clause, string value)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            sql.Append(clause);
            args.Add(value);
        }

        internal static string Like(string text, bool contains)
        {
            if (text == null || text.Length == 0)
            {
                return null;
            }
            StringBuilder sb = new StringBuilder();
            if (contains)
            {
                sb.Append('%');
            }
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '\\' || c == '%' || c == '_' || c == '[')
                {
                    sb.Append('\\');
                }
                sb.Append(c);
            }
            return sb.Append('%').ToString();
        }

        static Dictionary<string, object> Item(ArcKind kind, Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = Cell(row, "code");
            item["name"] = Cell(row, "name");
            if (kind.ClassCol != null)
            {
                item["class_code"] = Cell(row, "class_code");
            }
            ArcListExtra.PutEnd(item, kind, row);
            ArcListExtra.PutFlags(item, kind, row);
            item["ufts"] = Cell(row, "ufts");
            return item;
        }

        internal static string Cell(Dictionary<string, object> row, string name)
        {
            object value;
            if (row == null || !row.TryGetValue(name, out value))
            {
                return null;
            }
            return value as string;
        }
    }
}
