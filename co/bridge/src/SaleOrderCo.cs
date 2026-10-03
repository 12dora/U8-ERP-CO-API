using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static class SaleOrderCo
    {
        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, SaSession.SaVt(kind), out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                return CoRows.Pack(kind, id, snap, doms);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "sale_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
            if (head == null)
            {
                throw new BridgeException(400, "bad_request", "表头必须是对象");
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, SaSession.SaVt(kind), out sys, out co);
                string card = kind.SaCard == null || kind.SaCard.Length == 0 ? "17" : kind.SaCard;
                SoDom.Templates(co, ctx.Conn, card, ctx.Item, doms);
                SoDom.Apply(ctx.Conn, doms, head, lines);
                // Save 不算价税。按单行 BodyCheck 回写单价、金额和本币金额。
                SaleCalc.ForCreate(co, doms[0], doms[1], lines);
                SoSave.Stamp(ctx, sys, doms[0], card);
                string code;
                int id = SoSave.SaveNew(ctx.Conn, co, doms, ctx.Item, out code);
                return Created(ctx, kind, id, code);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            if (kind == null || kind.Name != "sale_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
            }
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            VoucherLockGate.Refuse(ctx.Conn, kind.Name, id, ctx.OperatorName);
            RefuseDelete(ctx.Conn, kind, id, snap);
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, SaSession.SaVt(kind), out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SoSave.RunDelete(ctx.Conn, co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                return Gone(ctx, kind, id);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        static void RefuseDelete(object conn, VoucherKind kind, int id, Dictionary<string, object> snap)
        {
            RefuseWritable(conn, kind, id, snap, "单据正在审批，不能删除");
        }

        // 未审核、未关闭、无下游、不在审批中。删除和修改走同一组门，审批文案按动作区分。
        internal static void RefuseWritable(object conn, VoucherKind kind, int id, Dictionary<string, object> snap, string wfMessage)
        {
            if (CoRows.Col(snap, "verifier").Length > 0 || CoRows.Col(snap, "verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (CoRows.Col(snap, "closer").Length > 0 || Scalar(conn, ClosedSql, id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (Scalar(conn, DispatchSql, id) || Scalar(conn, BillSql, id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
            RefuseRunning(conn, kind, id, snap, wfMessage);
        }

        const string ClosedSql = "select top 1 s.cSCloser from SO_SODetails s where s.ID=? "
            + "and s.cSCloser is not null and ltrim(rtrim(s.cSCloser))<>''";
        const string DispatchSql = "select top 1 convert(varchar(20), d.iSOsID) from DispatchLists d "
            + "where d.iSOsID<>0 and d.iSOsID in (select s.iSOsID from SO_SODetails s where s.ID=? and s.iSOsID<>0)";
        const string BillSql = "select top 1 convert(varchar(20), b.iSOsID) from SaleBillVouchs b "
            + "where b.iSOsID<>0 and b.iSOsID in (select s.iSOsID from SO_SODetails s where s.ID=? and s.iSOsID<>0)";
        const string RunningSql = "select top 1 convert(varchar(20), f.ID) from WF_ActiveFlow f "
            + "where f.FlagCode=0 and RTRIM(f.VoucherId)=convert(varchar(20), ?) "
            + "and RTRIM(f.VoucherType) in (select o.cBizObjectId from AuditBizObjects o where o.srcTable=?)";
        const string BizSql = "select top 1 o.cBizObjectId from AuditBizObjects o where o.srcTable=?";

        internal static void RefuseRunning(object conn, VoucherKind kind, int id, Dictionary<string, object> snap, string wfMessage)
        {
            if (!CoRows.FlagOf(snap, "wf"))
            {
                return;
            }
            if (InFlow(snap) || Active(conn, kind, id) != null)
            {
                throw new BridgeException(409, "workflow_enabled", wfMessage);
            }
        }

        // 已提交（iverifystate>0）即使没有业务对象行也拒绝。
        static bool InFlow(Dictionary<string, object> snap)
        {
            decimal state;
            string text = CoRows.Col(snap, "vstate");
            if (!decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out state))
            {
                return false;
            }
            return state > 0m;
        }

        static string Active(object conn, VoucherKind kind, int id)
        {
            if (!HasBiz(conn, kind.HeadTable))
            {
                throw new BridgeException(409, "workflow_unknown", "AuditBizObjects 没有该单据表");
            }
            try
            {
                return Rows.Scalar(conn, RunningSql, new object[] { id, kind.HeadTable });
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

        static bool HasBiz(object conn, string table)
        {
            try
            {
                return Rows.Scalar(conn, BizSql, new object[] { table }) != null;
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

        static bool Scalar(object conn, string sql, int id)
        {
            return Rows.Scalar(conn, sql, new object[] { id }) != null;
        }

        static ApiResult Created(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                if (id <= 0)
                {
                    id = IdByCode(conn, code);
                }
                if (id <= 0)
                {
                    throw new BridgeException(504, "outcome_unknown", Unknown(code));
                }
                Dictionary<string, object> row = CoRows.HeadRow(conn, kind, id);
                if (row == null || CoRows.Col(row, "code").Length == 0)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["type"] = kind.Name;
                body["id"] = id;
                body["code"] = CoRows.Col(row, "code");
                body["state"] = CoRows.StateOf(row);
                return ApiResult.Ok(body);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                // 订单已经提交；回读失败时结果未知，不能报成普通错误让调用方重试。
                CoRows.Note(ctx.Item, "Created " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(code));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static int IdByCode(object conn, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return 0;
            }
            List<Dictionary<string, object>> rows = Rows.Query(
                conn, "select ID from SO_SOMain where cSOCode=?", new object[] { no }, 2);
            if (rows == null || rows.Count != 1)
            {
                return 0;
            }
            return CoRows.AsId(CoRows.Col(rows[0], "ID"));
        }

        static string Unknown(string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return "已保存但未能确定单据标识";
            }
            return "已保存但未能确定单据标识，单号 " + no;
        }

        internal static ApiResult Gone(WorkContext ctx, VoucherKind kind, int id)
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
    }
}
