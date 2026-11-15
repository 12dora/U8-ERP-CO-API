using System;
using System.Collections.Generic;

namespace U8Co
{
    // 请购单：读取、审核 / 弃审、删除、整单关闭 / 打开。新增、修改见 PuAppEdit，事务与回读见 PuAppSave。
    // CO 是 VoucherCO_PU，登录子系统 PU；Init 走 PuSession（sBillType 空串、普通采购、CG）。
    internal static partial class PuApp
    {
        // VoucherCO_PU 的 vouchertype 枚举：采购订单 1、到货单 2、采购发票 4 已实测；请购单 0 是推断，未经实测。
        internal const int Vt = 0;
        // true：读取走 CO 的 GetVoucherDataById（同采购订单）；false：只走 SQL。CO 读取不通时改成 false，未经实测。
        static readonly bool LoadByCo = true;
        const string LineClosedSql = "select top 1 convert(varchar(20), s.AutoID) from PU_AppVouchs s "
            + "where s.ID=? and s.cbcloser is not null and ltrim(rtrim(s.cbcloser))<>''";
        // 已被采购订单参照（PO_Podetails.iAppIds），或已有累计订货、合同、询价数量。
        const string OrderedSql = "select top 1 convert(varchar(20), d.ID) from PO_Podetails d "
            + "where d.iAppIds in (select s.AutoID from PU_AppVouchs s where s.ID=?)";
        const string BusySql = "select top 1 convert(varchar(20), s.AutoID) from PU_AppVouchs s where s.ID=? and ("
            + "isnull(s.iReceivedQTY,0)<>0 or isnull(s.iReceivedNum,0)<>0 or isnull(s.fconquantity,0)<>0 or "
            + "isnull(s.fconnum,0)<>0 or isnull(s.iSumXJqty,0)<>0 or isnull(s.iSumXJCGqty,0)<>0)";

        const string LockRowsSql = "select count(*) from PU_AppVouchs s with (updlock, holdlock) where s.ID=?";

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> snap = HeadRow(ctx.Conn, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (!LoadByCo)
            {
                return SqlLoad(ctx, kind, id, snap);
            }
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.Open(ctx, Vt, false, out info, out co);
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                return CoRows.Pack(kind, id, snap, doms);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 verify 或 unverify");
            }
            Gate(ctx, kind, id, action);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.Open(ctx, Vt, true, out info, out co);
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                string err = ConfirmTran(ctx, co, doms, action, id);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                return FreshVerify(ctx, kind, id, action);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            Gate(ctx, kind, id, "delete");
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.Open(ctx, Vt, false, out info, out co);
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                DeleteTran(ctx, co, doms, id);
                return Gone(ctx, kind, id);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        // 只做整单关闭 / 打开（CloseApp / OpenApp）；按行关闭不支持。
        public static ApiResult Close(WorkContext ctx, VoucherKind kind, int id, string action, int[] lineIds)
        {
            if (action != "close" && action != "open")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 close 或 open");
            }
            if (lineIds != null)
            {
                throw new BridgeException(400, "bad_request", "请购单只支持整单关闭或打开");
            }
            Gate(ctx, kind, id, action);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.Open(ctx, Vt, false, out info, out co);
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                CloseTran(ctx, co, doms, action);
                return FreshClose(ctx, kind, id, action);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        // 登录后、调 CO 前的闸门。op：update / delete / verify / unverify / close / open。
        internal static Dictionary<string, object> Gate(WorkContext ctx, VoucherKind kind, int id, string op)
        {
            Dictionary<string, object> row = HeadRow(ctx.Conn, id);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            PurchaseCo.RefuseFlow(ctx.Conn, kind, row);
            string bus = CoRows.Col(row, "bus_type");
            if (bus.Length > 0 && bus != PuAppFields.Bus)
            {
                throw new BridgeException(409, "state_mismatch", "只支持普通采购的请购单");
            }
            if (op == "close" || op == "open")
            {
                GateClose(row, op, Closed(ctx.Conn, row, id));
                return row;
            }
            if (Closed(ctx.Conn, row, id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            GateVerify(row, op);
            if (op != "verify")
            {
                RefuseChild(ctx.Conn, id, op);
            }
            return row;
        }

        static void GateVerify(Dictionary<string, object> row, string op)
        {
            bool verified = Verified(row);
            if (op == "unverify" && !verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (op != "unverify" && verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
        }

        // anyShut：表头或任一行已关闭。只关了部分行的单据也能打开（同采购订单）。
        static void GateClose(Dictionary<string, object> row, string op, bool anyShut)
        {
            bool shut = CoRows.Col(row, "closer").Length > 0;
            if (op == "close" && !Verified(row))
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (op == "close" && shut)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (op == "open" && !anyShut)
            {
                throw new BridgeException(409, "state_mismatch", "单据未关闭");
            }
        }

        internal static bool LineClosed(object conn, int id)
        {
            return Rows.Scalar(conn, LineClosedSql, new object[] { id }) != null;
        }

        static bool Closed(object conn, Dictionary<string, object> row, int id)
        {
            if (CoRows.Col(row, "closer").Length > 0)
            {
                return true;
            }
            return Rows.Scalar(conn, LineClosedSql, new object[] { id }) != null;
        }

        // 事务里在调 CO 之前再查一遍下游：请购行加 UPDLOCK/HOLDLOCK，挡住并发的参照生单回写。
        internal static void RefuseChildLocked(object conn, int id, string op)
        {
            Rows.Scalar(conn, LockRowsSql, new object[] { id });
            RefuseChild(conn, id, op);
        }

        static void RefuseChild(object conn, int id, string op)
        {
            string tail = op == "unverify" ? "不能弃审" : (op == "delete" ? "不能删除" : "不能修改");
            if (Rows.Scalar(conn, OrderedSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "请购单已被采购订单参照，" + tail);
            }
            if (Rows.Scalar(conn, BusySql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "请购单已有累计订货、合同或询价数量，" + tail);
            }
        }

        // 不走 CoRows.HeadRow：列名照 PU_AppVouch，别名与 CoRows.StateOf 一致。
        const string HeadSql = "select cVerifier as verifier, cAuditDate as verify_date, cCode as code, "
            + "cAuditTime as verify_sys, cCloser as closer, IsWfControlled as wf, cBusType as bus_type, "
            + "dCloseTime as closed_at from PU_AppVouch where ID=?";

        internal static Dictionary<string, object> HeadRow(object conn, int id)
        {
            return Rows.One(conn, HeadSql, new object[] { id });
        }

        internal static bool Verified(Dictionary<string, object> row)
        {
            return CoRows.Col(row, "verifier").Length > 0;
        }

        // LoadByCo=false 时的读取：表头、表体各一条 SELECT，表体最多 500 行。
        static ApiResult SqlLoad(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> snap)
        {
            Dictionary<string, object> head = Rows.One(ctx.Conn, "select * from PU_AppVouch where ID=?", new object[] { id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn,
                "select * from PU_AppVouchs where ID=? order by AutoID", new object[] { id }, 501)
                ?? new List<Dictionary<string, object>>();
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(snap, "code");
            body["head"] = head;
            if (lines.Count > 500)
            {
                lines.RemoveAt(500);
                body["lines_truncated"] = true;
            }
            body["lines"] = lines;
            body["state"] = CoRows.StateOf(snap);
            return ApiResult.Ok(body);
        }

        static void Release(object info, object co, object[] doms)
        {
            ComUtil.Final(doms[1]);
            ComUtil.Final(doms[0]);
            ComUtil.Final(co);
            ComUtil.Final(info);
        }
    }
}
