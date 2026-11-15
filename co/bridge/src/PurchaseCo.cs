using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static class PurchaseCo
    {
        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            int vt = Vt(kind);
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            PuRet.RequireRed(kind, snap);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.UseStored(ctx, kind, id);
                OpenFor(ctx, kind, vt, false, out info, out co);
                ReadPu(co, doms, id, ctx.Item);
                return CoRows.Pack(kind, id, snap, doms);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            int vt = Vt(kind);
            CheckAction(action);
            Dictionary<string, object> before = CoRows.HeadRow(ctx.Conn, kind, id);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Refuse(ctx, kind, before, action, id);
            VoucherLockGate.Refuse(ctx.Conn, kind.Name, id, ctx.OperatorName);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.UseStored(ctx, kind, id);
                OpenFor(ctx, kind, vt, true, out info, out co);
                ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                string err = Confirm(ctx, co, kind, doms, action);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
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

        static int Vt(VoucherKind kind)
        {
            if (kind != null && kind.Name == "purchase_order")
            {
                return 1;
            }
            if (kind != null && (kind.Name == "arrival" || kind.Name == "purchase_return"))
            {
                return 2;
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持");
        }

        static void CheckAction(string action)
        {
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 verify 或 unverify");
            }
        }

        static void Refuse(WorkContext ctx, VoucherKind kind, Dictionary<string, object> row, string action, int id)
        {
            RefuseBill(kind, row);
            RefuseFlow(ctx.Conn, kind, row);
            if (CoRows.Col(row, "closer").Length > 0 || CoRows.Col(row, "cstate") == "2")
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (action == "verify" && Verified(row))
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (action == "unverify" && Blank(row))
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (action == "unverify")
            {
                RefuseChild(ctx.Conn, kind, id);
            }
        }

        static void RefuseBill(VoucherKind kind, Dictionary<string, object> row)
        {
            if (kind.Name == "purchase_return")
            {
                PuRet.RequireRed(kind, row);
                return;
            }
            if (kind.Name != "arrival")
            {
                return;
            }
            string bill = CoRows.Col(row, "bill_type");
            if (bill.Length > 0 && bill != "0")
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字到货单");
            }
        }

        internal static void RefuseFlow(object conn, VoucherKind kind, Dictionary<string, object> row)
        {
            if (CoRows.FlagOf(row, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
            }
            string hit = FlowRelease(conn, kind.HeadTable);
            if (hit != null)
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
            }
        }

        static string FlowRelease(object conn, string table)
        {
            string sql = "select top 1 r.cBizObjectId from Table_WorkFlowRelease r "
                + "inner join AuditBizObjects o on o.cBizObjectId = r.cBizObjectId "
                + "where r.Status = 0 and o.srcTable = ? and r.cBizEventId = o.cBizObjectId + '.Submit'";
            try
            {
                return Rows.Scalar(conn, sql, new object[] { table });
            }
            catch (Exception ex)
            {
                if (CoRows.MissingTable(ex))
                {
                    throw new BridgeException(409, "workflow_unknown", "审批流表不可用");
                }
                throw;
            }
        }

        internal static void RefuseChild(object conn, VoucherKind kind, int id)
        {
            if (kind.Name == "arrival" || kind.Name == "purchase_return")
            {
                RefuseArrival(conn, id);
                return;
            }
            if (Child(conn, "select top 1 d.cbCloser from PO_Podetails d where d.POID=? and d.cbCloser is not null and ltrim(rtrim(d.cbCloser))<>''", id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (Child(conn, "select top 1 convert(varchar(20), a.iPOsID) from PU_ArrivalVouchs a where a.iPOsID<>0 and a.iPOsID in (select d.ID from PO_Podetails d where d.POID=?)", id)
                || Child(conn, "select top 1 convert(varchar(20), r.iPOsID) from rdrecords01 r where r.iPOsID<>0 and r.iPOsID in (select d.ID from PO_Podetails d where d.POID=?)", id)
                || Child(conn, "select top 1 convert(varchar(20), p.iPOsID) from PurBillVouchs p where p.iPOsID<>0 and p.iPOsID in (select d.ID from PO_Podetails d where d.POID=?)", id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
        }

        const string ArrivalBusySql = "select top 1 convert(varchar(20), s.Autoid) from PU_ArrivalVouchs s where s.ID=? and ("
            + "isnull(s.fInspectQuantity,0)<>0 or isnull(s.fValidInQuan,0)<>0 or isnull(s.fInValidInQuan,0)<>0 or "
            + "isnull(s.fKPQuantity,0)<>0 or isnull(s.fInspectNum,0)<>0 or isnull(s.fValidInNum,0)<>0 or "
            + "isnull(s.fInvalidInNum,0)<>0 or isnull(s.fRetQuantity,0)<>0 or isnull(s.fDegradeInQuantity,0)<>0)";
        const string ArrivalRdSql = "select top 1 convert(varchar(40), r.AutoID) from rdrecords01 r where r.iArrsId<>0 and r.iArrsId in (select s.Autoid from PU_ArrivalVouchs s where s.ID=?)";

        internal static void RefuseArrival(object conn, int id)
        {
            RefuseArrival(conn, id, false);
        }

        internal static void RefuseArrival(object conn, int id, bool deleting)
        {
            RefuseArrival(conn, id, deleting ? "不能删除" : "不能弃审");
        }

        // tail 是拒绝原文的后半句（不能删除 / 不能弃审 / 不能修改）。
        internal static void RefuseArrival(object conn, int id, string tail)
        {
            if (Child(conn, ArrivalBusySql, id))
            {
                throw new BridgeException(409, "state_mismatch", "到货单已报检或已入库，" + tail);
            }
            if (Child(conn, ArrivalRdSql, id))
            {
                throw new BridgeException(409, "state_mismatch", "到货单已有入库单，" + tail);
            }
        }

        static bool Child(object conn, string sql, int id)
        {
            return Rows.Scalar(conn, sql, new object[] { id }) != null;
        }

        // 修改、删除、关闭与到货单删除复用这条读取，行为与审核前的加载相同。
        internal static void ReadPu(object co, object[] doms, int id, WorkItem item)
        {
            string locate = "";
            object[] args = new object[] { doms[0], doms[1], "", id, locate };
            object ret = ComUtil.CallRef(co, "GetVoucherDataById", args, new int[] { 0, 1, 4 });
            CoRows.Swap(doms, 0, args[0]);
            CoRows.Swap(doms, 1, args[1]);
            string text = VariantText(ret);
            CoRows.Note(item, "GetVoucherDataById " + text);
            List<Dictionary<string, object>> rows = Rows.FromDom(doms[0], 1);
            if (rows != null && rows.Count > 0)
            {
                return;
            }
            if (text.Length > 0 && !AllDigits(text))
            {
                throw new BridgeException(409, "u8_rejected", text);
            }
        }

        static string Confirm(WorkContext ctx, object co, VoucherKind kind, object[] doms, string action)
        {
            bool open = false;
            object msgDom = null;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                object ret = Invoke(co, kind, doms, action, out msgDom);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(ctx.Item, action + " " + msg + DomText(msgDom));
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

        static object Invoke(object co, VoucherKind kind, object[] doms, string action, out object msgDom)
        {
            msgDom = null;
            if (kind.Name == "arrival" || kind.Name == "purchase_return")
            {
                string name = action == "verify" ? "ConfirmArr" : "CancelconfirmArr";
                return ComUtil.Call(co, name, new object[] { doms[0] });
            }
            if (action != "verify")
            {
                return ComUtil.Call(co, "CancelconfirmPO", new object[] { doms[0], doms[1] });
            }
            msgDom = Rows.NewDom();
            object[] args = new object[] { doms[0], msgDom, doms[1] };
            object ret = ComUtil.CallRef(co, "ConfirmPO", args, new int[] { 1 });
            if (args[1] != null && !object.ReferenceEquals(args[1], msgDom))
            {
                ComUtil.Final(msgDom);
                msgDom = args[1];
            }
            return ret;
        }

        static ApiResult Fresh(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = CoRows.HeadRow(conn, kind, id);
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", "审核状态与操作不一致");
                }
                if (action == "verify")
                {
                    RequireVerified(row, ctx.Session.OperatorName);
                }
                else if (!Blank(row))
                {
                    throw new BridgeException(409, "state_mismatch", "弃审后审核人仍在");
                }
                return VerifyBody(ctx.Item, kind, id, action, row);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static void RequireVerified(Dictionary<string, object> row, string operatorName)
        {
            if (!Verified(row))
            {
                throw new BridgeException(409, "state_mismatch", "审核后审核人或审核日期为空");
            }
            string name = operatorName == null ? "" : operatorName.Trim();
            if (!string.Equals(CoRows.Col(row, "verifier"), name, StringComparison.Ordinal))
            {
                throw new BridgeException(409, "state_mismatch", "审核人与登录操作员姓名不一致");
            }
        }

        static ApiResult VerifyBody(WorkItem item, VoucherKind kind, int id, string action, Dictionary<string, object> row)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = item.Acc;
            body["id"] = id;
            body["action"] = action;
            body["verified_by"] = CoRows.Col(row, "verifier");
            body["verified_at"] = Verified(row) ? When(row) : "";
            body["type"] = kind.Name;
            return ApiResult.Ok(body);
        }

        static bool Verified(Dictionary<string, object> row)
        {
            return CoRows.Col(row, "verifier").Length > 0 && CoRows.Col(row, "verify_date").Length > 0;
        }

        static bool Blank(Dictionary<string, object> row)
        {
            return CoRows.Col(row, "verifier").Length == 0
                && CoRows.Col(row, "verify_date").Length == 0;
        }

        static string When(Dictionary<string, object> row)
        {
            string sys = CoRows.Col(row, "verify_sys");
            return sys.Length > 0 ? sys : CoRows.Col(row, "verify_date");
        }

        // 采购退货单（红字到货单）用 PuRet 的红字 Init，其余沿用 PuSession。
        static void OpenFor(WorkContext ctx, VoucherKind kind, int vt, bool verify, out object info, out object co)
        {
            if (kind.Name == "purchase_return")
            {
                PuRet.Open(ctx, false, verify, out info, out co);
                return;
            }
            PuSession.Open(ctx, vt, verify, out info, out co);
        }

        static string DomText(object dom)
        {
            if (dom == null)
            {
                return "";
            }
            try
            {
                string xml = Values.Text(ComUtil.Get(dom, "xml")).Trim();
                if (xml.Length > 120)
                {
                    return " " + xml.Substring(0, 120);
                }
                if (xml.Length == 0)
                {
                    return "";
                }
                return " " + xml;
            }
            catch (Exception)
            {
                return "";
            }
        }

        static string VariantText(object value)
        {
            if (value == null || value is DBNull || value == Type.Missing)
            {
                return "";
            }
            return Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture);
        }

        static bool AllDigits(string text)
        {
            if (text == null || text.Length == 0)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }
    }
}
