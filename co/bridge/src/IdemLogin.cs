using System;
using System.Threading;

namespace U8Co
{
    // 重放前的登录校验：重放不能绕过 U8 登录。口令不进内容摘要，所以必须单独确认这一次的口令有效。
    // 登录缓存里有同一账套、年度、操作员、口令、子系统和日期的条目（写线程刚登录成功过）就算通过；
    // 否则在写线程池上排一个只登录的任务（与 login-check 相同，不调用任何业务组件），登录失败照常返回 422。
    internal static class IdemLogin
    {
        const string LoginPath = "/u8co/v1/login-check";

        public static void Verify(WorkItem item, long deadlineTicks)
        {
            string sub = WorkRun.SubOf(item);
            if (AuthCache.Enabled && AuthCache.Find(AuthCache.KeyOf(item, sub)) != null)
            {
                return;
            }
            WorkItem probe = Probe(item, sub);
            probe.Done = new ManualResetEvent(false);
            try
            {
                int got = StaWorker.TryEnqueue(probe);
                if (got != StaWorker.Enqueued)
                {
                    throw HttpServer.QueueError(got);
                }
                if (!probe.Done.WaitOne(IdemFlow.RemainingMs(deadlineTicks)))
                {
                    // 只登录的任务不写 U8，超时一律可以重试。
                    HttpServer.WaitError(probe, new AuditDraft());
                    throw new BridgeException(503, "busy_timeout", "登录校验超时，可以重试");
                }
                Check(probe.Result);
            }
            finally
            {
                try
                {
                    probe.Done.Close();
                }
                catch (Exception)
                {
                }
            }
        }

        static WorkItem Probe(WorkItem item, string sub)
        {
            WorkItem probe = new WorkItem();
            probe.Config = item.Config;
            probe.Kind = WorkItem.LoginKind;
            probe.Acc = item.Acc;
            probe.Year = item.Year;
            probe.Operator = item.Operator;
            probe.Password = item.Password;
            probe.Date = item.Date;
            probe.ClientIp = item.ClientIp;
            probe.Path = LoginPath;
            probe.Route = item.Path;
            probe.Action = "idempotent_login";
            probe.Started = DateTime.UtcNow;
            probe.SubId = sub;
            return probe;
        }

        static void Check(ApiResult result)
        {
            if (result == null)
            {
                throw new BridgeException(500, "internal", "登录校验没有结果");
            }
            if (result.Status == 200)
            {
                return;
            }
            object message = null;
            if (result.Body != null)
            {
                result.Body.TryGetValue("message", out message);
            }
            throw new BridgeException(result.Status, result.Code ?? "internal", message as string ?? result.AuditMessage ?? "");
        }
    }
}
