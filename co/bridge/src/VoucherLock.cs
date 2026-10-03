using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售订单锁定与解锁（vouchers/lock，action=lock|unlock）。锁定人写在表头 cLocker（操作员姓名）。
    // 走 VoucherCO_Sa.LockVouch（VT 12，读单 SQL 同 SaleEditClose），在请求连接的 CoTrans 里，提交前在同一连接上核对 cLocker，
    // 提交后在新连接上回读。只做整单锁定；按行锁定（LockVouch 第三个参数 iSID）不做。
    // 采购订单锁定暂不支持：VoucherCO_PU.DoLock 实测一律回「本张已经被修改,不能锁定.」，登录前 400（Requests.GuardLock）。
    // 采购订单的 cLocker（U8 客户端锁定的）仍由 VoucherLockGate 挡住别的操作员。见 docs/u8-notes.md「销售订单 / 采购订单锁定」。
    internal static class VoucherLock
    {
        public const string SaLockRule = "write:sale_order:lock";
        public const string SaUnlockRule = "write:sale_order:unlock";
        public const string PuUnsupported = "采购订单锁定暂不支持（U8 采购组件 DoLock 一律拒绝），请在 U8 客户端锁定";

        const string SaStateSql = "select cVerifier as verifier, dverifydate as verify_date, cLocker as locker, "
            + "cCusCode, cDepCode, cPersonCode, cSTCode from SO_SOMain where ID=?";
        const string SaLockerSql = "select cLocker as locker from SO_SOMain where ID=?";
        const string PuLockerSql = "select cLocker as locker from PO_Pomain where POID=?";

        public static bool Lockable(VoucherKind kind)
        {
            return kind != null && kind.Name == "sale_order";
        }

        public static ApiResult Run(WorkContext ctx)
        {
            VoucherKind kind = ctx == null || ctx.Item == null ? null : ctx.Item.Type;
            Refuse(kind);
            int id = ctx.Item.Id;
            string action = ctx.Item.Action;
            bool locking = WantLock(action);
            Dictionary<string, object> state = Rows.One(ctx.Conn, SaStateSql, new object[] { id });
            if (state == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Permit(ctx, locking, state);
            string user = User(ctx);
            string held = CoRows.Col(state, "locker");
            if (Gate(kind.Name, Verified(state), held, user, locking))
            {
                // 本人已锁定（例如上次锁定 504 后重试）：不再调用 U8，按当前状态 200，already=true。
                ApiResult same = Body(kind, id, action, held);
                same.Body["already"] = true;
                return same;
            }
            RunSa(ctx, id, locking);
            return ReadBack(ctx, kind, id, action, locking);
        }

        // 登录前（Requests.GuardLock）和处理时共用：采购订单单独说明原因，其他类型 400。
        internal static void Refuse(VoucherKind kind)
        {
            if (kind != null && kind.Name == "purchase_order")
            {
                throw new BridgeException(400, "bad_request", PuUnsupported);
            }
            if (!Lockable(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持锁定");
            }
        }

        internal static bool WantLock(string action)
        {
            if (action == "lock")
            {
                return true;
            }
            if (action == "unlock")
            {
                return false;
            }
            throw new BridgeException(400, "bad_request", "action 只能是 lock 或 unlock");
        }

        // 纯判断（自检覆盖）。返回 true 表示本人已锁定、锁定请求直接按成功返回（幂等重试）。
        // 别人已锁定 409「单据已被 X 锁定」；已审核的销售订单不能锁定（U8 回「销售订单已经审核!」）；
        // 未锁定就解锁 409；解锁只认锁定人本人（按 U8 写入 cLocker 的方式比较姓名），别人解锁 409「单据由 X 锁定」。
        internal static bool Gate(string kindName, bool verified, string locker, string user, bool locking)
        {
            string held = locker == null ? "" : locker.Trim();
            if (locking)
            {
                return GateLock(kindName, verified, held, user);
            }
            if (held.Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未锁定");
            }
            if (!VoucherLockGate.SameUser(held, user))
            {
                throw new BridgeException(409, "state_mismatch", "单据由 " + held + " 锁定");
            }
            return false;
        }

        static bool GateLock(string kindName, bool verified, string held, string user)
        {
            if (held.Length > 0 && VoucherLockGate.SameUser(held, user))
            {
                return true;
            }
            if (held.Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已被 " + held + " 锁定");
            }
            if (verified && kindName == "sale_order")
            {
                throw new BridgeException(409, "state_mismatch", "已审核的销售订单不能锁定");
            }
            return false;
        }

        static bool Verified(Dictionary<string, object> state)
        {
            return CoRows.Col(state, "verifier").Length > 0 || CoRows.Col(state, "verify_date").Length > 0;
        }

        // 功能权限：销售订单卡片 SA_17voucher 的 lock / unlock（SA03010108 / SA03010109），UFMeta AA_FormButtonAuths。
        // 数据权限按表头对象。
        static void Permit(WorkContext ctx, bool locking, Dictionary<string, object> state)
        {
            PermRule rule = PermRegistry.ForKey(locking ? SaLockRule : SaUnlockRule);
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, rule);
            if (!PermCheck.RowAllowed(perm, rule, state))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
        }

        internal static string LockerSql(string kindName)
        {
            if (kindName == "sale_order")
            {
                return SaLockerSql;
            }
            if (kindName == "purchase_order")
            {
                return PuLockerSql;
            }
            return null;
        }

        static string User(WorkContext ctx)
        {
            string user = ctx.OperatorName;
            return user == null ? "" : user.Trim();
        }

        static void RunSa(WorkContext ctx, int id, bool locking)
        {
            object sys = null;
            object co = null;
            object[] doms = new object[1];
            bool open = false;
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, 12, out sys, out co);
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                doms[0] = AdoXml.LoadDom(ctx.Conn, AdoXml.VoucherSql(ctx.Conn, true), id);
                object[] args = new object[] { doms[0], locking };
                object ret = ComUtil.CallRef(co, "LockVouch", args, new int[] { 0 });
                CoRows.Swap(doms, 0, args[0]);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, "LockVouch " + (locking ? "1 " : "0 ") + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                Confirm(ctx, "sale_order", id, locking);
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
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                SaSession.CloseSa(sys);
                ComUtil.Final(sys);
            }
        }

        // 提交前在请求连接上核对 cLocker 已到目标状态。U8 已自行结束事务时 AfterCheck 改报 504。
        static void Confirm(WorkContext ctx, string kindName, int id, bool locking)
        {
            string sql = LockerSql(kindName);
            StockCall.AfterCheck(ctx, delegate(object conn)
            {
                Dictionary<string, object> row = Rows.One(conn, sql, new object[] { id });
                bool held = row != null && CoRows.Col(row, "locker").Length > 0;
                if (held != locking)
                {
                    throw new BridgeException(409, "u8_rejected", locking ? "U8 没有锁定单据" : "U8 没有解锁单据");
                }
            }, "锁定状态");
        }

        static ApiResult ReadBack(WorkContext ctx, VoucherKind kind, int id, string action, bool locking)
        {
            object conn = null;
            string locker;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = Rows.One(conn, LockerSql(kind.Name), new object[] { id });
                if (row == null)
                {
                    throw new BridgeException(504, "outcome_unknown", "已提交但回读锁定状态失败");
                }
                locker = CoRows.Col(row, "locker");
            }
            catch (BridgeException ex)
            {
                CoRows.Note(ctx.Item, "LockRead " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但回读锁定状态失败");
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "LockRead " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但回读锁定状态失败");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if ((locker.Length > 0) != locking)
            {
                throw new BridgeException(504, "outcome_unknown", "已提交但回读的锁定状态与请求不符");
            }
            return Body(kind, id, action, locker);
        }

        static ApiResult Body(VoucherKind kind, int id, string action, string locker)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["action"] = action;
            body["locked"] = locker.Length > 0;
            body["locker"] = locker;
            return ApiResult.Ok(body);
        }
    }
}
