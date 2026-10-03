using System;
using System.Threading;

namespace U8Co
{
    internal delegate ApiResult WorkExec(WorkItem item);

    // 一个任务从出队到发信号的公共流程。写线程和读线程共用，审计行只在这里写。
    internal static class WorkRun
    {
        public static void Run(WorkItem item, WorkExec exec, int slot)
        {
            if (Skip(item))
            {
                return;
            }
            // 看门狗按缺省 3 分钟进，存货核算脚本执行时才放宽（IaRun.RunScripts）。长任务从这里起算请求级的脚本时间预算。
            IaReq.Arm(item);
            Watchdog.Enter(slot);
            try
            {
                Finish(item, exec);
            }
            finally
            {
                // 写入限额（WriteQuota）：本任务占的名额在这里结算，2xx 计入已提交，其余退回。不抛异常。
                WriteQuota.Settle(item);
                // 预演状态绑在线程上，线程会被下一个任务复用：每个任务结束都清掉。
                DryRun.End();
                Watchdog.Leave(slot);
                Interlocked.Exchange(ref item.Phase, WorkItem.Finished);
                item.Password = null;
                item.Auth = null;
                Signal(item);
            }
        }

        static void Finish(WorkItem item, WorkExec exec)
        {
            try
            {
                item.Result = exec(item);
            }
            catch (BridgeException ex)
            {
                item.Result = ApiResult.From(ex);
            }
            catch (Exception ex)
            {
                item.Result = ApiResult.From(new BridgeException(500, "internal", ex.Message));
            }
            try
            {
                Audit(item, null);
            }
            catch (Exception)
            {
            }
        }

        static bool Skip(WorkItem item)
        {
            if (DateTime.UtcNow.Ticks > item.DeadlineUtcTicks)
            {
                Interlocked.CompareExchange(ref item.Phase, WorkItem.Abandoned, WorkItem.Queued);
            }
            int got = Interlocked.CompareExchange(ref item.Phase, WorkItem.Running, WorkItem.Queued);
            if (got == WorkItem.Queued)
            {
                return false;
            }
            Expire(item);
            return true;
        }

        // 入队之后再查一次。以后若多一条入队路径，也不会对名单外的账套调用 Login 或查库。
        // 写入策略（WriteClassGate.Post）同样在登录前再判一次：排队期间生效的冻结、时段照样拦下。
        // 写任务的许可状态暂停（LicenseHold.RequireWrite）对新登录和复用的登录都生效。
        public static void RequireAccount(WorkItem item)
        {
            if (!BridgeConfig.AllowsAccount(item.Config, item.Acc))
            {
                throw new BridgeException(403, "account_not_allowed", "账套不在允许列表内");
            }
            WriteClassGate.Post(item);
            // 许可保护的 holdWritesWhen（策略第 8 步）：复用缓存里的登录不经 LicenseHold.Enter，写任务在这里按当前许可状态先判一次。
            // 放在写入限额之前，被暂停的写入不占限额名额。
            LicenseHold.RequireWrite(item);
            // 写入限额（策略第 7 步）必须在写入策略检查（第 1–6 步）之后：放在本函数最后。
            WriteQuota.Require(item);
        }

        public static string SubOf(WorkItem item)
        {
            if (item.SubId != null && item.SubId.Length > 0)
            {
                return item.SubId;
            }
            if (item.Type == null || item.Type.SubId == null || item.Type.SubId.Length == 0)
            {
                return "SA";
            }
            return item.Type.SubId;
        }

        public static U8Open LoginAsk(WorkItem item)
        {
            U8Open ask = new U8Open();
            ask.SubId = SubOf(item);
            ask.Acc = item.Acc;
            ask.Year = item.Year;
            ask.User = item.Operator;
            ask.Password = item.Password;
            ask.Date = item.Date;
            ask.Write = WriteGate.IsWrite(item);
            return ask;
        }

        static void Audit(WorkItem item, string forceOutcome)
        {
            AuditDraft draft = DraftOf(item);
            string outcome = forceOutcome;
            if (outcome == null)
            {
                outcome = item.Result == null ? "internal" : item.Result.Code;
            }
            string message = item.Result == null ? "" : item.Result.AuditMessage;
            AuditLog.Write(item.Config, draft, outcome, message, item.Password);
        }

        static AuditDraft DraftOf(WorkItem item)
        {
            AuditDraft draft = new AuditDraft();
            draft.Start = item.Started;
            draft.Ip = item.ClientIp;
            draft.Path = item.Path;
            draft.Acc = item.Acc;
            draft.Year = item.Year;
            draft.Operator = item.Operator;
            draft.HasId = item.HasId;
            draft.Id = item.Id;
            draft.Action = item.Action;
            draft.TranBefore = Text(item.TranBefore);
            draft.TranAfter = Text(item.TranAfter);
            draft.TypeName = item.Type == null ? "" : Text(item.Type.Name);
            draft.Route = Text(item.Route ?? item.Path);
            draft.Detail = Text(item.Detail);
            draft.DryRun = item.DryRun ? Text(item.DryRunMode) : "";
            draft.Caller = Text(item.Caller);
            draft.Op = item.WriteInfo == null ? "" : Text(item.WriteInfo.Op);
            draft.Policy = Text(item.Policy);
            return draft;
        }

        static string Text(string value)
        {
            return value ?? "";
        }

        static void Expire(WorkItem item)
        {
            item.Result = ApiResult.From(new BridgeException(503, "busy_timeout", "排队超时，可以重试"));
            try
            {
                Audit(item, "expired");
            }
            catch (Exception)
            {
            }
            item.Password = null;
            item.Auth = null;
            Signal(item);
        }

        public static void Cancel(WorkItem item)
        {
            Interlocked.Exchange(ref item.Phase, WorkItem.Abandoned);
            item.Result = ApiResult.From(new BridgeException(503, "stopping", "服务正在停止"));
            try
            {
                Audit(item, null);
            }
            catch (Exception)
            {
            }
            item.Password = null;
            item.Auth = null;
            Signal(item);
        }

        static void Signal(WorkItem item)
        {
            try
            {
                item.Done.Set();
            }
            catch (Exception)
            {
            }
        }
    }
}
