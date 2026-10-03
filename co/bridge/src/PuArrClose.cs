using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 到货单关闭 / 打开（vouchers/close，type arrival），照采购订单关闭：VoucherCO_PU.Init vt 2，GetVoucherDataById 载入，
    // CloseArrItems / OpenArrItems(头, 体, strErr, bleCloseAll)，strErr 按引用、bleCloseAll 按值（类型库）。在请求连接的 CoTrans 里，
    // 提交前在同一连接上核对行关闭人已到目标状态（按行时没选的行不变），提交后在新连接上回读。表头 ccloser / dclosedate，表体 cbcloser / dlineclosedate。
    internal static class PuArrClose
    {
        internal const int LinesMax = 200;
        internal const string LinesSql = "select Autoid as line_id, cbcloser as closer from PU_ArrivalVouchs where ID=? order by Autoid";
        internal const string HeadSql = "select ccloser as closer, dclosedate as closed_date from PU_ArrivalVouch where ID=?";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, string action, int[] lineIds)
        {
            if (kind == null || kind.Name != "arrival")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持关闭");
            }
            if (action != "close" && action != "open")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 close 或 open");
            }
            Dictionary<string, object> head = CoRows.HeadRow(ctx.Conn, kind, id);
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Dictionary<int, string> before = Closers(ctx.Conn, id);
            Gate(action, head, before, lineIds);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                PuSession.Open(ctx, 2, false, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                if (lineIds != null)
                {
                    KeepLines(doms[1], lineIds);
                }
                CloseTran(ctx, co, doms, new CloseCall(id, action, lineIds, before));
                return Fresh(ctx, kind, id, action);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        // 闸门（纯函数，--selftest 覆盖）：只收蓝字到货单（iBillType=0）；关闭要求已审核；整单已关闭不能再关，
        // 表头和各行都没有关闭人不能打开；按行 1 到 200 行、本单行、不重复，逐行判断状态。
        internal static void Gate(string action, Dictionary<string, object> head, Dictionary<int, string> closers, int[] lineIds)
        {
            if (CoRows.Col(head, "bill_type") != "0")
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字到货单");
            }
            if (action == "close" && CoRows.Col(head, "verifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (lineIds == null)
            {
                GateWhole(action, CoRows.Col(head, "closer").Length > 0, closers);
                return;
            }
            if (lineIds.Length == 0 || lineIds.Length > LinesMax)
            {
                throw new BridgeException(400, "bad_request", "没有要处理的明细");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lineIds.Length; i++)
            {
                GateOne(action, closers, seen, lineIds[i]);
            }
        }

        static void GateWhole(string action, bool headShut, Dictionary<int, string> closers)
        {
            if (action == "close" && headShut)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (action == "open" && !headShut && !AnyShut(closers))
            {
                throw new BridgeException(409, "state_mismatch", "单据未关闭");
            }
        }

        static void GateOne(string action, Dictionary<int, string> closers, HashSet<int> seen, int id)
        {
            string closer;
            if (id <= 0 || closers == null || !closers.TryGetValue(id, out closer))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (!seen.Add(id))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            bool shut = closer != null && closer.Length > 0;
            if (action == "close" && shut)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (action == "open" && !shut)
            {
                throw new BridgeException(409, "state_mismatch", "单据未关闭");
            }
        }

        static bool AnyShut(Dictionary<int, string> closers)
        {
            if (closers == null)
            {
                return false;
            }
            foreach (KeyValuePair<int, string> pair in closers)
            {
                if (pair.Value != null && pair.Value.Length > 0)
                {
                    return true;
                }
            }
            return false;
        }

        // 一次关闭 / 打开：单据、动作、所选行（整单为 null）和调用前各行的关闭人。
        sealed class CloseCall
        {
            public readonly int Id;
            public readonly string Action;
            public readonly int[] LineIds;
            public readonly Dictionary<int, string> Before;

            public CloseCall(int id, string action, int[] lineIds, Dictionary<int, string> before)
            {
                Id = id;
                Action = action;
                LineIds = lineIds;
                Before = before;
            }
        }

        // 提交前核对（纯函数）：整单处理时全部行、按行时所选的行，关闭后都有关闭人、打开后都没有。
        internal static bool Reached(string action, int[] lineIds, Dictionary<int, string> closers)
        {
            if (closers == null || closers.Count == 0)
            {
                return false;
            }
            bool want = action == "close";
            foreach (KeyValuePair<int, string> pair in closers)
            {
                if (lineIds != null && Array.IndexOf(lineIds, pair.Key) < 0)
                {
                    continue;
                }
                bool shut = pair.Value != null && pair.Value.Length > 0;
                if (shut != want)
                {
                    return false;
                }
            }
            return true;
        }

        // 按行处理的提交前核对（纯函数）：没选的行关闭状态与调用前相同，行也没有增减；整单处理不看。
        internal static bool Untouched(int[] lineIds, Dictionary<int, string> before, Dictionary<int, string> after)
        {
            if (lineIds == null)
            {
                return true;
            }
            if (before == null || after == null || before.Count != after.Count)
            {
                return false;
            }
            foreach (KeyValuePair<int, string> pair in before)
            {
                string now;
                if (!after.TryGetValue(pair.Key, out now))
                {
                    return false;
                }
                if (Array.IndexOf(lineIds, pair.Key) < 0 && Shut(pair.Value) != Shut(now))
                {
                    return false;
                }
            }
            return true;
        }

        static bool Shut(string closer)
        {
            return closer != null && closer.Length > 0;
        }

        internal static Dictionary<int, string> Closers(object conn, int id)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, LinesSql, new object[] { id }, 5000);
            Dictionary<int, string> map = new Dictionary<int, string>();
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(rows[i], "line_id"));
                if (lineId > 0)
                {
                    map[lineId] = CoRows.Col(rows[i], "closer");
                }
            }
            return map;
        }

        // 按行处理时只留下所选的行交给 U8（到货单表体行主键属性是 autoid）。
        static void KeepLines(object body, int[] ids)
        {
            for (int guard = 0; guard < 10000; guard++)
            {
                List<object> rows = DomRows.RowsOf(body);
                int count = rows == null ? 0 : rows.Count;
                object drop = null;
                int kept = 0;
                for (int i = 0; i < count; i++)
                {
                    if (Array.IndexOf(ids, CoRows.AsId(DomRows.Get(rows[i], "autoid"))) >= 0)
                    {
                        kept++;
                    }
                    else if (drop == null)
                    {
                        drop = rows[i];
                    }
                }
                if (drop == null)
                {
                    if (kept != ids.Length)
                    {
                        throw new BridgeException(409, "state_mismatch", "U8 返回的表体与到货单不一致");
                    }
                    return;
                }
                DomRows.RemoveRow(drop);
            }
            throw new BridgeException(500, "internal", "未能筛出关闭行");
        }

        static void CloseTran(WorkContext ctx, object co, object[] doms, CloseCall call)
        {
            string action = call.Action;
            int[] lineIds = call.LineIds;
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                string method = action == "close" ? "CloseArrItems" : "OpenArrItems";
                object[] args = new object[] { doms[0], doms[1], "", lineIds == null };
                object ret = ComUtil.CallRef(co, method, args, new int[] { 2 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string err = Values.Text(args[2]).Trim();
                CoRows.Note(ctx.Item, method + " " + Values.Text(ret) + " " + err);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                Dictionary<int, string> after = Closers(ctx.Conn, call.Id);
                if (!Reached(action, lineIds, after))
                {
                    throw new BridgeException(409, "state_mismatch", "U8 没有处理任何行");
                }
                if (!Untouched(lineIds, call.Before, after))
                {
                    throw new BridgeException(409, "state_mismatch", "U8 同时改动了未选的明细行，已撤销");
                }
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // CommitSeen 之后的回读失败一律 504：已经关闭 / 打开，调用方不要再投一次。
        static ApiResult Fresh(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> head = Rows.One(conn, HeadSql, new object[] { id });
                List<Dictionary<string, object>> lines = Rows.Query(conn, LinesSql, new object[] { id }, 5000);
                if (head == null)
                {
                    throw new InvalidOperationException("到货单回读为空");
                }
                return ApiResult.Ok(Body(kind.Name, id, action, head, lines));
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Fresh " + ex.Message);
                string done = action == "close" ? "已关闭" : "已打开";
                throw new BridgeException(504, "outcome_unknown",
                    "到货单" + done + "，但回读失败，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 响应同采购订单关闭：closed 看表头关闭人，closed_at 取 dclosedate。
        internal static Dictionary<string, object> Body(string type, int id, string action, Dictionary<string, object> head,
            List<Dictionary<string, object>> lines)
        {
            string closer = CoRows.Col(head, "closer");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = type;
            body["id"] = id;
            body["action"] = action;
            body["closed"] = closer.Length > 0;
            body["closed_by"] = closer;
            body["closed_at"] = closer.Length > 0 ? DateText(head) : "";
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            for (int i = 0; lines != null && i < lines.Count; i++)
            {
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["line_id"] = CoRows.AsId(CoRows.Col(lines[i], "line_id"));
                item["closed"] = CoRows.Col(lines[i], "closer").Length > 0;
                list.Add(item);
            }
            body["lines"] = list;
            return body;
        }

        static string DateText(Dictionary<string, object> head)
        {
            object value;
            if (head.TryGetValue("closed_date", out value) && value is DateTime)
            {
                DateTime at = (DateTime)value;
                string pattern = at.TimeOfDay.Ticks == 0 ? "yyyy-MM-dd" : "yyyy-MM-dd HH:mm:ss";
                return at.ToString(pattern, CultureInfo.InvariantCulture);
            }
            return CoRows.Col(head, "closed_date");
        }
    }
}
