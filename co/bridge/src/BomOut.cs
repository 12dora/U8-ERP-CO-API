using System;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单新增、修改成功后的响应：在新连接上回读，给 ok、type、id、code（母件）、version、state、
    // lines（保存后的行数）和 components（每行 line_id、sort_seq、inv_code、base_qty_n、base_qty_d）；新增另核对替代料、定位符。
    // 回读失败时已经写进去了，一律 504 outcome_unknown，带上 BomId。
    internal static class BomOut
    {
        public static ApiResult Saved(WorkContext ctx, VoucherKind kind, int id, string what)
        {
            return Saved(ctx, kind, id, what, null);
        }

        // want 非空（新增）：另核对每行的替代料、定位符与请求相同，不同 409（版本已建成，带上 BomId）。
        // 修改在 BomEdit.Confirm 里已整行核对过（含替代料、定位符），传 null。
        public static ApiResult Saved(WorkContext ctx, VoucherKind kind, int id, string what, BomAsk want)
        {
            object conn = null;
            Dictionary<string, object> body;
            bool same;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> head = BomRead.Head(conn, id);
                List<Dictionary<string, object>> lines = BomRead.Lines(conn, id);
                body = BomRead.Body(kind, id, head);
                body["lines"] = lines.Count;
                body["components"] = Components(lines);
                same = want == null || BomSubs.SameAll(lines, want.Rows);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "BomOut " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown",
                    "已" + what + "物料清单但未能回读，BomId " + BomReq.Num(id));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (!same)
            {
                throw new BridgeException(409, "state_mismatch",
                    "已" + what + "物料清单（BomId " + BomReq.Num(id) + "），但回读的替代料或定位符与请求不符");
            }
            return ApiResult.Ok(body);
        }

        static List<object> Components(List<Dictionary<string, object>> lines)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["line_id"] = CoRows.AsId(CoRows.Col(lines[i], "line_id"));
                item["sort_seq"] = BomSql.Int(CoRows.Col(lines[i], "sort_seq"));
                item["inv_code"] = CoRows.Col(lines[i], "inv_code");
                item["base_qty_n"] = MoCreateSql.Dec(CoRows.Col(lines[i], "base_qty_n"));
                item["base_qty_d"] = MoCreateSql.Dec(CoRows.Col(lines[i], "base_qty_d"));
                list.Add(item);
            }
            return list;
        }

        // 调用之后在新连接上读表头（审核、弃审回读状态用）；读不到或出错 504。
        public static Dictionary<string, object> Reread(WorkContext ctx, int id, string what)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return BomRead.Head(conn, id);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "BomOut " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown",
                    "已提交" + what + "但未能回读状态，BomId " + BomReq.Num(id));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // lost 非空：InvokeApi 期间或之后出了异常或 IPC 错误。回读没到目标时：IPC 错误报 503（多半没做成），其他 504。
        public static BridgeException Unreached(Exception lost, string what, int id)
        {
            if (lost != null && MoApi.IsIpc(lost.Message))
            {
                return new BridgeException(503, "u8_unavailable", "U8 生产制造服务未运行");
            }
            if (lost != null)
            {
                string text = MoApi.FirstLine(lost.Message);
                return new BridgeException(504, "outcome_unknown",
                    "U8 " + what + "调用异常，结果未知，BomId " + BomReq.Num(id) + (text.Length > 0 ? "：" + text : ""));
            }
            return new BridgeException(409, "state_mismatch", "U8 返回成功但回读与" + what + "结果不符");
        }
    }
}
