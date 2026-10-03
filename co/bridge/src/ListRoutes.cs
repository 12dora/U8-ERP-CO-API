using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // vouchers/list 与 stock/current：只读 SQL，跑在读线程池上，只用 ctx.Conn，不碰 ctx.Session。
    // 分页用主键 keyset（after = 上一页的 next）。watermark 在查询前取，下一轮作为 changed_since 传回。
    internal static class ListRoutes
    {
        public const string VouchersOp = "vouchers/list";
        public const string StockOp = "stock/current";
        const string Prefix = "/u8co/v1/";
        const string WatermarkSql =
            "SELECT CONVERT(varchar(20), CONVERT(bigint, MIN_ACTIVE_ROWVERSION()) - 1) AS watermark";

        // 响应里转成 JSON 布尔的键；其余值保持字符串（数值、日期同 vouchers/load 的格式）。
        static readonly string[] BoolKeys = new string[]
        {
            "verified", "closed", "red", "wf", "prepay", "debit_side", "stopped",
            // 票据（ar_note / ap_note）：期初票据。
            "opening"
        };

        public static void Check(string op, Dictionary<string, object> body)
        {
            string name = OpName(op);
            if (name == VouchersOp)
            {
                ListArgs.Vouchers(body);
                return;
            }
            if (name == StockOp)
            {
                ListArgs.Stock(body);
                return;
            }
            throw new BridgeException(400, "bad_request", "未知的列表接口");
        }

        public static ApiResult Handle(WorkContext ctx, string op)
        {
            string name = OpName(op);
            Dictionary<string, object> body = ctx.Item == null ? null : ctx.Item.Body;
            if (name == VouchersOp)
            {
                return Vouchers(ctx, ListArgs.Vouchers(body));
            }
            if (name == StockOp)
            {
                return ListStock.Run(ctx, ListArgs.Stock(body));
            }
            throw new BridgeException(400, "bad_request", "未知的列表接口");
        }

        static string OpName(string op)
        {
            if (op != null && op.StartsWith(Prefix, StringComparison.Ordinal))
            {
                return op.Substring(Prefix.Length);
            }
            return op ?? "";
        }

        static ApiResult Vouchers(WorkContext ctx, ListArgs args)
        {
            object conn = ctx.Conn;
            string watermark = Watermark(conn);
            List<object> ps = new List<object>();
            string sql = ListSql.Vouchers(args, ps, ctx);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, ps.ToArray(), args.Limit + 1);
            string[] keys = args.KeysOnly ? ListSql.KeyOnlyKeys : ListSql.FullKeys(args.Kind);
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["type"] = args.Kind[ListKind.Name];
            return Page(result, rows, keys, args.Limit, watermark);
        }

        public static string Watermark(object conn)
        {
            string text = Rows.Scalar(conn, WatermarkSql, new object[0]);
            if (text == null)
            {
                throw new BridgeException(500, "internal", "读不到 MIN_ACTIVE_ROWVERSION");
            }
            return text.Trim();
        }

        // 多取的一行只用来判断还有没有下一页；next 是本页最后一行的 id，没有下一页为 null。
        public static ApiResult Page(Dictionary<string, object> result, List<Dictionary<string, object>> rows,
            string[] keys, int limit, string watermark)
        {
            int take = Math.Min(rows.Count, limit);
            List<object> items = new List<object>(take);
            object next = null;
            for (int i = 0; i < take; i++)
            {
                Dictionary<string, object> item = Shape(rows[i], keys);
                items.Add(item);
                next = item["id"];
            }
            if (rows.Count <= limit)
            {
                next = null;
            }
            result["items"] = items;
            result["next"] = next;
            result["watermark"] = watermark;
            return ApiResult.Ok(result);
        }

        // 键固定：查询里为 NULL 的列也给出 null，调用方不用判断键是否存在。
        static Dictionary<string, object> Shape(Dictionary<string, object> row, string[] keys)
        {
            Dictionary<string, object> item = new Dictionary<string, object>(keys.Length);
            for (int i = 0; i < keys.Length; i++)
            {
                object raw;
                row.TryGetValue(keys[i], out raw);
                item[keys[i]] = Value(keys[i], raw as string);
            }
            return item;
        }

        static object Value(string key, string text)
        {
            if (text == null)
            {
                return null;
            }
            if (key == "id")
            {
                int id;
                if (!int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out id))
                {
                    throw new BridgeException(500, "internal", "列表主键不是整数");
                }
                return id;
            }
            if (Array.IndexOf(BoolKeys, key) >= 0)
            {
                string flag = text.Trim();
                return flag.Length > 0 && flag != "0";
            }
            return text;
        }
    }
}
