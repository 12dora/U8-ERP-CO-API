using System;

namespace U8Co
{
    // 任务在工作线程上的执行体。登录对象和 COM 只在创建它的线程上用，用完在同一线程释放。
    internal static class StaExec
    {
        // 写线程：每个任务一次登录（开了 loginReuse 时按 LoginCache 复用本线程的登录）。
        // 登录成功后把连接串记进缓存，读线程靠它免登录。
        public static ApiResult Write(WorkItem item)
        {
            WorkRun.RequireAccount(item);
            string subId = WorkRun.SubOf(item);
            U8Session session = null;
            WorkContext ctx = null;
            ApiResult result = null;
            bool keep = false;
            try
            {
                session = Open(item, subId);
                if (item.Kind == WorkItem.LoginKind)
                {
                    ApiResult ok = SalesVerify.LoginOk(session, item.Operator);
                    keep = true;
                    return ok;
                }
                ctx = NewContext(item);
                ctx.Session = session;
                // 读路由的功能权限（PermGate）；写路由直接放过。
                PermGate.Enter(ctx);
                // 写预演：DryRunRun.Run 包住处理函数，预演结束的结果优先；不是预演时就是 Dispatch.Handle。
                result = DryRunRun.Run(ctx);
                // 字段权限：读路由成功后统一遮一次（PermMask；写路由、perm/* 不动）。
                PermMask.Apply(ctx, result);
                // 登录放回缓存的前提见 KeepAfter；只在处理函数正常返回后、关闭连接之前判断，异常路径一律不放回。
                keep = KeepAfter(ctx);
                return result;
            }
            catch (Exception ex)
            {
                Exception scrubbed = WorkContext.ScrubEx(ctx, ex);
                if (!object.ReferenceEquals(scrubbed, ex))
                {
                    throw scrubbed;
                }
                throw;
            }
            finally
            {
                try
                {
                    Close(ctx, result);
                }
                finally
                {
                    if (session != null)
                    {
                        LoginCache.Release(session, item, subId, keep);
                    }
                }
            }
        }

        // 读线程：不登录，连接串来自登录缓存。审计与写线程是同一套（WorkRun）。
        // 排队期间条目过期或被删掉时，本任务改走写线程的流程，在本线程上登录一次。
        public static ApiResult Read(WorkItem item)
        {
            // 写预演只在写线程上跑（DryRunRun.Run 包住处理函数）；读线程池收到一律拒绝，审计照写。
            if (item != null && item.DryRun)
            {
                throw new BridgeException(500, "internal", "预演任务进了读线程池");
            }
            WorkRun.RequireAccount(item);
            AuthEntry auth = item.Auth;
            if (!AuthCache.Live(auth) || auth.ConnString == null || auth.ConnString.Length == 0)
            {
                item.Auth = null;
                return Write(item);
            }
            WorkContext ctx = NewContext(item);
            ApiResult result = null;
            try
            {
                ctx.ConnString = auth.ConnString;
                ctx.AuthName = auth.OperatorName;
                PermGate.Enter(ctx);
                result = Dispatch.Handle(ctx);
                PermMask.Apply(ctx, result);
                return result;
            }
            finally
            {
                Close(ctx, result);
            }
        }

        // 只在真实登录之后记连接串和操作员姓名：复用的登录不刷新 AuthCache，口令核对仍跟着真实登录走。
        static U8Session Open(WorkItem item, string subId)
        {
            string key = AuthCache.Enabled ? AuthCache.KeyOf(item, subId) : null;
            U8Session session;
            try
            {
                session = LoginCache.Acquire(item, subId);
            }
            catch (Exception)
            {
                // 任何登录异常都删掉这个键，下次读请求重新登录。
                if (key != null)
                {
                    AuthCache.Forget(key);
                }
                // 权限快照缓存同样清掉该操作员。
                PermCache.Forget(item.Acc, item.Operator);
                throw;
            }
            if (key != null && !LoginCache.LastAcquireReused)
            {
                Remember(key, item, session);
            }
            return session;
        }

        static void Remember(string key, WorkItem item, U8Session session)
        {
            try
            {
                string conn = AdoXml.ConnectionString(session.Login, item.Config);
                AuthCache.Store(key, conn, session.OperatorName);
            }
            catch (Exception)
            {
                // 记不进缓存只是下次读请求还要登录，不影响本次任务。异常文本可能含连接串，不外传。
            }
        }

        // 质量管理、应收、应付的登录首轮一律不复用（多处 by-ref 交给组件），与文档一致。
        static bool NoReuseSub(WorkContext ctx)
        {
            string sub = ctx.Item == null ? "" : WorkRun.SubOf(ctx.Item);
            return sub == "QM" || sub == "AR" || sub == "AP";
        }

        // 自检用：代替对请求连接查 @@TRANCOUNT，返回计数文本（"0" 为干净）。用完设回 null。
        internal static Func<WorkContext, string> TranCountProbe;

        // 登录可放回缓存：复用开着、本请求没调用过 ctx.DropLogin()、请求连接上没有残留事务。
        // @@TRANCOUNT 只是「出了岔子」的启发式检查：只看 ctx.Conn，不看 OpenFresh 或 CO 自己的连接；
        // 从没打开过的连接算干净，不为检查专门开连接。查不了一律当作不干净。
        internal static bool KeepAfter(WorkContext ctx)
        {
            if (!LoginCache.Active || ctx == null || !ctx.KeepLogin || NoReuseSub(ctx))
            {
                return false;
            }
            try
            {
                Func<WorkContext, string> probe = TranCountProbe;
                if (probe != null)
                {
                    return probe(ctx) == "0";
                }
                return !ctx.ConnOpened || CoTrans.Count(ctx.Conn) == "0";
            }
            catch (Exception)
            {
                return false;
            }
        }

        static WorkContext NewContext(WorkItem item)
        {
            WorkContext ctx = new WorkContext();
            ctx.Item = item;
            ctx.Config = item.Config;
            return ctx;
        }

        static void Close(WorkContext ctx, ApiResult result)
        {
            if (ctx == null)
            {
                return;
            }
            ctx.Scrub(result);
            ctx.Dispose();
        }
    }
}
