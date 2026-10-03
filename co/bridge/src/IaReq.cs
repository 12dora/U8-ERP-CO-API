using System;
using System.Collections.Generic;

namespace U8Co
{
    // ia/post、ia/period_end 的请求：fiscal_year、period、action，记账另有 on_uncosted。
    internal sealed class IaAsk
    {
        // true：期末处理（ia/period_end）；false：记账（ia/post）。
        public bool PeriodEnd;
        public int Year;
        public int Period;
        // true：恢复记账 / 取消期末处理。
        public bool Undo;
        // 记账时 U8 算不出成本的存货：refuse 列出后拒绝；skip 跳过这些存货、其余照记。
        public string OnUncosted = IaReq.Refuse;

        public string ActionText()
        {
            if (PeriodEnd)
            {
                return Undo ? "cancel" : "run";
            }
            return Undo ? "unpost" : "post";
        }
    }

    // 存货核算记账 / 恢复记账（ia/post）、期末处理 / 取消期末处理（ia/period_end）在登录前的校验（400）和测试账套名单（403）：
    // 不带 type、id。登录子系统 IA。处理在 IaRun；锁键 "ia:<年度>-<期间>" 和 "period:ia"（与存货月末结账串行），另持全局写闸门。
    internal static class IaReq
    {
        internal const string PostPath = "/u8co/v1/ia/post";
        internal const string PeriodEndPath = "/u8co/v1/ia/period_end";
        internal const string Sub = "IA";
        // 审计 action：ia_post / ia_unpost / ia_period_end / ia_period_end_cancel（入队前的拒绝按路由记 ia_post / ia_period_end）。
        public const string PostAction = "ia_post";
        public const string UnpostAction = "ia_unpost";
        public const string PeriodEndAction = "ia_period_end";
        public const string PeriodEndCancelAction = "ia_period_end_cancel";
        public const string Refuse = "refuse";
        public const string Skip = "skip";
        public const string TestOnly = "存货核算记账和期末处理只对配置为测试账套的账套开放";
        const string UncostedHint = "on_uncosted 只能是 refuse（缺省）或 skip，且只能和 action=post 一起用";

        public static bool IsPath(string path)
        {
            return path == PostPath || path == PeriodEndPath;
        }

        public static IaAsk Parse(string path, Dictionary<string, object> body)
        {
            IaAsk ask = new IaAsk();
            ask.PeriodEnd = path == PeriodEndPath;
            ask.Year = Int(body, "fiscal_year", 1000, 9999, "fiscal_year 必须是 4 位年度（整数）");
            ask.Period = Int(body, "period", 1, 12, "period 必须是 1 到 12 的整数");
            string action = Requests.Field(body, "action") as string;
            string done = ask.PeriodEnd ? "run" : "post";
            string undo = ask.PeriodEnd ? "cancel" : "unpost";
            if (action != done && action != undo)
            {
                throw BridgeException.BadField("action", "action 只能是 " + done + " 或 " + undo);
            }
            ask.Undo = action == undo;
            if (!ask.PeriodEnd)
            {
                ask.OnUncosted = Uncosted(Requests.Field(body, "on_uncosted"), ask.Undo);
            }
            return ask;
        }

        static string Uncosted(object raw, bool undo)
        {
            if (raw == null)
            {
                return Refuse;
            }
            string text = raw as string;
            if (text != Refuse && text != Skip)
            {
                throw BridgeException.BadField("on_uncosted", "on_uncosted 只能是 refuse 或 skip").WithHint(UncostedHint);
            }
            if (undo)
            {
                throw BridgeException.BadField("on_uncosted", "恢复记账不用 on_uncosted").WithHint(UncostedHint);
            }
            return text;
        }

        static int Int(Dictionary<string, object> body, string key, int min, int max, string message)
        {
            object raw = Requests.Field(body, key);
            if (!(raw is int) || (int)raw < min || (int)raw > max)
            {
                throw BridgeException.BadField(key, message);
            }
            return (int)raw;
        }

        // 入队前（解析之前）按路由给的审计 action。
        public static string DefaultAction(string path)
        {
            return path == PeriodEndPath ? PeriodEndAction : PostAction;
        }

        public static string AuditAction(IaAsk ask)
        {
            if (ask.PeriodEnd)
            {
                return ask.Undo ? PeriodEndCancelAction : PeriodEndAction;
            }
            return ask.Undo ? UnpostAction : PostAction;
        }

        // 权限规则键（PermRegistryIa）：write:ia:post / write:ia:unpost / write:ia:period_end。
        // 取消期末处理在 U8 的期末处理窗体里，按期末处理的功能 id。
        public static string RuleKey(IaAsk ask)
        {
            if (ask.PeriodEnd)
            {
                return "write:ia:period_end";
            }
            return ask.Undo ? "write:ia:unpost" : "write:ia:post";
        }

