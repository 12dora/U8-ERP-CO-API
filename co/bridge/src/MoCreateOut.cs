using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单新增的响应：与其他类型新增相同的 ok、type、id、code、state、lines（行数），
    // 另给 allocates（全部行的子件行数）、details（每行 line_id、sort_seq、inv_code、qty、status、allocates）；
    // 有行没展开子件（存货没有有效的标准 BOM）时加 warnings。
    internal static class MoCreateOut
    {
        public static ApiResult Result(VoucherKind kind, int id, List<Dictionary<string, object>> rows)
        {
            List<object> details = new List<object>();
            List<object> warnings = new List<object>();
            int allocates = 0;
            int released = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> line = Detail(rows[i]);
                int n = (int)line["allocates"];
                allocates += n;
                if ((int)line["status"] == 3)
                {
                    released++;
                }
                if (n == 0)
                {
                    warnings.Add("第 " + ((int)line["sort_seq"]).ToString(CultureInfo.InvariantCulture)
                        + " 行没有展开子件（存货没有有效的标准 BOM），allocates 为 0");
                }
                details.Add(line);
            }
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = rows.Count > 0 && released == rows.Count;
            state["verifier"] = "";
            state["verified_at"] = "";
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(rows[0], "MoCode");
            body["state"] = state;
            body["lines"] = rows.Count;
            body["allocates"] = allocates;
            body["details"] = details;
            if (warnings.Count > 0)
            {
                body["warnings"] = warnings;
            }
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Detail(Dictionary<string, object> row)
        {
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["line_id"] = CoRows.AsId(CoRows.Col(row, "MoDId"));
            line["sort_seq"] = Int(CoRows.Col(row, "SortSeq"));
            line["inv_code"] = CoRows.Col(row, "InvCode");
            line["qty"] = MoCreateSql.Dec(CoRows.Col(row, "Qty"));
            line["status"] = Int(CoRows.Col(row, "Status"));
            line["allocates"] = Int(CoRows.Col(row, "allocates"));
            return line;
        }

        static int Int(string text)
        {
            int n;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }
    }
}
