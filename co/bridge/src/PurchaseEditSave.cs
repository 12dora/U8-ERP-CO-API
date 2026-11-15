using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class PurchaseEdit
    {
        static int CreateTran(WorkContext ctx, VoucherKind kind, object co, object[] doms)
        {
            bool open = false;
            string code = "";
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                code = Allocate(co, doms, ctx.Item);
                object head = OnlyRow(doms[0]);
                DomRows.Set(doms[0], head, "cmaker", Maker(ctx));
                int id = Save(co, doms, (short)2, "", ctx.Item);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                DocMark.Created(ctx.Conn, kind, id, code);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                if (id <= 0)
                {
                    id = IdAfterCommit(ctx, kind, code);
                }
                return id;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // CommitSeen 之后按单号回读失败必须是 504，调用方重试会再生成一张采购订单。
        static int IdAfterCommit(WorkContext ctx, VoucherKind kind, string code)
        {
            try
            {
                int id = IdByCode(ctx.Conn, kind, code);
                if (id <= 0)
                {
                    throw new BridgeException(504, "outcome_unknown", Unknown(code));
                }
                return id;
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(code));
            }
        }

        static void CommitSave(WorkContext ctx, object co, object[] doms, short mode, string poid)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                Save(co, doms, mode, poid, ctx.Item);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        static void DeleteTran(WorkContext ctx, object co, object[] doms)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret).Trim();
                CoRows.Note(ctx.Item, "Delete " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
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

        static void CloseTran(WorkContext ctx, object co, object[] doms, string action, bool whole)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                string method = action == "close" ? "ClosePOItems" : "OpenPOItems";
                object[] args = new object[] { doms[0], doms[1], "", whole };
                object ret = ComUtil.CallRef(co, method, args, new int[] { 2, 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string err = Values.Text(args[2]).Trim();
                int count = CountOf(ret);
                CoRows.Note(ctx.Item, method + " " + count.ToString(CultureInfo.InvariantCulture) + " " + err);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                if (count <= 0)
                {
                    throw new BridgeException(409, "state_mismatch", "没有可处理的行");
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

        // GetVoucherNO(head, "88", err, no)，四个参数都按引用。成功后再把单号写到表头和表体。
        static string Allocate(object co, object[] doms, WorkItem item)
        {
            object[] args = new object[] { doms[0], "88", "", "" };
            object ret = ComUtil.CallRef(co, "GetVoucherNO", args, new int[] { 0, 1, 2, 3 });
            CoRows.Swap(doms, 0, args[0]);
            string err = Values.Text(args[2]).Trim();
            string no = Values.Text(args[3]).Trim();
            CoRows.Note(item, "GetVoucherNO " + Values.Text(ret) + " " + err + " " + no);
            if (!Values.Flag(ret) || no.Length == 0)
            {
                if (err.Length == 0)
                {
                    err = "未能取得单据号";
                }
                throw new BridgeException(409, "u8_rejected", err);
            }
            object row = OnlyRow(doms[0]);
            DomRows.Set(doms[0], row, "cpoid", no);
            DomRows.Set(doms[0], row, "editprop", "A");
            StampBodyCode(doms[1], no);
            return no;
        }

        static int Save(object co, object[] doms, short mode, string poid, WorkItem item)
        {
            object[] args = new object[] { doms[0], doms[1], mode, poid ?? "" };
            object ret = ComUtil.CallRef(co, "VoucherSave2", args, new int[] { 3 });
            string msg = ret == null ? "" : Convert.ToString(ret).Trim();
            CoRows.Note(item, "VoucherSave2 " + (msg.Length == 0 ? "ok" : msg));
            if (msg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", msg);
            }
            return CoRows.AsId(args[3]);
        }

        static string TemplateId(object co, object conn, WorkItem item)
        {
            try
            {
                object ret = ComUtil.Call(co, "GetDefaultVTID", new object[] { conn, "88" });
                int id = CoRows.AsId(ret);
                if (id > 0)
                {
                    return id.ToString(CultureInfo.InvariantCulture);
                }
                CoRows.Note(item, "GetDefaultVTID " + Values.Text(ret));
            }
            catch (Exception ex)
            {
                CoRows.Note(item, "GetDefaultVTID " + ex.Message);
            }
            return "8173";
        }

        static void StampBodyCode(object body, string code)
        {
            List<object> rows = DomRows.RowsOf(body);
            if (rows == null)
            {
                return;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                DomRows.Set(body, rows[i], "cpoid", code);
            }
        }

        static int IdByCode(object conn, VoucherKind kind, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return 0;
            }
            string sql = "select " + kind.IdColumn + " from " + kind.HeadTable + " where " + kind.CodeColumn + "=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { no });
            if (row == null)
            {
                return 0;
            }
            return AsInt(Cell(row, kind.IdColumn));
        }

        static ApiResult Gone(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                if (CoRows.HeadRow(conn, kind, id) != null)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["type"] = kind.Name;
                body["id"] = id;
                body["deleted"] = true;
                return ApiResult.Ok(body);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static ApiResult FreshClose(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                string headSql = "select cCloser as closer, dCloseDate as closed_date, dCloseTime as closed_time from "
                    + kind.HeadTable + " where " + kind.IdColumn + "=?";
                Dictionary<string, object> head = Rows.One(conn, headSql, new object[] { id });
                if (head == null)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                string lineSql = "select ID as line_id, cbCloser as closer from " + kind.BodyTable
                    + " where " + kind.BodyFk + "=? order by ID";
                List<Dictionary<string, object>> lines = Rows.Query(conn, lineSql, new object[] { id }, 5000);
                return CloseBody(kind, id, action, head, lines);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static ApiResult CloseBody(VoucherKind kind, int id, string action, Dictionary<string, object> head,
            List<Dictionary<string, object>> lines)
        {
            string closer = Show(Cell(head, "closer"));
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["action"] = action;
            body["closed"] = closer.Length > 0;
            body["closed_by"] = closer;
            body["closed_at"] = ClosedAt(head);
            body["lines"] = CloseLines(lines);
            return ApiResult.Ok(body);
        }

        static List<Dictionary<string, object>> CloseLines(List<Dictionary<string, object>> lines)
        {
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            if (lines == null)
            {
                return list;
            }
            for (int i = 0; i < lines.Count; i++)
            {
                string closer = Show(Cell(lines[i], "closer"));
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["line_id"] = AsInt(Cell(lines[i], "line_id"));
                item["closed"] = closer.Length > 0;
                list.Add(item);
            }
            return list;
        }

        static string ClosedAt(Dictionary<string, object> head)
        {
            string time = Show(Cell(head, "closed_time"));
            if (time.Length > 0)
            {
                return time;
            }
            return Show(Cell(head, "closed_date"));
        }

        static ApiResult AfterSaved(WorkContext ctx, VoucherKind kind, int id, object headDom)
        {
            string code = "";
            try
            {
                code = HeadCode(headDom, kind);
                return EditMsg.Saved(ctx, kind, id, null, 0);
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(id, code));
            }
        }

        static string HeadCode(object dom, VoucherKind kind)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows == null || rows.Count == 0 || kind == null || kind.CodeColumn == null)
                {
                    return "";
                }
                return Clean(DomRows.Get(rows[0], kind.CodeColumn));
            }
            finally
            {
                ReleaseRows(rows);
            }
        }

        static void ReleaseRows(List<object> rows)
        {
            if (rows == null)
            {
                return;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                ComUtil.ReleaseOne(rows[i]);
            }
        }
    }
}