        // 中文说明（审计备注、日志）。
        public static string Title(IaAsk ask)
        {
            if (ask.PeriodEnd)
            {
                return ask.Undo ? "取消期末处理" : "期末处理";
            }
            return ask.Undo ? "恢复记账" : "正常单据记账";
        }

        public static string MonthKey(IaAsk ask)
        {
            return PeriodGate.N(ask.Year) + "-" + (ask.Period < 10 ? "0" : "") + PeriodGate.N(ask.Period);
        }

        // 同一月份的记账、期末处理串行，并与存货月末结账（"period:ia"）串行。请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(string path, Dictionary<string, object> body)
        {
            IaAsk ask;
            try
            {
                ask = Parse(path, body);
            }
            catch (BridgeException)
            {
                return new string[0];
            }
            return new string[] { "ia:" + MonthKey(ask), "period:ia" };
        }

        // 存货核算脚本任务的 HTTP 等待（毫秒）：脚本超时 iaCommandSeconds 再加 60 秒给检查、提交和回读。
        public static int LongWait(BridgeConfig cfg)
        {
            int seconds = cfg == null ? 900 : cfg.IaCommandSeconds;
            return seconds * 1000 + 60000;
        }

        // 脚本超时后回滚要留的时间（秒），以及值得开始一个脚本的最少时间（秒）。
        public const int RollbackReserveSeconds = 120;
        public const int MinScriptSeconds = 30;

        static int Budget(BridgeConfig cfg)
        {
            return cfg == null ? 900 : cfg.IaCommandSeconds;
        }

        // 长任务（WaitMs 超过缺省）开始执行时起算请求级的脚本时间预算：截止 = 现在 + iaCommandSeconds，
        // 且不晚于 HTTP 等待截止前 60 秒（留给提交、回读）。其余任务不设。
        public static void Arm(WorkItem item)
        {
            if (item == null || item.WaitMs <= WorkItem.HttpWaitMs)
            {
                return;
            }
            long deadline = DateTime.UtcNow.Ticks + Budget(item.Config) * TimeSpan.TicksPerSecond;
            if (item.DeadlineUtcTicks > 0)
            {
                deadline = Math.Min(deadline, item.DeadlineUtcTicks - 60L * TimeSpan.TicksPerSecond);
            }
            item.IaDeadlineTicks = deadline;
        }

        // 请求级预算还剩的秒数（不小于 0）。没起算（短任务、自检）时按整个 iaCommandSeconds。through 每个存货核算步骤前可用它判断。
        public static int SecondsLeft(WorkItem item)
        {
            if (item == null || item.IaDeadlineTicks == 0)
            {
                return Budget(item == null ? null : item.Config);
            }
            long left = (item.IaDeadlineTicks - DateTime.UtcNow.Ticks) / TimeSpan.TicksPerSecond;
            return (int)Math.Max(0L, Math.Min(left, int.MaxValue));
        }

        // 下一个脚本的 CommandTimeout（秒）：min(iaCommandSeconds, 剩余 − 回滚预留)。不到 MinScriptSeconds 返回 0（不要开始）。
        public static int ScriptSeconds(WorkItem item)
        {
            int seconds = Math.Min(Budget(item == null ? null : item.Config), SecondsLeft(item) - RollbackReserveSeconds);
            return seconds < MinScriptSeconds ? 0 : seconds;
        }

        // 脚本做完以后（提交、回读或回滚）看门狗的放宽量：已执行的脚本总时长 + 回滚预留（Watchdog 另保底 3 分钟）。
        public static long AfterScriptTicks(WorkItem item)
        {
            long work = item == null ? 0 : item.IaWorkTicks;
            return work + RollbackReserveSeconds * TimeSpan.TicksPerSecond;
        }

        // 脚本执行期间看门狗的放宽量：执行 + 同样长的回滚 + 回滚预留。
        public static long WatchTicks(int scriptSeconds)
        {
            return (2L * scriptSeconds + RollbackReserveSeconds) * TimeSpan.TicksPerSecond;
        }

        // 用户决定（同月末结账）：只对 config.json 的 testAccounts 里的账套开放（含预演），其余登录前 403。
        public static void RequireTestAccount(WorkItem item)
        {
            TestAccountGate.Require(item, TestOnly);
        }

        // 登录前的字段名单（RequestsP4 的 P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] PostSpec = new string[] { PostPath, "fiscal_year", "period", "action", "on_uncosted" };
        internal static readonly string[] PeriodEndSpec = new string[] { PeriodEndPath, "fiscal_year", "period", "action" };
    }
}
