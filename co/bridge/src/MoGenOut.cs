using System.Collections.Generic;

namespace U8Co
{
    // 生产订单参照销售订单生成的响应、预演输入和调用前快照（审计事件 mo_generate_snapshot）。
    // 响应与生产订单新增相同（MoCreateOut：id、code、state、lines、allocates、details、warnings），另加 source_type、source_id；
    // details 每行另给 source_line_id（销售订单行 iSOsID）和 cust_bom（是否按客户 BOM 展开，CustBomId≠0）。
    internal static class MoGenOut
    {
        // 另带生单路由共有的 source_type、source_id（API 的 CoGenerateOut 必填）。
        public static ApiResult Result(VoucherKind kind, MoGenAsk ask, List<Dictionary<string, object>> rows)
        {
            int id = CoRows.AsId(CoRows.Col(rows[0], "MoId"));
            ApiResult result = MoCreateOut.Result(kind, id, rows);
            result.Body["source_type"] = "sale_order";
            result.Body["source_id"] = ask.SoId;
            List<object> details = result.Body["details"] as List<object>;
            for (int i = 0; details != null && i < details.Count && i < rows.Count; i++)
            {
                Dictionary<string, object> line = details[i] as Dictionary<string, object>;
                if (line == null)
                {
                    continue;
                }
                line["source_line_id"] = CoRows.AsId(CoRows.Col(rows[i], "OrderDId"));
                line["cust_bom"] = CoRows.AsId(CoRows.Col(rows[i], "CustBomId")) != 0;
            }
            return result;
        }

        public static void Warn(ApiResult result, string text)
        {
            List<object> warnings = result.Body.ContainsKey("warnings") ? result.Body["warnings"] as List<object> : null;
            if (warnings == null)
            {
                warnings = new List<object>();
                result.Body["warnings"] = warnings;
            }
            warnings.Add(text);
        }

        public static void DryInput(MoGenAsk ask)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> input = Snapshot(ask);
            DryRun.Set("input", input);
        }

        // 调用 U8 之前写审计：来源、类别、单号、调用前的最大 MoId / MoDId 和数据库时间，每行的来源行、数量、生产部门和
        // 调用前的销售订单已下达数量（iMoQuantity）。结果未知时据此在 U8 里核对。
        public static void LogSnapshot(WorkContext ctx, MoGenAsk ask)
        {
            Dictionary<string, object> evt = Snapshot(ask);
            evt["operator"] = ctx.Item == null ? "" : ctx.Item.Operator;
            evt["max_mo_id"] = ask.MaxMoId;
            evt["max_mo_d_id"] = ask.MaxMoDId;
            evt["since"] = ask.Since;
            AuditEvent.Write("mo_generate_snapshot", evt);
        }

        static Dictionary<string, object> Snapshot(MoGenAsk ask)
        {
            List<object> lines = new List<object>();
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                MoGenLine one = ask.Lines[i];
                Dictionary<string, object> line = new Dictionary<string, object>();
                line["sort_seq"] = one.Seq;
                line["source_line_id"] = one.SoLineId;
                line["inv_code"] = one.InvCode;
                line["qty"] = one.Qty;
                line["dept_code"] = one.FinalDept;
                line["remark"] = ask.Remark ?? Values.Text(one.U8Row == null ? null : one.U8Row["Remark"]);
                line["so_mo_qty_before"] = one.MoQtyBefore;
                lines.Add(line);
            }
            Dictionary<string, object> snap = new Dictionary<string, object>();
            snap["source_id"] = ask.SoId;
            snap["source_code"] = ask.SoCode;
            snap["mo_type"] = ask.MoType;
            snap["code"] = ask.Code;
            snap["lines"] = lines;
            return snap;
        }
    }
}
