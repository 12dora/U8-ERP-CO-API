using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一个子系统的许可状况。时间都是 UTC ticks，0 表示没有。
    internal sealed class LicenseSub
    {
        public long LastFull;
        public long LastOk;
        // 近 24 小时里 U8 回「加密点数已饱和」的时刻（含重试），最多 LicenseState.MaxFulls 个。
        public List<long> Fulls = new List<long>();
        // 最近一次登录在重试后仍以饱和告终，之后还没有成功登录过。
        public bool Down;
        // UA_TaskLog 登记的工作站数，-1 表示没采到。
        public int Used = -1;
        // 许可总数，-1 表示不知道。
        public int Limit = -1;
        // Used / Limit 来自加密服务器的租约（所在产品包的实际占用和总数），不是 UA_TaskLog 的下限。
        public bool Exact;

        public LicenseSub Copy()
        {
            LicenseSub copy = (LicenseSub)MemberwiseClone();
            copy.Fulls = new List<long>(Fulls);
            return copy;
        }
    }

    // 进程内的许可点数状态，登录线程和采样线程都会写，全部在 Gate 里改。
    // 只存子系统号和数字；连接串只给采样线程用，不写日志、审计和响应。
    internal static class LicenseState
    {
        public const int MaxFulls = 1000;
        const long ConnRefreshTicks = 30L * TimeSpan.TicksPerMinute;
        static readonly object Gate = new object();
        static readonly Dictionary<string, LicenseSub> Subs = new Dictionary<string, LicenseSub>(StringComparer.Ordinal);
        static Dictionary<string, int> _cfgLimits = new Dictionary<string, int>(StringComparer.Ordinal);
        static HashSet<string> _modules = new HashSet<string>(StringComparer.Ordinal);
        static int _warnFree = 1;
        static bool _sampling;
        static bool _sampled;
        static string _conn;
        static long _connTicks;
        static volatile string _lastError;
        public const string SourceLeases = "leases";
        public const string SourceTaskLog = "tasklog";
        static string _source = SourceTaskLog;
        static Dictionary<string, LicensePack> _packs = new Dictionary<string, LicensePack>(StringComparer.Ordinal);
        static Dictionary<string, int> _u8Limits;
        // 最近一次租约采样成功的时刻（UTC ticks），以及租约数字的有效期（3 个采样间隔，0 表示不采样）。
        static long _leaseTicks;
        static long _staleTicks;

        public static void Configure(BridgeConfig cfg, IEnumerable<string> modules)
        {
            lock (Gate)
            {
                Subs.Clear();
                _packs = new Dictionary<string, LicensePack>(StringComparer.Ordinal);
                _source = SourceTaskLog;
                _u8Limits = null;
                _sampled = false;
                _leaseTicks = 0;
                _staleTicks = 3L * cfg.LicenseSampleMinutes * TimeSpan.TicksPerMinute;
                _warnFree = cfg.LicenseWarnFree;
                _sampling = cfg.LicenseSampleMinutes > 0;
                _cfgLimits = new Dictionary<string, int>(cfg.LicenseLimits, StringComparer.Ordinal);
                _modules = new HashSet<string>(modules, StringComparer.Ordinal);
                foreach (string sub in _cfgLimits.Keys)
                {
                    _modules.Add(sub);
                }
                SetLimits(null);
            }
        }

        public static int WarnFree
        {
            get
            {
                lock (Gate)
                {
                    return _warnFree;
                }
            }
        }

        // 最近一次采样的异常（不含连接串和口令），没有为 null。
        public static string LastError
        {
            get { return _lastError; }
        }

        public static string ConnString
        {
            get
            {
                lock (Gate)
                {
                    return _conn;
                }
            }
        }

        // 每次 U8 回「已饱和」都记一次（含重试）。
        public static void NoteSaturated(string sub)
        {
            if (!ConfigRules.SubCode(sub))
            {
                return;
            }
            long now = DateTime.UtcNow.Ticks;
            lock (Gate)
            {
                LicenseSub s = Get(sub);
                s.LastFull = now;
                Prune(s, now);
                if (s.Fulls.Count >= MaxFulls)
                {
                    s.Fulls.RemoveAt(0);
                }
                s.Fulls.Add(now);
            }
        }

        // 重试用完仍失败。只有饱和才记 license_full 事件（每个请求一行，不是每次重试一行）。
        public static void NoteFinal(string sub, BridgeException ex, int attempts)
        {
            try
            {
                if (ex == null || ex.Code != LicenseRetry.FullCode || !ConfigRules.SubCode(sub))
                {
                    return;
                }
                lock (Gate)
                {
                    Get(sub).Down = true;
                }
                Dictionary<string, object> evt = new Dictionary<string, object>();
                evt["sub"] = sub;
                evt["attempts"] = attempts;
                AuditEvent.Write("license_full", evt);
            }
            catch (Exception)
            {
                // 记状态失败不能盖住原来的登录错误。
            }
        }

        // 登录成功。饱和过的子系统第一次恢复时记 license_ok；采样开着时顺便记下连接串（30 分钟刷新一次）。
        public static void NoteOk(string sub, object login, BridgeConfig cfg)
        {
            try
            {
                long now = DateTime.UtcNow.Ticks;
                bool recovered = false;
                bool wantConn;
                lock (Gate)
                {
                    if (ConfigRules.SubCode(sub))
                    {
                        LicenseSub s = Get(sub);
                        s.LastOk = now;
                        recovered = s.Down;
                        s.Down = false;
                    }
                    wantConn = _sampling && (_conn == null || now - _connTicks > ConnRefreshTicks);
                }
                if (recovered)
                {
                    Dictionary<string, object> evt = new Dictionary<string, object>();
                    evt["sub"] = sub;
                    AuditEvent.Write("license_ok", evt);
                }
                if (wantConn)
                {
                    RememberConn(AdoXml.ConnectionString(login, cfg), now);
                }
            }
            catch (Exception)
            {
                // 登录已经成功，这里失败只影响采样。
            }
        }

        static void RememberConn(string conn, long now)
        {
            if (conn == null || conn.Length == 0)
            {
                return;
            }
            lock (Gate)
            {
                _conn = conn;
                _connTicks = now;
            }
        }

        // 采样结果：只保留关心的子系统。没出现在结果里的记 0。
        public static void ApplyUsed(Dictionary<string, int> used)
        {
            lock (Gate)
            {
                foreach (string sub in _modules)
                {
                    int count;
                    Get(sub).Used = used.TryGetValue(sub, out count) ? count : 0;
                }
                _sampled = true;
            }
        }

        // U8 给出的许可总数；为 null（读不到）时用 config.json 的 licenseLimits。
        public static void ApplyLimits(Dictionary<string, int> fromU8)
        {
            lock (Gate)
            {
                if (fromU8 != null)
                {
                    _u8Limits = new Dictionary<string, int>(fromU8, StringComparer.Ordinal);
                }
                SetLimits(fromU8);
            }
        }

        // 租约采样成功：桥登录的子系统（_modules：LicenseSampler.LoginModules 加 licenseLimits 的键）的 used / limit
        // 换成所在产品包（或独立模块）的实际数，没出现的清成不知道。桥不登录的模块（如只含 CA 的包）不进 license / license_detail，
        // 满了也不告警；全部产品包照样进 license_packs。
        public static void ApplyLeases(LicenseLeaseSnapshot snap)
        {
            lock (Gate)
            {
                ClearNumbers();
                foreach (KeyValuePair<string, int[]> pair in snap.Modules)
                {
                    if (_modules.Contains(pair.Key) && ConfigRules.SubCode(pair.Key))
                    {
                        LicenseSub s = Get(pair.Key);
                        s.Used = pair.Value[0];
                        s.Limit = pair.Value[1];
                        s.Exact = true;
                    }
                }
                _packs = new Dictionary<string, LicensePack>(StringComparer.Ordinal);
                foreach (KeyValuePair<string, LicensePack> pair in snap.Packs)
                {
                    _packs[pair.Key] = pair.Value.Copy();
                }
                _source = SourceLeases;
                _sampled = true;
                _leaseTicks = DateTime.UtcNow.Ticks;
            }
        }

        // 租约读不到、回落到 UA_TaskLog：从租约切回来时清掉包的数字，总数恢复成上一次读到的 U8 总数或 licenseLimits。
        public static void UseTaskLog()
        {
            lock (Gate)
            {
                FallBack();
            }
        }

        // 租约数字超过 3 个采样间隔没有更新（采样线程卡住或死掉）时同样回落，不把旧数字当现状。
        // Snapshot 每次都先调它；自检直接传时刻。
        public static void ExpireStale(long now)
        {
            lock (Gate)
            {
                if (_source == SourceLeases && _staleTicks > 0 && now - _leaseTicks > _staleTicks)
                {
                    FallBack();
                    _lastError = "租约数字超过 3 个采样间隔没有更新，已不再采用";
                }
            }
        }

        // 调用方持有 Gate。数字清空后在 UA_TaskLog 采到之前算没采样过（总体 unknown，不是 ok）。
        static void FallBack()
        {
            if (_source == SourceTaskLog)
            {
                return;
            }
            ClearNumbers();
            _packs = new Dictionary<string, LicensePack>(StringComparer.Ordinal);
            _source = SourceTaskLog;
            _sampled = false;
            SetLimits(_u8Limits);
        }

        // 调用方持有 Gate。
        static void ClearNumbers()
        {
            foreach (LicenseSub s in Subs.Values)
            {
                s.Used = -1;
                s.Limit = -1;
                s.Exact = false;
            }
        }

        public static string Source
        {
            get
            {
                lock (Gate)
                {
                    return _source;
                }
            }
        }

        // 各产品包的副本（租约来源时才有）。
        public static Dictionary<string, LicensePack> Packs()
        {
            Dictionary<string, LicensePack> copy = new Dictionary<string, LicensePack>(StringComparer.Ordinal);
            lock (Gate)
            {
                foreach (KeyValuePair<string, LicensePack> pair in _packs)
                {
                    copy[pair.Key] = pair.Value.Copy();
                }
            }
            return copy;
        }

        // 调用方持有 Gate。
        static void SetLimits(Dictionary<string, int> fromU8)
        {
            foreach (string sub in _modules)
            {
                int limit;
                if (fromU8 != null && fromU8.TryGetValue(sub, out limit))
                {
                    Get(sub).Limit = limit;
                }
                else if (_cfgLimits.TryGetValue(sub, out limit))
                {
                    Get(sub).Limit = limit;
                }
                else if (fromU8 != null)
                {
                    Get(sub).Limit = -1;
                }
            }
        }

        public static void NoteError(string text)
        {
            _lastError = text;
        }

        // 给 LicenseView 用的快照：各子系统的副本，以及是否采样过。
        public static Dictionary<string, LicenseSub> Snapshot(out bool sampled)
        {
            Dictionary<string, LicenseSub> copy = new Dictionary<string, LicenseSub>(StringComparer.Ordinal);
            ExpireStale(DateTime.UtcNow.Ticks);
            lock (Gate)
            {
                sampled = _sampled;
                foreach (KeyValuePair<string, LicenseSub> pair in Subs)
                {
                    copy[pair.Key] = pair.Value.Copy();
                }
            }
            return copy;
        }

        // 调用方持有 Gate。
        static LicenseSub Get(string sub)
        {
            LicenseSub s;
            if (!Subs.TryGetValue(sub, out s))
            {
                s = new LicenseSub();
                Subs[sub] = s;
            }
            return s;
        }

        // 调用方持有 Gate。丢掉 24 小时以前的饱和时刻。
        static void Prune(LicenseSub s, long now)
        {
            long cutoff = now - TimeSpan.TicksPerDay;
            int drop = 0;
            while (drop < s.Fulls.Count && s.Fulls[drop] < cutoff)
            {
                drop++;
            }
            if (drop > 0)
            {
                s.Fulls.RemoveRange(0, drop);
            }
        }
    }
}
