using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class SaleEdit
    {
        static void GateClose(object conn, int id, Dictionary<string, object> snap, bool closing, int[] lineIds)
        {
            bool verified = CoRows.Col(snap, "verifier").Length > 0 && CoRows.Col(snap, "verify_date").Length > 0;
            if (closing && !verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            bool headClosed = CoRows.Col(snap, "closer").Length > 0;
            if (lineIds == null)
            {
                if (closing && headClosed)
                {
                    throw new BridgeException(409, "state_mismatch", "单据已关闭");
                }
                if (!closing && !headClosed)
                {
                    throw new BridgeException(409, "state_mismatch", "单据未关闭");
                }
                return;
            }
            Dictionary<int, string> lines = LineClosers(conn, id);
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            for (int i = 0; i < lineIds.Length; i++)
            {
                GateLine(lines, seen, lineIds[i], closing);
            }
        }

        static void GateLine(Dictionary<int, string> lines, Dictionary<int, bool> seen, int lineId, bool closing)
        {
            if (seen.ContainsKey(lineId))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            seen[lineId] = true;
            string closer;
            if (lineId <= 0 || !lines.TryGetValue(lineId, out closer))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            bool shut = closer != null && closer.Trim().Length > 0;
            if (closing && shut)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (!closing && !shut)
            {
                throw new BridgeException(409, "state_mismatch", "单据未关闭");
            }
        }

        static Dictionary<int, string> LineClosers(object conn, int id)
        {
            Dictionary<int, string> lines = new Dictionary<int, string>();
            List<Dictionary<string, object>> rows = Rows.Query(conn, LineStateSql, new object[] { id }, 5000);
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(rows[i], "iSOsID"));
                if (lineId > 0)
                {
                    lines[lineId] = CoRows.Col(rows[i], "cSCloser");
                }
            }
            return lines;
        }

        static void RunClose(WorkContext ctx, int id, bool closing, int[] lineIds)
        {
            object sys = null;
            object co = null;
            object dom = null;
            bool open = false;
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, 12, out sys, out co);
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                if (lineIds == null)
                {
                    CloseOne(CloseAskOf(ctx.Conn, co, id, closing, 0, true), ref dom);
                }
                else
                {
                    for (int i = 0; i < lineIds.Length; i++)
                    {
                        CloseOne(CloseAskOf(ctx.Conn, co, id, closing, lineIds[i], false), ref dom);
                    }
                }
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
            finally
            {
                ComUtil.Final(dom);
                ComUtil.Final(co);
                SaSession.CloseSa(sys);
                ComUtil.Final(sys);
            }
        }

        static CloseAsk CloseAskOf(object conn, object co, int id, bool closing, int lineId, bool whole)
        {
            CloseAsk ask = new CloseAsk();
            ask.Conn = conn;
            ask.Co = co;
            ask.Id = id;
            ask.Closing = closing;
            ask.LineId = lineId;
            ask.Whole = whole;
            return ask;
        }

        static void CloseOne(CloseAsk ask, ref object dom)
        {
            ComUtil.Final(dom);
            dom = AdoXml.LoadDom(ask.Conn, AdoXml.VoucherSql(ask.Conn, true), ask.Id);
            object[] args;
            int[] refs;
            if (ask.Whole)
            {
                args = new object[] { dom, ask.Closing };
                refs = new int[] { 0, 1 };
            }
            else
            {
                args = new object[] { dom, ask.Closing, ask.LineId };
                refs = new int[] { 0, 1, 2 };
            }
            object ret = ComUtil.CallRef(ask.Co, "OrderClose", args, refs);
            Keep(ref dom, args[0]);
            string msg = ret == null ? "" : Convert.ToString(ret);
            if (msg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", msg);
            }
        }

        static void Keep(ref object dom, object updated)
        {
            if (updated == null || updated is DBNull || object.ReferenceEquals(dom, updated))
            {
                return;
            }
            ComUtil.Final(dom);
            dom = updated;
        }

        static ApiResult ReadClosed(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> head = Rows.One(conn, CloseHeadSql, new object[] { id });
                if (head == null)
                {
                    throw new BridgeException(504, "outcome_unknown", "已保存但回读关闭状态失败");
                }
                List<Dictionary<string, object>> found = Rows.Query(conn, CloseLineSql, new object[] { id }, 5000);
                return ClosedBody(kind, id, action, head, found);
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 504)
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "CloseRead " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已保存但回读关闭状态失败");
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "CloseRead " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已保存但回读关闭状态失败");
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static ApiResult ClosedBody(VoucherKind kind, int id, string action, Dictionary<string, object> head,
            List<Dictionary<string, object>> found)
        {
            string closer = CoRows.Col(head, "closer");
            string at = CoRows.Col(head, "closed_sys");
            if (at.Length == 0)
            {
                at = CoRows.Col(head, "closed_on");
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["action"] = action;
            body["closed"] = closer.Length > 0;
            body["closed_by"] = closer;
            body["closed_at"] = at;
            body["lines"] = ClosedLines(found);
            return ApiResult.Ok(body);
        }

        static List<Dictionary<string, object>> ClosedLines(List<Dictionary<string, object>> found)
        {
            List<Dictionary<string, object>> lines = new List<Dictionary<string, object>>();
            if (found == null)
            {
                return lines;
            }
            for (int i = 0; i < found.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(found[i], "line_id"));
                if (lineId <= 0)
                {
                    continue;
                }
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["line_id"] = lineId;
                item["closed"] = CoRows.Col(found[i], "closer").Length > 0;
                lines.Add(item);
            }
            return lines;
        }

        static bool WantClose(string action)
        {
            if (action == "close")
            {
                return true;
            }
            if (action == "open")
            {
                return false;
            }
            throw new BridgeException(400, "bad_request", "action 必须是 close 或 open");
        }

        static Dictionary<string, string> HeadVals(object headDom)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            List<object> rows = DomRows.RowsOf(headDom);
            if (rows.Count < 1)
            {
                return map;
            }
            Put(map, rows[0], "dDate");
            Put(map, rows[0], "dPreDateBT");
            Put(map, rows[0], "dPreMoDateBT");
            return map;
        }

        static void Put(Dictionary<string, string> map, object row, string name)
        {
            string value = DomRows.Get(row, name);
            if (value.Length > 0)
            {
                map[name] = value;
            }
        }

        static Dictionary<string, string> Names(object dom)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            List<string> names = DomRows.Schema(dom);
            if (names == null)
            {
                return map;
            }
            for (int i = 0; i < names.Count; i++)
            {
                string name = names[i] == null ? "" : names[i].Trim();
                if (name.Length > 0 && !map.ContainsKey(name))
                {
                    map[name] = name;
                }
            }
            return map;
        }

        static int MaxRowNo(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            int max = rows.Count;
            for (int i = 0; i < rows.Count; i++)
            {
                int n = CoRows.AsId(DomRows.Get(rows[i], "irowno"));
                if (n > max)
                {
                    max = n;
                }
            }
            return max;
        }

        static object FindLine(object body, int lineId)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.AsId(DomRows.Get(rows[i], "isosid")) == lineId)
                {
                    return rows[i];
                }
            }
            return null;
        }

        static object At(object body, int index)
        {
            List<object> rows = DomRows.RowsOf(body);
            if (index < 0 || index >= rows.Count)
            {
                throw new BridgeException(500, "internal", "模板行数不符");
            }
            return rows[index];
        }

        static string User(WorkContext ctx)
        {
            string user = ctx.Session.OperatorName ?? "";
            if (user.Length == 0)
            {
                user = ctx.Item.Operator ?? "";
            }
            return user;
        }

        static string LoginDate(WorkContext ctx)
        {
            string date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length > 0)
            {
                return date;
            }
            return DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        static bool HasKey(Dictionary<string, object> map, string name)
        {
            return RawKey(map, name) != null || (map != null && Contains(map, name));
        }

        static bool Contains(Dictionary<string, object> map, string name)
        {
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (Same(pair.Key, name))
                {
                    return true;
                }
            }
            return false;
        }

        static object RawKey(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return null;
            }
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (Same(pair.Key, name) && pair.Value != null && !(pair.Value is DBNull))
                {
                    return pair.Value;
                }
            }
            return null;
        }

        static string TextKey(Dictionary<string, object> map, string name)
        {
            return Values.Text(RawKey(map, name)).Trim();
        }

        static bool Same(string key, string name)
        {
            return string.Equals(key, name, StringComparison.OrdinalIgnoreCase);
        }

        static void RefuseBlue(Dictionary<string, object> shape)
        {
            if (CoRows.FlagOf(shape, "bReturnFlag"))
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字发货单");
            }
            if (CoRows.Col(shape, "cVouchType") != "05")
            {
                throw new BridgeException(400, "bad_request", "仅支持发货单类型 05");
            }
            if (CoRows.FlagOf(shape, "bFirst"))
            {
                throw new BridgeException(400, "bad_request", "不支持期初发货单");
            }
        }

        static bool HeadRate(Dictionary<string, object> head)
        {
            return HasKey(head, "iexchrate") || HasKey(head, "cexch_name");
        }

        static void RecalcOpen(object co, object[] doms)
        {
            int count = DomRows.RowsOf(doms[1]).Count;
            for (int i = 0; i < count; i++)
            {
                object row = At(doms[1], i);
                string prop = DomRows.Get(row, "editprop");
                if (prop == "D")
                {
                    continue;
                }
                SaleCalc.RecalcExisting(co, doms[0], doms[1], row);
                MarkOpen(doms, i, prop);
            }
        }

        // 汇率重算改了金额。未改行的 editprop 仍是空，U8 不会保存，所以标成 M。新增行保持 A。
        static void MarkOpen(object[] doms, int index, string prop)
        {
            object row = At(doms[1], index);
            if (prop == "A")
            {
                DomRows.Set(doms[1], row, "editprop", "A");
                return;
            }
            DomRows.Set(doms[1], row, "editprop", "M");
        }

        sealed class CloseAsk
        {
            internal object Conn;
            internal object Co;
            internal int Id;
            internal bool Closing;
            internal int LineId;
            internal bool Whole;
        }
    }
}
