using System;
using System.Collections.Generic;
using System.Threading;

namespace U8Co
{
    // 审批流操作之后清理 U8 终审插件在本进程里另开、从不注销的 QM 登录留下的孤儿任务
    // （UFSYSTEM..UA_TaskLog / ua_Task_Common）。只在 config.json 的 cleanOrphanTasks 为 true 时运行。
    // 只删：本机 cStation、QM 子系统、调用前快照里没有、登录时间在 [调用前, U8 调用返回后] 两次 SQL Server 时间之间的行；
    // ua_Task_Common 有该任务号的行时，操作员和账套还必须是本次请求的（在删除事务里锁住后核对，去掉的审计 foreign_owner）。
    // 开关打开时审批流操作经 Gate 串行：Begin 取锁、Finish 放锁，窗口里不会有本桥别的审批流调用。
    // 取不到锁就不调 U8（503 busy_timeout，可重试）；取到锁后快照失败只是不清理，U8 调用仍在锁内。
    // 任何失败只写审计事件，不改变请求结果。见 docs/u8-notes.md「审批流终审留下的孤儿任务」。
    internal sealed class OrphanScan
    {
        public string Station;
        public string Since;
        public string Until;
        public string Operator;
        public string Acc;
        public HashSet<string> Before;
        public string Action;
        public bool Held;
        // 非 null：本次不清理（快照失败、过大或没有操作员），锁照常持有到 Finish。
        public string NoClean;
    }

    internal static class TaskOrphans
    {
        const int MaxIds = 10;
        // 等别的审批流操作放锁最多这么久，且不超过请求期限（入队 + 75 秒）减去 ReserveMs；
        // 等不到就在调 U8 之前返回 503 busy_timeout（什么都没写，可重试）。
        const int WaitMs = 60000;
        const int ReserveMs = 30000;
        // 分段等，每段之间看一次看门狗：进程已判不健康（将 Halt、Settle 后退出）就不再等。
        const int SliceMs = 1000;
        static readonly SemaphoreSlim Gate = new SemaphoreSlim(1, 1);
        static volatile bool _enabled;

        // 启动时按 config.json 的 cleanOrphanTasks 设置，只给 meta 的 features 用；是否清理以 ctx.Config 为准。
        public static void Configure(bool on)
        {
            _enabled = on;
        }

        public static bool Enabled
        {
            get { return _enabled; }
        }

        // 调 U8 之前：取锁，取 SQL Server 时间作窗口起点，快照本机现有任务号。
        // 开关关着返回 null。取不到锁抛 503 busy_timeout（调用方还没调 U8）。
        // 返回非 null 时一定持锁，调用方必须调 Finish 放锁；快照失败时 NoClean 非 null，只是本次不清理。
        public static OrphanScan Begin(WorkContext ctx, string action)
        {
            if (ctx == null || ctx.Config == null || !ctx.Config.CleanOrphanTasks) return null;
            Acquire(ctx.Item);
            OrphanScan scan = new OrphanScan();
            scan.Action = action;
            scan.Held = true;
            try
            {
                Snap(ctx, scan);
            }
            catch (Exception ex)
            {
                scan.NoClean = "failed";
                Failed(action, ex);
            }
            return scan;
        }

        static void Acquire(WorkItem item)
        {
            if (Gate.Wait(0)) return;
            long until = DateTime.UtcNow.Ticks + (long)Budget(item) * TimeSpan.TicksPerMillisecond;
            while (!Watchdog.Unhealthy())
            {
                int left = IdemFlow.RemainingMs(until);
                if (left <= 0) break;
                if (Gate.Wait(Math.Min(left, SliceMs))) return;
            }
            throw new BridgeException(503, "busy_timeout", "审批流操作排队中，请稍后重试");
        }

        // 最多 WaitMs；有请求期限时再留出 ReserveMs 给 U8 调用和回读，免得 HTTP 先答 504 而审批仍在进行。
        static int Budget(WorkItem item)
        {
            if (item == null || item.DeadlineUtcTicks == 0) return WaitMs;
            return Math.Min(WaitMs, IdemFlow.RemainingMs(item.DeadlineUtcTicks) - ReserveMs);
        }

