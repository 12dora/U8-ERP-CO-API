using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 修改、关闭、生单、新增之后的统一回读。
    internal static class EditMsg
    {
        public static ApiResult Saved(WorkContext ctx, VoucherKind kind, int id, VoucherKind source, int sourceId)
        {
            if (ctx == null || kind == null)
            {
                throw new BridgeException(500, "internal", "单据类型配置无效");
            }
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = CoRows.HeadRow(conn, kind, id);
                if (row == null)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                int lines = CountLines(conn, kind, id);
                return ApiResult.Ok(Body(kind, id, row, lines, source, sourceId));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static int CountLines(object conn, VoucherKind kind, int id)
        {
            string sql = "select count(*) from " + CoRows.Ident(kind.BodyTable)
                + " where " + CoRows.Ident(kind.BodyFk) + "=?";
            return ParseCount(Rows.Scalar(conn, sql, new object[] { id }));
        }

        static int ParseCount(string text)
        {
            if (text == null)
            {
                throw new BridgeException(500, "internal", "无法统计明细行");
            }
            string raw = text.Trim();
            int n;
            if (int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) && n >= 0)
            {
                return n;
            }
            decimal qty;
            if (decimal.TryParse(raw, NumberStyles.Number, CultureInfo.InvariantCulture, out qty) && qty >= 0)
            {
                return (int)qty;
            }
            throw new BridgeException(500, "internal", "无法统计明细行");
        }

        static Dictionary<string, object> Body(
            VoucherKind kind, int id, Dictionary<string, object> row, int lines, VoucherKind source, int sourceId)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(row, "code");
            body["state"] = CoRows.StateOf(row);
            body["lines"] = lines;
            if (source != null)
            {
                body["source_type"] = source.Name ?? "";
                body["source_id"] = sourceId;
            }
            return body;
        }
    }
}
