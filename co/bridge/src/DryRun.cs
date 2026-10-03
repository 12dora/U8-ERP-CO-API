using System;
using System.Collections.Generic;

namespace U8Co
{
    // 写预演（dry_run）结束时抛出，带预演响应体。任务执行体（DryRun.Run）把它变成 200，审计 outcome 记 dry_run。
    // 处理函数若接住它再包成别的异常抛出，结果同样按预演返回（DryRun.Finished 优先）；吞掉它继续走是缺陷。
    internal sealed class DryRunDone : Exception
    {
        public Dictionary<string, object> Body { get; private set; }
        public string Mode { get; private set; }

        public DryRunDone(string mode, Dictionary<string, object> body)
            : base("预演结束")
        {
            Mode = mode ?? "";
            Body = body ?? new Dictionary<string, object>();
        }
    }

    // 一次预演任务的状态，只在执行它的写线程上（[ThreadStatic]）。
    internal sealed class DryRunState
    {
        public WorkContext Ctx;
        public string Mode;
        public readonly List<VoucherKind> RefKinds = new List<VoucherKind>();
        public readonly List<int> RefIds = new List<int>();
        public readonly Dictionary<string, object> Detail = new Dictionary<string, object>();
        // 预演结束后的结果：DryRunDone（成功）或 BridgeException（U8 自行提交、回读失败）。非空之后不能再开事务或提交。
        public Exception Final;
        // 本任务里调过 CoTrans.Begin。没开过事务、也没走到提交点或 Stop 的预演按「没有需要写入的改动」返回。
        public bool Begun;

        public void Add(VoucherKind kind, int id)
        {
            for (int i = 0; i < RefIds.Count; i++)
            {
                if (RefIds[i] == id && object.ReferenceEquals(RefKinds[i], kind))
                {
                    return;
                }
            }
            RefKinds.Add(kind);
            RefIds.Add(id);
        }
    }

    // 写预演的公共入口。处理函数只用 Active / Created / Touched / Set / Stop；提交钩子在 CoTrans，执行体在 DryRunRun.cs。
    // 不是预演任务时这些调用都什么也不做，也不查库。
    internal static partial class DryRun
    {
        [ThreadStatic]
        static DryRunState _cur;

        // 本线程当前任务是预演（从 Begin 到 End，含已结束之后）。
        public static bool Active
        {
            get { return _cur != null; }
        }

        // 预演已结束（提交钩子已回滚、或 Stop 已抛出、或已判定 U8 自行提交）。
        public static bool Finished
        {
            get
            {
                DryRunState s = _cur;
                return s != null && s.Final != null;
            }
        }

        // 新单据主键已知时（事务内）登记。
        public static void Created(VoucherKind kind, int id)
        {
            DryRunState s = _cur;
            if (s == null || kind == null || id <= 0)
            {
                return;
            }
            s.Add(kind, id);
        }

        // 请求本身的 type/id 之外被改动的已有单据（生单来源、回写累计的上游单据等）。
        public static void Touched(VoucherKind kind, int id)
        {
            Created(kind, id);
        }

        // 处理函数给响应 detail 的补充（总账后像、核销号、计划凭证、校验后的输入等）。
        public static void Set(string key, object value)
        {
            DryRunState s = _cur;
            if (s == null || key == null || key.Length == 0)
            {
                return;
            }
            s.Detail[key] = value;
        }

        // 自己提交的组件调用之前调用：预演时停在这里（validate 模式），抛 DryRunDone；不是预演时什么也不做。
        public static void Stop(WorkContext ctx, string what)
        {
            DryRunState s = _cur;
            if (s == null)
            {
                return;
            }
            if (s.Final != null)
            {
                throw s.Final;
            }
            s.Detail["stopped_before"] = what ?? "";
            DryRunDone done = new DryRunDone(DryRunModes.Validate, DryRunRun.ValidateBody(s, ctx ?? s.Ctx));
            s.Final = done;
            throw done;
        }

        // 执行体在处理函数之前调用；Mode 来自 WorkItem.DryRunMode（Requests 登录前已按 DryRunModes 核对）。
        internal static void Begin(WorkContext ctx)
        {
            DryRunState s = new DryRunState();
            s.Ctx = ctx;
            s.Mode = ctx == null || ctx.Item == null ? "" : ctx.Item.DryRunMode ?? "";
            _cur = s;
        }

        // 每个任务结束都清掉（线程会被下一个任务复用）。可以重复调用。
        internal static void End()
        {
            _cur = null;
        }

        internal static DryRunState Current
        {
            get { return _cur; }
        }

        // CoTrans.Begin 之前：预演结束之后再开事务一律再抛同一个结果；否则记下本任务开过事务。
        internal static void BeforeBegin()
        {
            DryRunState s = _cur;
            if (s == null)
            {
                return;
            }
            if (s.Final != null)
            {
                throw s.Final;
            }
            s.Begun = true;
        }

        // 提交钩子（CoTrans.Commit / CommitSeen，预演时）：返回要抛出的异常，调用方 throw，绝不提交。
        internal static Exception AtCommit(object conn)
        {
            DryRunState s = _cur;
            if (s.Final != null)
            {
                return s.Final;
            }
            s.Final = DryRunRun.Close(s, conn);
            return s.Final;
        }
    }
}