        static void Snap(WorkContext ctx, OrphanScan scan)
        {
            scan.Station = Environment.MachineName;
            scan.Operator = ctx.Item == null ? null : ctx.Item.Operator;
            scan.Acc = ctx.Item == null ? null : ctx.Item.Acc;
            if (Blank(scan.Operator) || Blank(scan.Acc))
            {
                NoClean(scan, "no_operator");
                return;
            }
            scan.Since = TaskOrphansSql.Now(ctx.Conn);
            scan.Before = TaskOrphansSql.TaskIds(ctx.Conn, scan.Station);
            if (scan.Before == null) NoClean(scan, "snapshot_too_large");
        }

        static void NoClean(OrphanScan scan, string reason)
        {
            scan.NoClean = reason;
            Skipped(scan.Action, reason, -1);
        }

        // U8 调用一返回（含抛错）就取窗口终点。取不到时本次不清理（Until 留空），锁仍由 Finish 放。
        public static void Mark(WorkContext ctx, OrphanScan scan)
        {
            if (scan == null || scan.NoClean != null) return;
            try
            {
                scan.Until = TaskOrphansSql.Now(ctx.Conn);
            }
            catch (Exception ex)
            {
                scan.Until = null;
                Failed(scan.Action, ex);
            }
        }

        // 回读核对之后（成功或 U8 拒绝）。cause 是本桥调 U8 之前自己抛的错时不清理。任何路径都放锁。
        public static int Finish(WorkContext ctx, OrphanScan scan, Exception cause)
        {
            if (scan == null) return 0;
            try
            {
                if (BeforeU8(cause) || scan.NoClean != null || scan.Until == null) return 0;
                return Clean(ctx, scan);
            }
            catch (Exception ex)
            {
                Failed(scan.Action, ex);
                return 0;
            }
            finally
            {
                Release(scan);
            }
        }

        static void Release(OrphanScan scan)
        {
            if (!scan.Held) return;
            scan.Held = false;
            Gate.Release();
        }

        // 清掉了才在成功结果里带 orphan_tasks_cleaned。
        public static ApiResult Note(ApiResult result, int cleaned)
        {
            if (result != null && result.Body != null && cleaned > 0)
            {
                result.Body["orphan_tasks_cleaned"] = cleaned;
            }
            return result;
        }

        static int Clean(WorkContext ctx, OrphanScan scan)
        {
            List<string> ids = TaskOrphansSql.Candidates(ctx.Conn, scan);
            if (ids == null || ids.Count > MaxIds)
            {
                Skipped(scan.Action, "too_many", ids == null ? -1 : ids.Count);
                return 0;
            }
            if (ids.Count == 0) return 0;
            OrphanDrop drop = TaskOrphansSql.Delete(ctx, scan, ids);
            if (drop.Foreign > 0) Skipped(scan.Action, "foreign_owner", drop.Foreign);
            if (drop.Skip != null)
            {
                Skipped(scan.Action, drop.Skip, ids.Count);
                return 0;
            }
            if (drop.Deleted > 0) Cleaned(scan.Action, drop.Deleted);
            return drop.Deleted;
        }

        // 请求本身不合法、单据不存在、U8 组件未注册、预演停下（DryRunDone）：都在调 U8 之前抛出。
        static bool BeforeU8(Exception cause)
        {
            if (cause is DryRunDone) return true;
            BridgeException bridge = cause as BridgeException;
            if (bridge == null) return false;
            return bridge.Status == 400 || bridge.Status == 404 || bridge.Status == 503;
        }

        static bool Blank(string text)
        {
            return text == null || text.Trim().Length == 0;
        }

        static void Skipped(string action, string reason, int count)
        {
            Dictionary<string, object> fields = Fields(action);
            fields["reason"] = reason;
            if (count >= 0) fields["count"] = count;
            AuditEvent.Write("orphan_tasks_skipped", fields);
        }

        static void Cleaned(string action, int count)
        {
            Dictionary<string, object> fields = Fields(action);
            fields["count"] = count;
            fields["sub"] = TaskOrphansSql.SubText();
            AuditEvent.Write("orphan_tasks_cleaned", fields);
        }

        static void Failed(string action, Exception ex)
        {
            Dictionary<string, object> fields = Fields(action);
            string text = ex == null || ex.Message == null ? "" : ex.Message.Trim();
            if (text.Length > 500) text = text.Substring(0, 500);
            fields["error"] = text;
            AuditEvent.Write("orphan_tasks_failed", fields);
        }

        static Dictionary<string, object> Fields(string action)
        {
            Dictionary<string, object> fields = new Dictionary<string, object>();
            fields["action"] = action ?? "";
            return fields;
        }
    }
}
