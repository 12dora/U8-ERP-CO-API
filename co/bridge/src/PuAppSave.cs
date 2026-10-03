using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 请购单的事务与提交后回读。每次写都包在 CoTrans 里；提交后在新连接上回读，读不到目标状态 409，回读失败 504。
    internal static partial class PuApp
    {
        internal const string Card = "27";
        const string CardVtid = "8171";

        // ConfirmApp(头, DomMsg) 引用 {1}；CancelconfirmApp(头, 体) 按值。返回非空串是 U8 的拒绝原文。
        static string ConfirmTran(WorkContext ctx, object co, object[] doms, string action, int id)
        {
            bool open = false;
            object msgDom = null;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                object ret;
                if (action == "verify")
                {
                    msgDom = Rows.NewDom();
                    object[] args = new object[] { doms[0], msgDom };
                    ret = ComUtil.CallRef(co, "ConfirmApp", args, new int[] { 1 });
                    CoRows.ReleaseIfNew(msgDom, args[1]);
                }
                else
                {
                    RefuseChildLocked(ctx.Conn, id, action);
                    ret = ComUtil.Call(co, "CancelconfirmApp", new object[] { doms[0], doms[1] });
                }
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, (action == "verify" ? "ConfirmApp " : "CancelconfirmApp ") + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    CoTrans.Rollback(ctx.Conn);
                    open = false;
                    return msg;
                }
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                return "";
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
            finally
            {
                ComUtil.Final(msgDom);
            }
        }

        // CloseApp(头) / OpenApp(头) 按值，返回空串为成功。
        static void CloseTran(WorkContext ctx, object co, object[] doms, string action)
        {
            string method = action == "close" ? "CloseApp" : "OpenApp";
            RunTran(ctx, method, delegate { return ComUtil.Call(co, method, new object[] { doms[0] }); });
        }

        // Delete(头, 体) 引用 {0,1}，尾部 CurDom 不传。
        static void DeleteTran(WorkContext ctx, object co, object[] doms, int id)
        {
            RunTran(ctx, "Delete", delegate
            {
                RefuseChildLocked(ctx.Conn, id, "delete");
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                return ret;
            });
        }

        delegate object CoStep();

        static void RunTran(WorkContext ctx, string name, CoStep step)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                object ret = step();
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, name + " " + (msg.Length == 0 ? "ok" : msg));
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

        // 新增：GetVoucherNO(头, "27", err, no) 取号，再 VoucherSave2(头, 体, 2, curId)。返回新 ID，0 表示要按单号找。
        static int CreateTran(WorkContext ctx, object co, object[] doms, out string code)
        {
            bool open = false;
            code = "";
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                code = Allocate(co, doms, ctx.Item);
                int id = Save(co, doms, (short)2, "", ctx.Item);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                DocMark.Created(ctx.Conn, "purchase_requisition", id, code);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                return id;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 修改：VoucherSave2(头, 体, 1, id)。
        static void UpdateTran(WorkContext ctx, object co, object[] doms, int id)
        {
            RunTran(ctx, "VoucherSave2", delegate
            {
                RefuseChildLocked(ctx.Conn, id, "update");
                Save(co, doms, (short)1, id.ToString(CultureInfo.InvariantCulture), ctx.Item);
                return null;
            });
        }

        static string Allocate(object co, object[] doms, WorkItem item)
        {
            object[] args = new object[] { doms[0], Card, "", "" };
            object ret = ComUtil.CallRef(co, "GetVoucherNO", args, new int[] { 0, 1, 2, 3 });
            CoRows.Swap(doms, 0, args[0]);
            string err = Values.Text(args[2]).Trim();
            string no = Values.Text(args[3]).Trim();
            CoRows.Note(item, "GetVoucherNO " + Values.Text(ret) + " " + err + " " + no);
            if (!Values.Flag(ret) || no.Length == 0)
            {
                throw new BridgeException(409, "u8_rejected", err.Length == 0 ? "未能取得单据号" : err);
            }
            List<object> rows = DomRows.RowsOf(doms[0]);
            if (rows == null || rows.Count != 1)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            DomRows.Set(doms[0], rows[0], "ccode", no);
            DomRows.Set(doms[0], rows[0], "editprop", "A");
            return no;
        }

        static int Save(object co, object[] doms, short mode, string id, WorkItem item)
        {
            object[] args = new object[] { doms[0], doms[1], mode, id ?? "" };
            object ret = ComUtil.CallRef(co, "VoucherSave2", args, new int[] { 3 });
            string msg = Values.Text(ret).Trim();
            CoRows.Note(item, "VoucherSave2 " + (msg.Length == 0 ? "ok" : msg));
            if (msg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", msg);
            }
            return CoRows.AsId(args[3]);
        }

        static string TemplateId(object co, WorkContext ctx)
        {
            try
            {
                int id = CoRows.AsId(ComUtil.Call(co, "GetDefaultVTID", new object[] { ctx.Conn, Card }));
                if (id > 0)
                {
                    return id.ToString(CultureInfo.InvariantCulture);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GetDefaultVTID " + ex.Message);
            }
            return CardVtid;
        }

        // 已提交。新主键先认 curId，读不到按单号在新连接上找；都失败或回读失败一律 504。
        static ApiResult AfterSaved(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            try
            {
                if (id <= 0)
                {
                    id = IdByCode(ctx, code);
                }
                if (id > 0)
                {
                    return Saved(ctx, kind, id);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
            }
            string text = "已保存但未能确定单据标识";
            if (code != null && code.Length > 0)
            {
                text = text + "，单号 " + code;
            }
            if (id > 0)
            {
                text = text + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            throw new BridgeException(504, "outcome_unknown", text);
        }

        // 同 EditMsg.Saved 的响应：ok、type、id、code、state、lines（保存后的行数）。
        static ApiResult Saved(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = HeadRow(conn, id);
                if (row == null)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                string count = Rows.Scalar(conn, "select convert(varchar(20), count(*)) from PU_AppVouchs where ID=?",
                    new object[] { id });
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["type"] = kind.Name;
                body["id"] = id;
                body["code"] = CoRows.Col(row, "code");
                body["state"] = CoRows.StateOf(row);
                body["lines"] = CoRows.AsId(count);
                return ApiResult.Ok(body);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static int IdByCode(WorkContext ctx, string code)
        {
            if (code == null || code.Trim().Length == 0)
            {
                return 0;
            }
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return CoRows.AsId(Rows.Scalar(conn, "select convert(varchar(20), ID) from PU_AppVouch where cCode=?",
                    new object[] { code.Trim() }));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static ApiResult FreshVerify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            Dictionary<string, object> row = FreshRow(ctx, kind, id);
            if (action == "verify")
            {
                string name = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
                if (!Verified(row) || !string.Equals(CoRows.Col(row, "verifier"), name, StringComparison.Ordinal))
                {
                    throw new BridgeException(409, "state_mismatch", "审核后审核人与登录操作员不一致");
                }
            }
            else if (Verified(row))
            {
                throw new BridgeException(409, "state_mismatch", "弃审后审核人仍在");
            }
            Dictionary<string, object> state = CoRows.StateOf(row);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["id"] = id;
            body["action"] = action;
            body["type"] = kind.Name;
            body["verified_by"] = CoRows.Col(row, "verifier");
            body["verified_at"] = state["verified_at"];
            return ApiResult.Ok(body);
        }

        static ApiResult FreshClose(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            Dictionary<string, object> row = FreshRow(ctx, kind, id);
            string closer = CoRows.Col(row, "closer");
            bool linesOpen = action != "open" || !FreshLineClosed(ctx, id);
            if ((action == "close") != (closer.Length > 0) || !linesOpen)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["action"] = action;
            body["closed"] = closer.Length > 0;
            body["closed_by"] = closer;
            body["closed_at"] = CoRows.Col(row, "closed_at");
            body["lines"] = new List<Dictionary<string, object>>();
            return ApiResult.Ok(body);
        }

        static ApiResult Gone(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = null;
            bool there;
            try
            {
                conn = ctx.OpenFresh();
                there = HeadRow(conn, id) != null;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Gone " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交删除但未能回读，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (there)
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

        // 打开后回读：还有行关闭人非空时算回读不符。
        static bool FreshLineClosed(WorkContext ctx, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return LineClosed(conn, id);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Fresh " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读状态，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 提交后的回读：连不上或读失败是 504；单据不见了是 409。
        static Dictionary<string, object> FreshRow(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = null;
            Dictionary<string, object> row;
            try
            {
                conn = ctx.OpenFresh();
                row = HeadRow(conn, id);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Fresh " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读状态，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
            }
            return row;
        }
    }
}
