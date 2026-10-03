using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 只读档案（ArcKindRo）的 get / list：纯 SQL，只用 ctx.Conn。表名列名只来自 ArcKind。
    // get 的 fields 用表列名（没有 RsXml），去掉 NULL 列、口令类列（Rows.Hidden）和原始 rowversion；ufts 另给十进制。
    // 两列主键的档案（客户收货地址、自定义项、客户存货对照）走 ArcPair，汇率走 ArcExch，固定资产卡片走 ArcFa，操作员、角色走 ArcUa。
    internal static class ArcReadRo
    {
        internal const string UftsAlias = "co_ufts";

        public static ApiResult Get(WorkContext ctx, ArcReq req)
        {
            if (ArcKindRo.IsProject(req.Kind))
            {
                return ArcProject.Get(ctx, req);
            }
            if (ArcPair.Of(req.Kind) != null)
            {
                return ArcPair.Get(ctx, req);
            }
            if (ArcExch.Is(req.Kind))
            {
                return ArcExch.Get(ctx, req);
            }
            if (ArcFa.Is(req.Kind))
            {
                return ArcFa.Get(ctx, req);
            }
            if (ArcUa.Is(req.Kind))
            {
                return ArcUa.Get(ctx, req);
            }
            ArcKind k = req.Kind;
            List<object> args = new List<object>();
            StringBuilder sql = new StringBuilder("SELECT h.*");
            if (k.Ts != null)
            {
                sql.Append(", CONVERT(varchar(20), CONVERT(bigint, h.").Append(k.Ts).Append(")) AS ").Append(UftsAlias);
            }
            sql.Append(" FROM ").Append(k.Table).Append(" h WHERE h.").Append(k.Key).Append("=?");
            args.Add(req.Code);
            int year = AddYear(ctx, k, sql, args);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql.ToString(), args.ToArray());
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            PermHook.Archive(ctx, req.Code);
            Dictionary<string, object> body = ArcRead.Head(req);
            body["fields"] = Fields(row, k.Ts);
            if (k.Ts != null)
            {
                body["ufts"] = ArcRead.Cell(row, UftsAlias);
            }
            PutYear(body, year);
            return ApiResult.Ok(body);
        }

        public static ApiResult List(WorkContext ctx, ArcReq req)
        {
            if (ArcKindRo.IsProject(req.Kind))
            {
                return ArcProject.List(ctx, req);
            }
            if (ArcPair.Of(req.Kind) != null)
            {
                return ArcPair.List(ctx, req);
            }
            if (ArcExch.Is(req.Kind))
            {
                return ArcExch.List(ctx, req);
            }
            if (ArcFa.Is(req.Kind))
            {
                return ArcFa.List(ctx, req);
            }
            if (ArcUa.Is(req.Kind))
            {
                return ArcUa.List(ctx, req);
            }
            ArcKind k = req.Kind;
            object conn = ctx.Conn;
            // 没有 rowversion 的表没有水位。
            string watermark = k.Ts == null ? null : Rows.Scalar(conn, ArcRead.WatermarkSql, null);
            List<object> args = new List<object>();
            args.Add(req.Limit + 1);
            StringBuilder sql = new StringBuilder("SELECT TOP (?) h.");
            sql.Append(k.Key).Append(" AS code, h.").Append(k.NameCol).Append(" AS name");
            if (k.ClassCol != null)
            {
                sql.Append(", h.").Append(k.ClassCol).Append(" AS class_code");
            }
            ArcListExtra.AppendFlags(sql, req);
            if (k.Ts != null)
            {
                sql.Append(", CONVERT(varchar(20), CONVERT(bigint, h.").Append(k.Ts).Append(")) AS ufts");
            }
            sql.Append(" FROM ").Append(k.Table).Append(" h WHERE 1=1");
            int year = AddYear(ctx, k, sql, args);
            ArcRead.AddFilter(sql, args, " AND h." + k.Key + " > ?", req.After);
            ArcRead.AddFilter(sql, args, " AND h." + k.Key + " LIKE ? ESCAPE '\\'", ArcRead.Like(req.Prefix, false));
            ArcRead.AddFilter(sql, args, " AND h." + k.NameCol + " LIKE ? ESCAPE '\\'", ArcRead.Like(req.NameLike, true));
            if (k.Ts != null)
            {
                ArcRead.AddFilter(sql, args, " AND h." + k.Ts + " > CONVERT(binary(8), CONVERT(bigint, ?))", req.Since);
            }
            // 数据权限：凭证类别按 dsign，收发类别、采购类型、销售类型按各自编码；科目档案不过滤。
            PermHook.Where(sql, args, ctx, "h");
            sql.Append(" ORDER BY h.").Append(k.Key);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql.ToString(), args.ToArray(), req.Limit + 1);
            Dictionary<string, object> body = Page(req, rows, watermark);
            PutYear(body, year);
            return ApiResult.Ok(body);
        }

        // 一页列表：多读的一行只用来判断有没有下一页。项目另带 project_class 和 closed。
        internal static Dictionary<string, object> Page(ArcReq req, List<Dictionary<string, object>> rows, string watermark)
        {
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < req.Limit; i++)
            {
                items.Add(Item(req.Kind, rows[i]));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["archive"] = req.Kind.Name;
            body["items"] = items;
            body["next"] = rows.Count > req.Limit ? ArcRead.Cell(rows[req.Limit - 1], "code") : null;
            body["watermark"] = watermark;
            return body;
        }

        // 列名 → 值，保持查询顺序；原始 rowversion（十六进制）不返回。
        internal static Dictionary<string, object> Fields(Dictionary<string, object> row, string ts)
        {
            Dictionary<string, object> fields = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, UftsAlias, StringComparison.OrdinalIgnoreCase)
                    || (ts != null && string.Equals(pair.Key, ts, StringComparison.OrdinalIgnoreCase)))
                {
                    continue;
                }
                fields[pair.Key] = pair.Value;
            }
            return fields;
        }

        static Dictionary<string, object> Item(ArcKind kind, Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = ArcRead.Cell(row, "code");
            item["name"] = ArcRead.Cell(row, "name");
            if (kind.ClassCol != null)
            {
                item["class_code"] = ArcRead.Cell(row, "class_code");
            }
            if (ArcKindRo.IsProject(kind))
            {
                item["project_class"] = ArcRead.Cell(row, "project_class");
                item["closed"] = ArcRead.Cell(row, "closed") == "1";
            }
            ArcListExtra.PutFlags(item, kind, row);
            item["ufts"] = ArcRead.Cell(row, "ufts");
            return item;
        }

        // 科目按登录日期的年份（会计年度）过滤；请求的 year 是账套库年度，不用。
        static int AddYear(WorkContext ctx, ArcKind k, StringBuilder sql, List<object> args)
        {
            if (k.YearCol == null)
            {
                return 0;
            }
            int year = GlState.LoginYear(ctx);
            sql.Append(" AND h.").Append(k.YearCol).Append("=?");
            args.Add(year);
            return year;
        }

        static void PutYear(Dictionary<string, object> body, int year)
        {
            if (year > 0)
            {
                body["fiscal_year"] = year;
            }
        }
    }
}
