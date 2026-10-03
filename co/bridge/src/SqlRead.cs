using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 采购发票和生产订单只走 SQL，不打开 CO。
    internal static class SqlRead
    {
        const int LineCap = 500;

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            Require(kind);
            Dictionary<string, object> head = ReadHead(ctx, kind, id);
            bool truncated;
            List<Dictionary<string, object>> lines = ReadLines(ctx, kind, id, out truncated);
            Dictionary<string, object> body = Pack(kind, id, head, lines, truncated);
            if (kind.Name == "production_order")
            {
                FillOrder(ctx, kind, id, body, lines);
            }
            else
            {
                Dictionary<string, object> state = PurchaseState(ctx, kind, id);
                ReviewState(state, head);
                body["state"] = state;
            }
            return ApiResult.Ok(body);
        }

        // 采购发票：verified 即采购复核（cVerifier / cAuditDate），另给 reviewed* 同义字段，
        // 以及应付款管理的审核人 ap_verifier（cPBVVerifier），两种状态分开看。
        static void ReviewState(Dictionary<string, object> state, Dictionary<string, object> head)
        {
            state["reviewed"] = state["verified"];
            state["reviewer"] = state["verifier"];
            state["reviewed_at"] = state["verified_at"];
            string ap = CoRows.Col(head, "cPBVVerifier");
            state["ap_verified"] = ap.Length > 0;
            state["ap_verifier"] = ap;
        }

        static void Require(VoucherKind kind)
        {
            if (kind != null && (kind.Name == "purchase_invoice" || kind.Name == "production_order"))
            {
                return;
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持读取");
        }

        static Dictionary<string, object> ReadHead(WorkContext ctx, VoucherKind kind, int id)
        {
            string sql = "SELECT * FROM " + CoRows.Ident(kind.HeadTable)
                + " WHERE " + CoRows.Ident(kind.IdColumn) + "=?";
            Dictionary<string, object> head = Rows.One(ctx.Conn, sql, new object[] { id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Strip(head);
            return head;
        }

        static List<Dictionary<string, object>> ReadLines(WorkContext ctx, VoucherKind kind, int id, out bool truncated)
        {
            string sql = "SELECT * FROM " + CoRows.Ident(kind.BodyTable)
                + " WHERE " + CoRows.Ident(kind.BodyFk)
                + "=? ORDER BY " + CoRows.Ident(kind.LineIdColumn);
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, sql, new object[] { id }, LineCap + 1);
            if (lines == null)
            {
                lines = new List<Dictionary<string, object>>();
            }
            truncated = lines.Count > LineCap;
            while (lines.Count > LineCap)
            {
                lines.RemoveAt(lines.Count - 1);
            }
            for (int i = 0; i < lines.Count; i++)
            {
                Strip(lines[i]);
            }
            return lines;
        }

        static Dictionary<string, object> PurchaseState(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> row = CoRows.HeadRow(ctx.Conn, kind, id);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return CoRows.StateOf(row);
        }

        static void FillOrder(
            WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> body,
            List<Dictionary<string, object>> lines)
        {
            body["state"] = MoState(ctx.Conn, kind, id, lines);
            bool truncated;
            body["allocations"] = Allocations(ctx.Conn, kind, lines, out truncated);
            if (truncated)
            {
                body["allocations_truncated"] = true;
            }
        }

        // Status、RelsUser 是生产订单行上的固定列，不是请求文本。表名只来自种类配置。
        static Dictionary<string, object> MoState(
            object conn, VoucherKind kind, int id, List<Dictionary<string, object>> lines)
        {
            string verifier = "";
            string at = "";
            if (lines.Count > 0)
            {
                verifier = CoRows.Col(lines[0], "RelsUser");
                at = CoRows.Col(lines[0], "RelsDate");
            }
            string sql = "SELECT COUNT(*) AS n,"
                + " SUM(CASE WHEN Status IN (3, 4) THEN 1 ELSE 0 END) AS op,"
                + " SUM(CASE WHEN Status = 4 THEN 1 ELSE 0 END) AS closed,"
                + " SUM(CASE WHEN RelsUser IS NOT NULL AND LTRIM(RTRIM(RelsUser)) <> '' THEN 1 ELSE 0 END) AS named"
                + " FROM " + CoRows.Ident(kind.BodyTable)
                + " WHERE " + CoRows.Ident(kind.BodyFk) + "=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { id });
            int total = Num(row, "n");
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = total > 0 && Num(row, "op") == total && Num(row, "named") == total;
            state["verifier"] = verifier;
            state["verified_at"] = at;
            state["closed"] = total > 0 && Num(row, "closed") == total;
            return state;
        }

        static Dictionary<string, object> Pack(
            VoucherKind kind, int id, Dictionary<string, object> head,
            List<Dictionary<string, object>> lines, bool truncated)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(head, kind.CodeColumn);
            body["head"] = head;
            body["lines"] = lines;
            if (truncated)
            {
                body["lines_truncated"] = true;
            }
            return body;
        }

        static List<Dictionary<string, object>> Allocations(
            object conn, VoucherKind kind, List<Dictionary<string, object>> lines, out bool truncated)
        {
            truncated = false;
            List<int> ids = DetailIds(kind, lines);
            if (ids.Count == 0)
            {
                return new List<Dictionary<string, object>>();
            }
            object[] args = new object[ids.Count];
            for (int i = 0; i < ids.Count; i++)
            {
                args[i] = ids[i];
            }
            // mom_moallocate 是合同里的子件表，不是请求文本。列名用种类上的行主键。
            string column = CoRows.Ident(kind.LineIdColumn);
            string sql = "SELECT * FROM mom_moallocate WHERE " + column + " IN (" + Marks(ids.Count) + ") ORDER BY " + column;
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, args, LineCap + 1);
            if (rows == null)
            {
                rows = new List<Dictionary<string, object>>();
            }
            truncated = rows.Count > LineCap;
            while (rows.Count > LineCap)
            {
                rows.RemoveAt(rows.Count - 1);
            }
            for (int i = 0; i < rows.Count; i++)
            {
                Strip(rows[i]);
            }
            return rows;
        }

        static List<int> DetailIds(VoucherKind kind, List<Dictionary<string, object>> lines)
        {
            List<int> ids = new List<int>();
            for (int i = 0; i < lines.Count; i++)
            {
                int id = CoRows.AsId(CoRows.Col(lines[i], kind.LineIdColumn));
                if (id > 0 && !HasId(ids, id))
                {
                    ids.Add(id);
                }
            }
            return ids;
        }

        static bool HasId(List<int> ids, int id)
        {
            for (int i = 0; i < ids.Count; i++)
            {
                if (ids[i] == id)
                {
                    return true;
                }
            }
            return false;
        }

        static string Marks(int count)
        {
            StringBuilder buf = new StringBuilder();
            for (int i = 0; i < count; i++)
            {
                if (i > 0)
                {
                    buf.Append(',');
                }
                buf.Append('?');
            }
            return buf.ToString();
        }

        static int Num(Dictionary<string, object> row, string name)
        {
            string text = CoRows.Col(row, name).Trim();
            if (text.Length == 0)
            {
                return 0;
            }
            int n;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            decimal qty;
            if (decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out qty))
            {
                return (int)qty;
            }
            return 0;
        }

        static void Strip(Dictionary<string, object> row)
        {
            if (row == null)
            {
                return;
            }
            List<string> drop = new List<string>();
            foreach (string key in row.Keys)
            {
                if (Secret(key))
                {
                    drop.Add(key);
                }
            }
            for (int i = 0; i < drop.Count; i++)
            {
                row.Remove(drop[i]);
            }
        }

        static bool Secret(string name)
        {
            string lower = name == null ? "" : name.ToLowerInvariant();
            if (lower.IndexOf("password", StringComparison.Ordinal) >= 0)
            {
                return true;
            }
            return lower.IndexOf("pwd", StringComparison.Ordinal) >= 0;
        }
    }
}
