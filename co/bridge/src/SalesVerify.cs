using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static class SalesVerify
    {
        // 退货单审核时 WorkItem.Kind 的取值（只在本类内用）。
        const string ReturnKind = "sale_return";

        // 发货单 VT 9；退货单（红字发货单）VT 10。卡片 03 与 01 的扩展列相同，读单 SQL 共用 AdoXml.VoucherSql。
        static int VtOf(WorkItem item)
        {
            return item.Kind == ReturnKind ? SaleReturn.RedVt : 9;
        }

        public static ApiResult LoginOk(U8Session session, string user)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["operator"] = user;
            body["operator_name"] = session.OperatorName ?? "";
            return ApiResult.Ok(body);
        }

        public static ApiResult Verify(object conn, U8Session session, WorkItem item)
        {
            bool sale = item.Kind == WorkItem.SaleKind;
            HeadState before = AdoXml.ReadHead(conn, sale, item.Id);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RefuseDispatch(sale, item.Kind == ReturnKind, before);
            // 销售订单被别的操作员锁定时不能审核、弃审（VoucherLockGate）。
            VoucherLockGate.Refuse(conn, sale ? "sale_order" : "", item.Id, session.OperatorName);
            WorkflowGate.Ensure(conn, sale, before);
            RefuseBefore(before, item.Action);
            SaleReturn.RefuseReturned(conn, item.Kind == WorkItem.DispatchKind, item.Action, item.Id);
            string u8msg = RunCo(conn, session, item, sale);
            if (u8msg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", u8msg);
            }
            HeadState after = Reread(session, item, sale);
            RefuseAfter(after, item.Action, session.OperatorName);
            return Success(item.Acc, item.Id, item.Action, after);
        }

        public static ApiResult VerifyKind(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            string itemKind = ItemKindOf(kind);
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 verify 或 unverify");
            }
            ctx.Item.Kind = itemKind;
            ctx.Item.Id = id;
            ctx.Item.Action = action;
            if (ctx.Item.Config == null)
            {
                ctx.Item.Config = ctx.Config;
            }
            ApiResult result = Verify(ctx.Conn, ctx.Session, ctx.Item);
            if (result.Body != null)
            {
                result.Body["type"] = kind.Name;
            }
            return result;
        }

        // 退货单：审核用 VT 10。锁键在入队时已按 type 定好，这里只影响 VT 与红蓝核对。
        static string ItemKindOf(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            if (name == "sale_order")
            {
                return WorkItem.SaleKind;
            }
            if (name == "dispatch")
            {
                return WorkItem.DispatchKind;
            }
            if (name == ReturnKind)
            {
                return ReturnKind;
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持直接审核");
        }

        static void RefuseDispatch(bool sale, bool red, HeadState head)
        {
            if (sale)
            {
                return;
            }
            if (red)
            {
                SaleReturn.RefuseVerify(head);
                return;
            }
            if (head.Red)
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字发货单");
            }
            if (head.VouchType != "05")
            {
                throw new BridgeException(400, "bad_request", "仅支持发货单类型 05");
            }
            if (head.First)
            {
                throw new BridgeException(400, "bad_request", "不支持期初发货单");
            }
        }

        static void RefuseBefore(HeadState head, string action)
        {
            if (action == "verify" && IsVerified(head))
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (action == "unverify" && IsBlank(head))
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
        }

        static void RefuseAfter(HeadState head, string action, string operatorName)
        {
            if (head == null)
            {
                throw new BridgeException(409, "state_mismatch", "审核状态与操作不一致");
            }
            if (action == "verify")
            {
                RequireVerifiedBy(head, operatorName);
                return;
            }
            if (!IsBlank(head))
            {
                throw new BridgeException(409, "state_mismatch", "弃审后审核人仍在");
            }
        }

        static void RequireVerifiedBy(HeadState head, string operatorName)
        {
            if (!IsVerified(head))
            {
                throw new BridgeException(409, "state_mismatch", "审核后审核人或审核日期为空");
            }
            string name = operatorName == null ? "" : operatorName.Trim();
            if (!string.Equals(head.Verifier, name, StringComparison.Ordinal))
            {
                throw new BridgeException(409, "state_mismatch", "审核人与登录操作员姓名不一致");
            }
        }

        static bool IsVerified(HeadState head)
        {
            return head.Verifier != null && head.Verifier.Length > 0
                && head.VerifyDate != null && head.VerifyDate.Length > 0;
        }

        static bool IsBlank(HeadState head)
        {
            string verifier = head.Verifier ?? "";
            string date = head.VerifyDate ?? "";
            return verifier.Length == 0 && date.Length == 0;
        }

        static HeadState Reread(U8Session session, WorkItem item, bool sale)
        {
            object conn = null;
            try
            {
                conn = AdoXml.Open(AdoXml.ConnectionString(session.Login, item.Config));
                CoTrans.LockWait(conn);
                return AdoXml.ReadHead(conn, sale, item.Id);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static ApiResult Success(string acc, int id, string action, HeadState head)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = acc;
            body["id"] = id;
            body["action"] = action;
            body["verified_by"] = head.Verifier ?? "";
            body["verified_at"] = head.VerifiedAt ?? "";
            return ApiResult.Ok(body);
        }

        // bManualTrans 写在 VoucherCO.Init 之后。事务在 VerifyVouch 之前由本进程 BeginTrans。
        static string RunCo(object conn, U8Session session, WorkItem item, bool sale)
        {
            object sys = null;
            object co = null;
            object dom = null;
            try
            {
                int vt = sale ? 12 : VtOf(item);
                SaSession.OpenSa(conn, session.Login, vt, out sys, out co);
                dom = AdoXml.LoadDom(conn, AdoXml.VoucherSql(conn, sale), item.Id);
                return FinishVerify(conn, co, dom, item);
            }
            finally
            {
                SaSession.CloseSa(sys);
                ComUtil.Final(dom);
                ComUtil.Final(co);
                ComUtil.Final(sys);
            }
        }

        static string FinishVerify(object conn, object co, object dom, WorkItem item)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                item.TranBefore = CoTrans.Count(conn);
                object ret = ComUtil.Call(co, "VerifyVouch", new object[] { dom, item.Action == "verify" });
                item.TranAfter = CoTrans.Count(conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                if (msg.Length > 0)
                {
                    CoRows.Note(item, msg);
                    CoTrans.Rollback(conn);
                    open = false;
                    return msg;
                }
                CoTrans.CommitSeen(conn);
                open = false;
                return "";
            }
            catch (Exception)
            {
                NoteTran(conn, item);
                RollbackOpen(conn, open);
                throw;
            }
        }

        static void NoteTran(object conn, WorkItem item)
        {
            if (item.TranAfter != null)
            {
                return;
            }
            try
            {
                item.TranAfter = CoTrans.Count(conn);
            }
            catch (Exception)
            {
            }
        }

        static void RollbackOpen(object conn, bool open)
        {
            if (!open)
            {
                return;
            }
            try
            {
                CoTrans.Rollback(conn);
            }
            catch (Exception)
            {
            }
        }

    }
}
