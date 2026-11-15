using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // WedgedState.Strike 的结论。
    internal static class WedgedAction
    {
        // 功能关闭（wedgedLoginRecycle 为 0）。
        public const int None = 0;
        // 记了一次，还没到阈值。
        public const int Counted = 1;
        // 第一步：换掉该操作员固定的写线程。
        public const int Recycle = 2;
        // 第二步：请求重启进程。
        public const int Restart = 3;
        // 第二步被限频（距上一次桥自己结束进程不到 RestartGapTicks），照常回 422。
        public const int RestartLimited = 4;
        // 第二步会用掉服务失败动作里留给真卡死的那一次重启（FailChain），照常回 422。
        public const int RestartReserve = 6;
        // 第二步没开（wedgedLoginRestart 实际为假），照常回 422。
        public const int RestartOff = 5;

        public static string Label(int action)
        {
            switch (action)
            {
                case Counted: return "counted";
                case Recycle: return "recycle";
                case Restart: return "restart";
                case RestartLimited: return "restart_rate_limited";
                case RestartOff: return "restart_off";
                case RestartReserve: return "restart_scm_reserve";
                default: return "none";
            }
        }
    }

    // 服务的失败动作（WedgedRestart.FailureMode）：开头连续几条是「重启」（K），以及 SCM 重置失败计数的周期。
    // ResetTicks 不大于 0 表示不重置（dwResetPeriod 为 INFINITE；为 0 时同样按不重置算，保守）。
    internal sealed class ScmPolicy
    {
        public readonly int Restarts;
        public readonly long ResetTicks;
        // 失败动作全部为重启（至少一条）：SCM 重复最后一条，失败多少次都会拉起，FailChain 不按次数封顶。
        public readonly bool Unlimited;

        public ScmPolicy(int restarts, long resetTicks) : this(restarts, resetTicks, false)
        {
        }

        public ScmPolicy(int restarts, long resetTicks, bool unlimited)
        {
            Restarts = restarts;
            ResetTicks = resetTicks;
            Unlimited = unlimited && restarts >= 1;
        }
    }

    // SCM 的失败计数：距上一次失败超过重置周期才清零，第 N 次失败用第 N 条动作（超出条数用最后一条）。
    // 桥自己结束进程（自愈重启、看门狗卡死、终止进程的未处理异常）的时刻记在文件里（WedgedRestart），按它估计 SCM 此刻的计数。
    internal static class FailChain
    {
        // 以 now 结尾、相邻间隔都小于重置周期的那一串已记失败的个数（不含 now 本身）。failures 升序。
        public static int Length(IList<long> failures, long now, long resetTicks)
        {
            int n = 0;
            long last = now;
            for (int i = failures.Count - 1; i >= 0; i--)
            {
                long t = failures[i];
                if (t > now)
                {
                    // 将来的时刻（时钟回拨）不算。
                    continue;
                }
                if (resetTicks > 0 && last - t >= resetTicks)
                {
                    break;
                }
                n++;
                last = t;
            }
            return n;
        }

        // 这次结束进程之后 SCM 仍会拉起，并且还留一次重启给真正的卡死：链长 + 1 ≤ K − 1。
        // 失败动作全部为重启时 SCM 每次都拉起，不按次数封顶，只受 WedgedState.RestartGapTicks 限频。
        public static bool Allows(IList<long> failures, long now, ScmPolicy policy)
        {
            if (policy == null)
            {
                return false;
            }
            return policy.Unlimited || Length(failures, now, policy.ResetTicks) + 1 <= policy.Restarts - 1;
        }
    }

    // 「登录状态不是 0」自愈的判定（WedgedLogin 的纯状态部分）：不碰线程、COM、文件，时刻由调用方给，自检直接用。
    // 每个操作员（OperatorPin.Norm）一条记录：连续判为卡住的次数、上一次的时刻、上一次换线程的时刻。
    // - 相邻两次相隔超过 StrikeWindowTicks 从 1 重新数；数到阈值换线程（Recycle），计数清零。
    // - 换线程后 RecycleWindowTicks 之内再判为卡住就升级（Restart / RestartLimited / RestartReserve / RestartOff），不再换线程。
    //   重启开关和服务失败动作（policy）只在升级时才问。
    // - 距桥上一次结束进程（含进程重启前留在文件里的，SeedFailures）不到 RestartGapTicks 时限频；
    //   按 FailChain 会用掉留给真卡死的那次重启时不重启。
    // - 登录成功（Ok）删掉该操作员的记录：计数和换线程时刻一起清掉。
    // 健康检查只给数字和时刻，不给操作员。
    internal sealed class WedgedState
    {
        public const long StrikeWindowTicks = 10L * 60L * TimeSpan.TicksPerSecond;
        public const long RecycleWindowTicks = 30L * 60L * TimeSpan.TicksPerSecond;
        public const long RestartGapTicks = 30L * 60L * TimeSpan.TicksPerSecond;
        const long DayTicks = 24L * 3600L * TimeSpan.TicksPerSecond;
        const int MaxMarks = 512;
        const int MaxFailures = 48;

        sealed class Op
        {
            public int Strikes;
            public long LastStrike;
            public long RecycledAt;
        }

        readonly object _gate = new object();
        readonly int _threshold;
        readonly Dictionary<string, Op> _ops = new Dictionary<string, Op>(StringComparer.Ordinal);
        readonly List<long> _strikes = new List<long>();
        readonly List<long> _recycles = new List<long>();
        readonly List<long> _restarts = new List<long>();
        // 桥自己结束进程的全部时刻（自愈重启、看门狗、崩溃），升序，FailChain 用；_restarts 只是其中的自愈重启（健康检查）。
        readonly List<long> _failures = new List<long>();
        long _lastAt;

        public WedgedState(int threshold)
        {
            _threshold = threshold < 0 ? 0 : threshold;
        }

        public int Threshold
        {
            get { return _threshold; }
        }

        // 进程启动时把文件里记着的结束进程记录放进来：限频、FailChain 和 restarts_24h 跨进程有效。
        public void SeedFailures(FailRecord[] records)
        {
            if (records == null)
            {
                return;
            }
            lock (_gate)
            {
                for (int i = 0; i < records.Length; i++)
                {
                    _failures.Add(records[i].Ticks);
                    if (records[i].Wedged)
                    {
                        _restarts.Add(records[i].Ticks);
                    }
                }
                _failures.Sort();
                _restarts.Sort();
            }
        }

        // 判为卡住一次。strikes 返回该操作员此刻的连续次数（换线程那一次是阈值本身）。
        // policy 只在升级时调用：返回 null 表示第二步没开（WedgedRestart.Policy）。
        public int Strike(string op, long now, Func<ScmPolicy> policy, out int strikes)
        {
            strikes = 0;
            if (_threshold <= 0 || string.IsNullOrEmpty(op))
            {
                return WedgedAction.None;
            }
            lock (_gate)
            {
                Mark(_strikes, now);
                _lastAt = now;
                Op o = Get(op, now);
                if (o.RecycledAt > 0 && now - o.RecycledAt <= RecycleWindowTicks)
                {
                    o.Strikes++;
                    o.LastStrike = now;
                    strikes = o.Strikes;
                    return Escalate(now, policy);
                }
                if (o.Strikes > 0 && now - o.LastStrike > StrikeWindowTicks)
                {
                    o.Strikes = 0;
                }
                o.Strikes++;
                o.LastStrike = now;
                strikes = o.Strikes;
                if (o.Strikes < _threshold)
                {
                    return WedgedAction.Counted;
                }
                o.Strikes = 0;
                o.RecycledAt = now;
                Mark(_recycles, now);
                return WedgedAction.Recycle;
            }
        }

        // 调用方持有 _gate。
        int Escalate(long now, Func<ScmPolicy> policy)
        {
            ScmPolicy p = policy == null ? null : policy();
            if (p == null)
            {
                return WedgedAction.RestartOff;
            }
            long last = _failures.Count == 0 ? 0 : _failures[_failures.Count - 1];
            if (last > 0 && now - last < RestartGapTicks)
            {
                return WedgedAction.RestartLimited;
            }
            if (!FailChain.Allows(_failures, now, p))
            {
                return WedgedAction.RestartReserve;
            }
            _failures.Add(now);
            while (_failures.Count > MaxFailures)
            {
                _failures.RemoveAt(0);
            }
            Mark(_restarts, now);
            return WedgedAction.Restart;
        }

        // 该操作员登录成功：连续次数和换线程时刻一起清掉。
        public void Ok(string op)
        {
            if (string.IsNullOrEmpty(op))
            {
                return;
            }
            lock (_gate)
            {
                _ops.Remove(op);
            }
        }

        // 自检用：该操作员此刻的连续次数（没有记录为 0）。
        internal int StrikesOf(string op)
        {
            lock (_gate)
            {
                Op o;
                return op != null && _ops.TryGetValue(op, out o) ? o.Strikes : 0;
            }
        }

        // 有操作员还处在卡住状态：最近一次判为卡住在 RecycleWindowTicks 之内且之后没登录成功过。
        public bool Active(long now)
        {
            lock (_gate)
            {
                foreach (Op o in _ops.Values)
                {
                    if (now - o.LastStrike <= RecycleWindowTicks)
                    {
                        return true;
                    }
                }
                return false;
            }
        }

        // 健康检查片段 ,"wedged_logins":{active, count_24h, last_at, recycles_24h, restarts_24h}，last_at 为 UTC 或 null。
        public string HealthFragment(long now)
        {
            bool active = Active(now);
            lock (_gate)
            {
                StringBuilder sb = new StringBuilder();
                sb.Append(",\"wedged_logins\":{\"active\":").Append(active ? "true" : "false");
                sb.Append(",\"count_24h\":").Append(Count(_strikes, now));
                sb.Append(",\"last_at\":").Append(_lastAt <= 0 ? "null" : "\"" + Iso(_lastAt) + "\"");
                sb.Append(",\"recycles_24h\":").Append(Count(_recycles, now));
                sb.Append(",\"restarts_24h\":").Append(Count(_restarts, now));
                sb.Append('}');
                return sb.ToString();
            }
        }

        // 调用方持有 _gate。顺带删掉一天没再判为卡住的操作员。
        Op Get(string op, long now)
        {
            List<string> stale = null;
            foreach (KeyValuePair<string, Op> pair in _ops)
            {
                if (now - pair.Value.LastStrike > DayTicks)
                {
                    if (stale == null)
                    {
                        stale = new List<string>();
                    }
                    stale.Add(pair.Key);
                }
            }
            for (int i = 0; stale != null && i < stale.Count; i++)
            {
                _ops.Remove(stale[i]);
            }
            Op o;
            if (!_ops.TryGetValue(op, out o))
            {
                o = new Op();
                _ops[op] = o;
            }
            return o;
        }

        static void Mark(List<long> marks, long now)
        {
            marks.Add(now);
            while (marks.Count > MaxMarks || (marks.Count > 0 && now - marks[0] > DayTicks))
            {
                marks.RemoveAt(0);
            }
        }

        static string Count(List<long> marks, long now)
        {
            int n = 0;
            for (int i = 0; i < marks.Count; i++)
            {
                if (now - marks[i] <= DayTicks)
                {
                    n++;
                }
            }
            return n.ToString(CultureInfo.InvariantCulture);
        }

        internal static string Iso(long ticks)
        {
            return new DateTime(ticks, DateTimeKind.Utc).ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
        }
    }
}
