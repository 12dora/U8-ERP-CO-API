using System;

namespace U8Co
{
    // UFLTMService 自己开连接。不能再套本进程的 ADO 事务。
    // UFTS 只用 Rows.Ufts 的结果（去掉末尾空格，左侧空格保留）。
    internal static class WfLtm
    {
        const string LtmId = "UFLTMService.clsService";
        const string PiId = "QMWorkFlowSrv.clsQMFinalVerifyPI";

        public static string Submit(WorkContext ctx, VoucherKind kind, int id)
        {
            return Ltm(ctx, kind, id, "", true);
        }

        public static string Withdraw(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            return Ltm(ctx, kind, id, code, false);
        }

        static string Ltm(WorkContext ctx, VoucherKind kind, int id, string code, bool submit)
        {
            string ufts = Rows.Ufts(ctx.Conn, kind.HeadTable, kind.IdColumn, id);
            if (ufts == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return LtmRun(ctx, kind, id, code, ufts, submit);
        }

        static string LtmRun(WorkContext ctx, VoucherKind kind, int id, string code, string ufts, bool submit)
        {
            object ltm = null;
            object pi = null;
            bool started = false;
            bool open = false;
            bool called = false;
            bool sealing = false;
            try
            {
                ltm = ComUtil.Create(LtmId);
                if (ltm == null)
                {
                    throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
                }
                ComUtil.Call(ltm, "Start", new object[] { DbName(ctx) });
                started = true;
                ComUtil.Call(ltm, "BeginTransaction", new object[0]);
                open = true;
                // 实测顺序：Start、BeginTransaction 之后才创建 clsQMFinalVerifyPI。
                pi = ComUtil.Create(PiId);
                if (pi == null)
                {
                    throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
                }
                // 六个参数全部 by-ref（含登录），本次登录不放回缓存（LoginCache）。
                ctx.DropLogin();
                called = true;
                PiResult result = submit
                    ? Pi(pi, "DoSubmit", SubmitArgs(ctx, kind, id, ufts), 5)
                    : Pi(pi, "UndoSubmit", UndoArgs(ctx, kind, id, ufts, code), 4);
                return Finish(ctx, ltm, result, ref open, ref sealing);
            }
            catch (BridgeException ex)
            {
                Freeze(ex, sealing, ref open);
                throw;
            }
            catch (Exception ex)
            {
                if (!called) throw;
                return SealFault(ctx, ex, sealing, ref open);
            }
            finally
            {
                if (open) Quiet(ltm, "Rollback");
                if (started) Quiet(ltm, "Finish");
                ComUtil.Final(pi);
                ComUtil.Final(ltm);
            }
        }

        // 先回滚再回读。未提交的锁会挡住新连接，而且异常之后不能 Commit。
        static string Finish(WorkContext ctx, object ltm, PiResult result, ref bool open, ref bool sealing)
        {
            if (result.Threw)
            {
                Quiet(ltm, "Rollback");
                open = false;
                return Workflow.Rescue(ctx, result.Cause);
            }
            if (!result.Ok)
            {
                Quiet(ltm, "Rollback");
                open = false;
                return WfProxy.Done(ctx, false, result.Err, false);
            }
            sealing = true;
            ComUtil.Call(ltm, "Commit", new object[0]);
            open = false;
            return WfProxy.Done(ctx, true, result.Err, false);
        }

        // 六个参数都是 ByRef。单据号用字符串，和业务号、UFTS 一样。
        static object[] SubmitArgs(WorkContext ctx, VoucherKind kind, int id, string ufts)
        {
            string biz = kind.BizObjectId;
            return new object[] { biz, biz + ".Submit", WfProxy.IdText(id), ctx.Session.Login, ufts, "" };
        }

        static object[] UndoArgs(WorkContext ctx, VoucherKind kind, int id, string ufts, string code)
        {
            string biz = kind.BizObjectId;
            string shown = code == null ? "" : code;
            return new object[] { WfProxy.IdText(id), biz, ctx.Session.Login, ufts, "", shown };
        }

        static PiResult Pi(object pi, string method, object[] args, int errAt)
        {
            try
            {
                object ret = ComUtil.CallRef(pi, method, args, AllRef(args.Length));
                PiResult result = new PiResult();
                result.Ok = Values.Flag(ret);
                result.Err = WfProxy.Text(args[errAt]);
                return result;
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                PiResult noted = new PiResult();
                noted.Threw = true;
                noted.Cause = ex;
                return noted;
            }
        }

        static string DbName(WorkContext ctx)
        {
            return Values.Text(ComUtil.Get(ctx.Session.Login, "UfDbName"));
        }

        static int[] AllRef(int n)
        {
            int[] refs = new int[n];
            for (int i = 0; i < n; i++) refs[i] = i;
            return refs;
        }

        static void Quiet(object target, string method)
        {
            if (target == null) return;
            try
            {
                ComUtil.Call(target, method, new object[0]);
            }
            catch (Exception)
            {
            }
        }

        static string SealFault(WorkContext ctx, Exception ex, bool sealing, ref bool open)
        {
            try
            {
                string note = Workflow.Rescue(ctx, ex);
                open = false;
                return note;
            }
            catch (BridgeException bridge)
            {
                Freeze(bridge, sealing, ref open);
                throw;
            }
        }

        // Commit 可能已经落库。结果对不上或读不出来时不再回滚。
        static void Freeze(BridgeException ex, bool sealing, ref bool open)
        {
            if (!sealing || ex == null) return;
            if (ex.Code == "outcome_unknown" || ex.Code == "state_mismatch") open = false;
        }

        sealed class PiResult
        {
            public bool Ok;
            public string Err;
            public bool Threw;
            public Exception Cause;
        }
    }
}
