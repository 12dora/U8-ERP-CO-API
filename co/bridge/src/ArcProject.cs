using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 项目档案（只读）：项目大类在 fitem，每个大类的项目在 fitemss<大类>。编码写成 "<项目大类>:<项目编码>"。
    // 只认 fitem.ctable 恰好是 fitemss<大类> 且表存在的大类；存货核算（ctable=inventory）这类借用别的档案的大类不在其中。
    // 表名由桥用校验过的大类（1–2 位字母数字）拼出，调用方的文本只进参数。这些表没有 rowversion。
    internal static class ArcProject
    {
        // 大类 2 位 + 冒号 + 项目编码 60 位（fitemss*.citemcode 是 nvarchar(60)）。
        internal const int MaxCode = 63;
        const int MaxItem = 60;
        // 不给 project_class 时一次 UNION ALL 的大类上限；超过时 400，请调用方按大类逐个列，不静默截断。
        const int MaxClasses = 100;
        const string ClassSql = "SELECT TOP (101) f.citem_class AS cls FROM fitem f"
            + " WHERE f.ctable = N'fitemss' + f.citem_class AND OBJECT_ID(N'dbo.' + f.ctable, N'U') IS NOT NULL";
        const string CodeExpr = "(u.cls + ':' + u.citemcode)";

        public static ApiResult Get(WorkContext ctx, ArcReq req)
        {
            string[] parts = Split(req.Code, "code");
            object conn = ctx.Conn;
            List<string> classes = Classes(conn, parts[0]);
            if (classes.Count == 0)
            {
                throw new BridgeException(404, "not_found", "项目大类不存在");
            }
            string sql = "SELECT h.* FROM " + Table(classes[0]) + " h WHERE h.citemcode=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { parts[1] });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            PermHook.Archive(ctx, classes[0] + ":" + parts[1]);
            Dictionary<string, object> body = ArcRead.Head(req);
            body["project_class"] = classes[0];
            body["fields"] = ArcReadRo.Fields(row, null);
            return ApiResult.Ok(body);
        }

        // 不给 project_class 时按全部大类 UNION ALL，按 "<大类>:<编码>" 整串排序翻页。
        public static ApiResult List(WorkContext ctx, ArcReq req)
        {
            object conn = ctx.Conn;
            List<string> classes = Classes(conn, req.ProjectClass);
            if (classes.Count == 0 && req.ProjectClass != null)
            {
                throw new BridgeException(404, "not_found", "项目大类不存在");
            }
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            if (classes.Count > 0)
            {
                List<object> args = new List<object>();
                string sql = ListSql(req, classes, args, ctx);
                rows = Rows.Query(conn, sql, args.ToArray(), req.Limit + 1);
            }
            return ApiResult.Ok(ArcReadRo.Page(req, rows, null));
        }

        static string ListSql(ArcReq req, List<string> classes, List<object> args, WorkContext ctx)
        {
            args.Add(req.Limit + 1);
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ").Append(CodeExpr).Append(" AS code, u.citemname AS name,");
            sql.Append(" u.citemccode AS class_code, u.cls AS project_class, u.bclose AS closed FROM (");
            for (int i = 0; i < classes.Count; i++)
            {
                if (i > 0)
                {
                    sql.Append(" UNION ALL ");
                }
                sql.Append("SELECT CAST(? AS nvarchar(2)) AS cls, citemcode, citemname, citemccode, bclose FROM ").Append(Table(classes[i]));
                args.Add(classes[i]);
            }
            sql.Append(") u WHERE 1=1");
            ArcRead.AddFilter(sql, args, " AND " + CodeExpr + " > ?", req.After);
            ArcRead.AddFilter(sql, args, " AND " + CodeExpr + " LIKE ? ESCAPE '\\'", ArcRead.Like(req.Prefix, false));
            ArcRead.AddFilter(sql, args, " AND u.citemname LIKE ? ESCAPE '\\'", ArcRead.Like(req.NameLike, true));
            // 数据权限：项目按（大类、编码）。
            PermHook.Where(sql, args, ctx, "u");
            sql.Append(" ORDER BY code");
            return sql.ToString();
        }

        // 可用的大类（按编码排序）；给了 cls 只查这一个。数据库里的值再按标识符规则校验一遍才拼表名。
        internal static List<string> Classes(object conn, string cls)
        {
            List<object> args = new List<object>();
            string sql = ClassSql;
            if (cls != null)
            {
                sql += " AND f.citem_class=?";
                args.Add(cls);
            }
            sql += " ORDER BY f.citem_class";
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, args.ToArray(), MaxClasses + 1);
            if (rows.Count > MaxClasses)
            {
                throw ArcReq.Bad("项目大类超过 100 个，请用 project_class 按大类分别列出", "project_class");
            }
            List<string> classes = new List<string>();
            for (int i = 0; i < rows.Count; i++)
            {
                string value = ArcRead.Cell(rows[i], "cls");
                if (value != null && IsClass(value))
                {
                    classes.Add(value);
                }
            }
            return classes;
        }

        internal static string Table(string cls)
        {
            if (!IsClass(cls))
            {
                throw new BridgeException(500, "internal", "标识符无效");
            }
            return "fitemss" + cls;
        }

        // project_class：1 到 2 位 ASCII 字母或数字。
        internal static string CheckClass(string text)
        {
            if (text == null || !IsClass(text))
            {
                throw ArcReq.Bad("project_class 必须是 1 到 2 位字母或数字", "project_class");
            }
            return text;
        }

        // "<项目大类>:<项目编码>" → { 大类, 项目编码 }。
        internal static string[] Split(string code, string label)
        {
            int at = code == null ? -1 : code.IndexOf(':');
            if (at < 1 || !IsClass(code.Substring(0, at)))
            {
                throw ArcReq.Bad(label + " 必须写成 <项目大类>:<项目编码>", FieldPath.Clean(label));
            }
            string item = code.Substring(at + 1);
            if (item.Length == 0 || item.Length > MaxItem || item.Trim().Length != item.Length)
            {
                throw ArcReq.Bad(label + " 的项目编码长度必须在 1 到 60 之间，前后不能有空格", FieldPath.Clean(label));
            }
            return new string[] { code.Substring(0, at), item };
        }

        internal static bool IsClass(string text)
        {
            if (text.Length < 1 || text.Length > 2)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                bool ok = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
                if (!ok)
                {
                    return false;
                }
            }
            return true;
        }
    }
}
