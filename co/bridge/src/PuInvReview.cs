using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票复核 / 取消复核（采购管理的「复核」，写 cVerifier、cAuditDate、cAuditTime、iverifystateex）。
    // 不是应付款管理的审核（cPBVVerifier、Ap_Detail），那一步见 ArapAudit（action arap_verify / arap_unverify）。
    // 与删除同一 Init（vt 4，purbill / ppurbill），SetVerifyMode(true)，GetVoucherDataById(h, b, "", pbvid, "") 引用 {0,1,4}；
    // 复核 ConfirmBill(h, false) 引用 {1}，取消复核 CancelconfirmBill(h)；返回空为成功，都在 CoTrans 里（测试账套实测可回滚）。
    internal static partial class PuInv
    {
        const string ReviewedSql = "select h.cVerifier, convert(varchar(10), h.cAuditDate, 23) as AuditDate"
            + " from PurBillVouch h where h.PBVID=?";

        public static ApiResult Review(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 verify 或 unverify");
            }
            Dictionary<string, object> row = Rows.One(ctx.Conn, DelSql, new object[] { id });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            string bill = BillKey(CoRows.Col(row, "cPBVBillType"));
            if (CoRows.Col(row, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持复核普通采购的发票");
            }
            GateReview(ctx, kind, id, row, action);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                Open(ctx, 4, bill, PositiveOf(ctx.Conn, id), out info, out co);
                ComUtil.Call(co, "SetVerifyMode", new object[] { true });
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                ReviewTran(ctx, co, doms, id, action);
                return Reviewed(ctx, kind, id, action);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        // 登录后、调用前的闸门。审批流、复核状态；取消复核另查应付审核、结算、现付、应付明细。
        static void GateReview(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> row, string action)
        {
            if (CoRows.Col(row, "Wf") == "1")
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，不能直接复核");
            }
            Dictionary<string, object> wf = new Dictionary<string, object>();
            wf["wf"] = CoRows.Col(row, "Wf");
            PurchaseCo.RefuseFlow(ctx.Conn, kind, wf);
            bool reviewed = CoRows.Col(row, "cVerifier").Length > 0;
            if (action == "verify")
            {
                if (reviewed)
                {
                    throw new BridgeException(409, "state_mismatch", "发票已复核");
                }
                return;
            }
            if (!reviewed)
            {
                throw new BridgeException(409, "state_mismatch", "发票未复核");
            }
            GateUnreview(ctx.Conn, id, row);
        }

        static void GateUnreview(object conn, int id, Dictionary<string, object> row)
        {
            if (CoRows.Col(row, "cPBVVerifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "已在应付款管理审核，请先弃审");
            }
            if (CoRows.Col(row, "SDate").Length > 0 || Rows.Scalar(conn, SettleSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票已结算");
            }
            if (CoRows.Col(row, "Paid") != "0")
            {
                throw new BridgeException(409, "state_mismatch", "发票已现付");
            }
            object[] key = new object[] { CoRows.Col(row, "cPBVBillType"), CoRows.Col(row, "cPBVCode") };
            if (Rows.Scalar(conn, ApSql, key) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票已有应付明细");
            }
        }

        static void ReviewTran(WorkContext ctx, object co, object[] doms, int id, string action)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                string msg = InvokeReview(ctx, co, doms, action);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                CoRows.Note(ctx.Item, action + " " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                bool want = action == "verify";
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireReviewed(conn, id, want); },
                    "PBVID " + id.ToString(CultureInfo.InvariantCulture));
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // ConfirmBill 的第二个参数是 out bTask（是否生成了待办），只记审计，不影响结果。
        static string InvokeReview(WorkContext ctx, object co, object[] doms, string action)
        {
            if (action != "verify")
            {
                return Values.Text(ComUtil.Call(co, "CancelconfirmBill", new object[] { doms[0] })).Trim();
            }
            object[] args = new object[] { doms[0], false };
            object ret = ComUtil.CallRef(co, "ConfirmBill", args, new int[] { 1 });
            CoRows.Note(ctx.Item, "ConfirmBill bTask=" + Values.Text(args[1]));
            return Values.Text(ret).Trim();
        }

        // 提交前在同一事务里确认 U8 确实写了（或清了）复核人，没写就回滚。
        static void RequireReviewed(object conn, int id, bool want)
        {
            Dictionary<string, object> row = Rows.One(conn, ReviewedSql, new object[] { id });
            bool has = row != null && CoRows.Col(row, "cVerifier").Length > 0;
            if (has != want)
            {
                throw new BridgeException(409, "u8_rejected", want ? "U8 没有写入复核人" : "U8 没有清除复核人");
            }
        }

        // 已提交。新连接（不加 NOLOCK）回读复核人；读不出来是 504，读出来对不上是 409。
        static ApiResult Reviewed(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            object conn = null;
            Dictionary<string, object> row;
            try
            {
                conn = ctx.OpenFresh();
                row = Rows.One(conn, ReviewedSql, new object[] { id });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "PuInvReview " + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "已提交但未能回读复核状态，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            return ReviewBody(ctx, kind, id, action, row);
        }

        static ApiResult ReviewBody(WorkContext ctx, VoucherKind kind, int id, string action, Dictionary<string, object> row)
        {
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
            }
            string reviewer = CoRows.Col(row, "cVerifier");
            string at = CoRows.Col(row, "AuditDate");
            if (action == "verify")
            {
                string name = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
                if (reviewer.Length == 0 || at.Length == 0)
                {
                    throw new BridgeException(409, "state_mismatch", "复核后复核人或复核日期为空");
                }
                if (!string.Equals(reviewer, name, StringComparison.Ordinal))
                {
                    throw new BridgeException(409, "state_mismatch", "复核人与登录操作员姓名不一致");
                }
            }
            else if (reviewer.Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "取消复核后复核人仍在");
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["id"] = id;
            body["action"] = action;
            body["verified_by"] = reviewer;
            body["verified_at"] = action == "verify" ? at : "";
            body["type"] = kind.Name;
            return ApiResult.Ok(body);
        }
    }
}
