using System;
using System.Collections.Generic;
using System.Threading;

namespace U8Co
{
    internal sealed class WorkItem
    {
        public const string LoginKind = "login";
        public const string SaleKind = "sale";
        public const string DispatchKind = "dispatch";
        public const int HttpWaitMs = 75000;
        // 写路由（WriteGate.IsWrite）开始执行时至少要剩这么多时间（按 DeadlineUtcTicks 算）。不够就不开始，
        // 按排队超时返回 503 busy_timeout（没有执行，可以重试），免得 HTTP 等待先到、返回 504 outcome_unknown 而 U8 照样写进去。
        // 也就是写入最多排队 HttpWaitMs − MinWriteStartMs = 45 秒。
        public const int MinWriteStartMs = 30000;
        public const int Queued = 0;
        public const int Running = 1;
        public const int Abandoned = 2;
        public const int Finished = 3;

        public BridgeConfig Config;
        public string Kind;
        public string Acc;
        public string Year;
        public string Operator;
        public string Password;
        public string Date;
        public bool HasId;
        public int Id;
        public string Action;
        public string ClientIp;
        public string Path;
        public DateTime Started;
        public ManualResetEvent Done;
        public ApiResult Result;
        public int Phase;
        // 排队截止时刻，也是 HTTP 线程等结果的截止时刻。入队前已设（更早）的保留，否则入队时设为 HttpWaitMs 之后。
        public long DeadlineUtcTicks;
        public string TranBefore;
        public string TranAfter;
        public string Route;
        public VoucherKind Type;
        public string Opinion;
        public Dictionary<string, object> Head;
        public object[] Lines;
        public string Detail;
        // null 表示整单关闭或打开。生单成功前 Id 是来源单据，Source 是来源类型。
        public int[] LineIds;
        public VoucherKind Source;
        // 入队时算好的单据锁键。取到任务的线程持有它们，跑完在 finally 里放掉。
        public string[] LockKeys;
        public bool KeysHeld;
        // 只给读线程：登录缓存里的连接串和姓名。跑完清掉，不进审计和响应。
        public AuthEntry Auth;
        // 解析后的请求体（不含 password_enc）。总账、档案、列表的领域类从这里读路由字段。
        public Dictionary<string, object> Body;
        // 路由指定的登录子系统；空表示按单据类型（WorkRun.SubOf）。
        public string SubId;
        // 幂等键（只在新建类路由上、调用方带了 idempotency_key 时非空），见 IdemReq / IdemFlow。
        public IdemAsk Idem;
        // 写预演（DryRunReq）：请求体 dry_run=true；DryRunMode 是登录前按 DryRunModes 查到的 rollback / validate。
        public bool DryRun;
        public string DryRunMode;
        // 请求体的 caller（调用方，进审计；不带为空）。
        public string Caller;
        // 写入策略：登录前的分类结果（WriteClass；读路由为 null）和判定（allow 或拒绝的错误码；未配置策略为空）。
        public WriteRequestInfo WriteInfo;
        public string Policy;
        // 本任务的 HTTP 等待（毫秒），也是排队截止的起算长度。缺省 HttpWaitMs；存货核算的长任务（ia/post、ia/period_end、
        // 含存货核算的 periods/close）登录前改成 iaCommandSeconds + 60 秒（IaReq.LongWait）。看门狗只在脚本执行期间放宽（IaRun.RunScripts）。
        public int WaitMs = HttpWaitMs;
        // 存货核算脚本的请求级截止时刻（UTC ticks）：长任务开始执行时设为当时 + iaCommandSeconds（IaReq.Arm），0 表示没设。
        public long IaDeadlineTicks;
        // 本请求已执行的存货核算脚本总时长（ticks）。回滚前看门狗按它再放宽（IaRun.BeforeRollback）。
        public long IaWorkTicks;
        // 本请求在事务里开始执行过存货核算脚本（IaRun.RunScripts）。只有这时才按事务层数判断结果未知（IaRun.AfterRollback）。
        public bool IaStarted;

        // 写路由剩下的时间已不够开始执行（见 MinWriteStartMs）。读路由总是 false。
        public static bool TooLateToWrite(WorkItem item, long nowTicks)
        {
            return WriteGate.IsWrite(item) && item.DeadlineUtcTicks - nowTicks < MinStartMs(item) * TimeSpan.TicksPerMillisecond;
        }

        // 开始执行至少要剩的时间：MinWriteStartMs 加上长任务多出的等待，排队容忍度对所有路由都是 45 秒。
        internal static long MinStartMs(WorkItem item)
        {
            return MinWriteStartMs + Math.Max(0L, (long)item.WaitMs - HttpWaitMs);
        }
    }
}
