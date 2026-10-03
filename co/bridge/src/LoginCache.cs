using System;
using System.Collections.Generic;
using System.Globalization;
using System.Threading;

namespace U8Co
{
    // 登录复用（配置 loginReuse，缺省关闭）：每个写线程自己留一小撮 U8 登录对象，同键的下一次任务直接复用。
    // 登录对象是 STA 上的 RCW，只在创建它的线程上用、在同一线程上 ShutDown，绝不跨线程（[ThreadStatic]）。
    // 键与 AuthCache 相同（账套、年度、操作员、口令、子系统、登录日期的 HMAC），口令或日期一变就是新键。
    // 闲置超过 TTL（StaWorker 配 60 秒）作废，线程空闲时 StaPool.Loop 调 Sweep 清；
    // 不论多常用，距真实登录满 MaxAgeSeconds（600 秒）一律重新登录，口令修改、停用操作员最迟这时生效。
    // 只缓存登录对象本身，不缓存任何 CO、业务对象或连接；本次任务有异常、调用过 ctx.DropLogin()
    // 或连接上留着事务时不放回（StaExec.KeepAfter）。读线程（MarkNoKeepThread）一律用完即关。
    // 缓存中的登录在 ShutDown 之前一直占着该子系统在本工作站的许可点数和 UA_TaskLog 行。
    // 关闭时与原来完全一样：每次新登录、用完即关。
    // 停用时段（配置 loginReuseOffHours，ReuseWindow）：开着复用时，时段内每次取用、放回都按关闭处理（用完即关），
    // 本线程已缓存的登录在下一次取用或空闲清理时在本线程上全部关掉；时段外照常复用。每次取用、放回、清理都现算。
    //
    // 自检（LoginCacheSelfTest）用的内部接口，不碰 COM：
    //   Opener  —— 代替 U8Session.Open，可返回 new U8Session()（登录对象为空，Dispose 什么都不做）；
    //   Healthy —— 代替命中时对 LogState / cUserName 的 COM 检查；
    //   Now     —— 代替 DateTime.UtcNow，用来推进时钟测 TTL 和 MaxAgeSeconds；停用时段也用它
    //              （Kind 为 Utc 时换成本地时间，其他按本地时间直接用）；
    //   MaxAgeSeconds —— 距真实登录的最长复用时间；
    //   CachedCountForThisThread() —— 本线程缓存里的登录数。
    // 用完把这几个委托设回 null（Now 设回 null 也按 UtcNow 处理）。
    internal static class LoginCache
    {
        sealed class Entry
        {
            public string Key;
            public U8Session Session;
            public long BornTicks;
            public long ExpiresTicks;
            // 放回时的腾退轮次（RequestEvict）；空闲清理关掉轮次早于当前的。
            public long Epoch;
        }

        [ThreadStatic]
        static List<Entry> _entries;
        // 取出在用的登录 → 真实登录时刻。放回时据此算绝对有效期。
        [ThreadStatic]
        static Dictionary<U8Session, long> _born;
        [ThreadStatic]
        static bool _noKeep;
        [ThreadStatic]
        static bool _lastReused;

        static volatile bool _on;
        static volatile ReuseWindow _window;
        static int _cap = 16;
        static long _ttlTicks = 60L * TimeSpan.TicksPerSecond;
        static long _opened;
        static long _reused;
        static long _closed;
        // 许可保护（LicenseHold）到上限时加一：此前放回的缓存登录在各自线程的下一次空闲清理时关掉。
        static long _evictEpoch;

        internal static int MaxAgeSeconds = 600;
        internal static Func<WorkItem, string, U8Session> Opener;
        internal static Func<U8Session, bool> Healthy;
        internal static Func<DateTime> Now;

        // 不设停用时段。
        public static void Configure(bool on, int capPerWorker, int ttlSeconds)
        {
            Configure(on, capPerWorker, ttlSeconds, null);
        }

        // StaWorker.Start 调用一次。关闭时不清已有缓存：关着的进程里本来就不会有。window 为 null 表示不设停用时段。
        public static void Configure(bool on, int capPerWorker, int ttlSeconds, ReuseWindow window)
        {
            Interlocked.Exchange(ref _cap, capPerWorker < 1 ? 1 : capPerWorker);
            Interlocked.Exchange(ref _ttlTicks, (ttlSeconds < 1 ? 1L : ttlSeconds) * TimeSpan.TicksPerSecond);
            _window = window;
            _on = on;
        }

        // 配置的开关（loginReuse），不看停用时段；StaExec.KeepAfter 用它，时段内由 Release 当场关掉。
        public static bool Enabled
        {
            get { return _on; }
        }

        public static ReuseWindow Window
        {
            get { return _window; }
        }

        // 自检用：当前的每线程上限与闲置有效期（秒），自检结束时按原值 Configure 回去。
        internal static int CapPerWorker
        {
            get { return Interlocked.CompareExchange(ref _cap, 0, 0); }
        }

        internal static int TtlSeconds
        {
            get { return (int)(Interlocked.Read(ref _ttlTicks) / TimeSpan.TicksPerSecond); }
        }

        // 此刻是否真的复用：开关打开且不在停用时段内。
        public static bool Active
        {
            get { return _on && !Paused(); }
        }

        // 本线程最近一次 Acquire 是否复用了缓存的登录（StaExec 只在真实登录后刷新 AuthCache）。
        public static bool LastAcquireReused
        {
            get { return _lastReused; }
        }

        // 读线程池在线程启动时调用：这个线程上的登录一律用完即关，不进缓存。
        public static void MarkNoKeepThread()
        {
            _noKeep = true;
        }

        // 在当前 STA 线程上取一个登录：关闭时每次新登录；开启时同键、未过期、仍健康的就复用，否则新登录。
        // 登录异常原样抛出（含许可重试后的 503），由调用方处理。
        public static U8Session Acquire(WorkItem item, string subId)
        {
            _lastReused = false;
            if (!_on)
            {
                return OpenNew(item, subId);
            }
            Entry hit = null;
            if (Paused())
            {
                // 进入停用时段：本线程缓存的登录先全部关掉，本次新登录。
                DropCached();
            }
            else
            {
                hit = Take(AuthCache.KeyOf(item, subId));
            }
            if (hit != null)
            {
                if (IsHealthy(hit.Session))
                {
                    Interlocked.Increment(ref _reused);
                    _lastReused = true;
                    MarkOut(hit.Session, hit.BornTicks);
                    NoteOk(hit.Session, item, subId);
                    return hit.Session;
                }
                Kill(hit.Session);
            }
            U8Session fresh = OpenNew(item, subId);
            MarkOut(fresh, Clock());
            return fresh;
        }

        // keep 为假、复用关闭、停用时段内或读线程：立即关闭登录。keep 为真：放回本线程缓存，刷新闲置期，超出上限关掉最久没用的。
        public static void Release(U8Session s, WorkItem item, string subId, bool keep)
        {
            if (s == null)
            {
                return;
            }
            if (!_on)
            {
                Close(s);
                return;
            }
            long born = TakeBorn(s);
            if (!keep || _noKeep || Paused())
            {
                Close(s);
                return;
            }
            string key;
            try
            {
                key = AuthCache.KeyOf(item, subId);
            }
            catch (Exception)
            {
                Kill(s);
                return;
            }
            Put(key, s, born);
        }

        // 线程空闲时（StaPool.Loop，同一 STA、不持队列锁）关掉本线程已过期的登录和腾退轮次之前放回的登录；
        // 停用时段内全部关掉。
        public static void Sweep()
        {
            List<Entry> list = _entries;
            if (list == null)
            {
                return;
            }
            if (_on && Paused())
            {
                DropCached();
                return;
            }
            Expire(list, Clock());
            Evict(list, Interlocked.Read(ref _evictEpoch));
            if (list.Count == 0)
            {
                _entries = null;
            }
        }

        // LicenseHold 到上限时调用：本线程闲置的缓存登录当场关掉（必须在本线程、不持任何锁）。
        public static void DropIdle()
        {
            DropCached();
        }

        // LicenseHold 本线程腾退后仍到上限时调用：其他线程在下一次空闲清理（Sweep）时关掉此前放回的缓存登录。
        public static void RequestEvict()
        {
            Interlocked.Increment(ref _evictEpoch);
        }

        static void Evict(List<Entry> list, long epoch)
        {
            for (int i = list.Count - 1; i >= 0; i--)
            {
                if (list[i].Epoch < epoch)
                {
                    U8Session s = list[i].Session;
                    list.RemoveAt(i);
                    Kill(s);
                }
            }
        }

        // 工作线程退出前（StaPool.Loop）在同一 STA 上关闭本线程缓存的全部登录。看门狗线程不能调用。
        public static void Drain()
        {
            _born = null;
            DropCached();
        }

        // 追加到 StaWorker.HealthJson：复用开关（login_reuse 是配置，login_reuse_active 是此刻是否真的复用，
        // 停用时段内为 false）与进程累计的新登录数、复用数、关闭数（opened − closed 即在用登录数）。
        public static string HealthFragment()
        {
            return string.Format(
                CultureInfo.InvariantCulture,
                ",\"login_reuse\":{0},\"login_reuse_active\":{1},\"logins_opened\":{2},\"logins_reused\":{3},\"logins_closed\":{4}",
                _on ? "true" : "false",
                Active ? "true" : "false",
                Interlocked.Read(ref _opened),
                Interlocked.Read(ref _reused),
                Interlocked.Read(ref _closed));
        }

        internal static int CachedCountForThisThread()
        {
            List<Entry> list = _entries;
            return list == null ? 0 : list.Count;
        }

        // 在本线程上关掉本线程缓存的全部登录（不动在用的登录）。
        static void DropCached()
        {
            List<Entry> list = _entries;
            if (list == null)
            {
                return;
            }
            _entries = null;
            for (int i = 0; i < list.Count; i++)
            {
                Kill(list[i].Session);
            }
            list.Clear();
        }

        // 此刻在停用时段内（没设时段为假）。按桥所在机器的本地时间算。
        static bool Paused()
        {
            ReuseWindow window = _window;
            return window != null && window.Contains(LocalNow());
        }

        static DateTime LocalNow()
        {
            Func<DateTime> now = Now;
            DateTime t = now != null ? now() : DateTime.Now;
            return t.Kind == DateTimeKind.Utc ? t.ToLocalTime() : t;
        }

        static U8Session OpenNew(WorkItem item, string subId)
        {
            Func<WorkItem, string, U8Session> opener = Opener;
            U8Session session = opener != null
                ? opener(item, subId)
                : U8Session.Open(item.Config, WorkRun.LoginAsk(item));
            Interlocked.Increment(ref _opened);
            return session;
        }

        // 复用也算一次成功登录：饱和恢复的 license_ok 与采样连接照常记（与 U8Session.Open 成功时相同）。
        static void NoteOk(U8Session s, WorkItem item, string subId)
        {
            if (s.Login != null && item != null)
            {
                LicenseState.NoteOk(subId, s.Login, item.Config);
            }
        }

        static void MarkOut(U8Session s, long born)
        {
            if (s == null)
            {
                return;
            }
            if (_born == null)
            {
                _born = new Dictionary<U8Session, long>();
            }
            _born[s] = born;
        }

        // 取出时没登记过的（不应出现）按现在算，最多再活一个 MaxAgeSeconds。
        static long TakeBorn(U8Session s)
        {
            long born;
            Dictionary<U8Session, long> map = _born;
            if (map != null && map.TryGetValue(s, out born))
            {
                map.Remove(s);
                return born;
            }
            return Clock();
        }

        // 取出同键条目（取出即离开缓存，用完再由 Release 放回）。顺带关掉已过期的（含超过绝对有效期的）。
        static Entry Take(string key)
        {
            List<Entry> list = _entries;
            if (list == null)
            {
                return null;
            }
            Expire(list, Clock());
            for (int i = 0; i < list.Count; i++)
            {
                if (string.Equals(list[i].Key, key, StringComparison.Ordinal))
                {
                    Entry e = list[i];
                    list.RemoveAt(i);
                    return e;
                }
            }
            return null;
        }

        // 最近用过的排在末尾；同键旧条目先关掉；超出上限从头部（最久没用）关起。
        // 有效期取「现在 + 闲置 TTL」与「真实登录 + MaxAgeSeconds」中较早的，已到期的直接关掉。
        static void Put(string key, U8Session s, long born)
        {
            long now = Clock();
            long expires = Math.Min(now + Interlocked.Read(ref _ttlTicks), born + MaxAgeSeconds * TimeSpan.TicksPerSecond);
            if (now >= expires)
            {
                Kill(s);
                return;
            }
            List<Entry> list = _entries;
            if (list == null)
            {
                list = new List<Entry>();
                _entries = list;
            }
            Expire(list, now);
            DropKey(list, key, s);
            Entry e = new Entry();
            e.Key = key;
            e.Session = s;
            e.BornTicks = born;
            e.ExpiresTicks = expires;
            e.Epoch = Interlocked.Read(ref _evictEpoch);
            list.Add(e);
            int cap = Thread.VolatileRead(ref _cap);
            while (list.Count > cap)
            {
                U8Session oldest = list[0].Session;
                list.RemoveAt(0);
                Kill(oldest);
            }
        }

        static void DropKey(List<Entry> list, string key, U8Session keep)
        {
            for (int i = list.Count - 1; i >= 0; i--)
            {
                if (string.Equals(list[i].Key, key, StringComparison.Ordinal))
                {
                    U8Session old = list[i].Session;
                    list.RemoveAt(i);
                    if (!object.ReferenceEquals(old, keep))
                    {
                        Kill(old);
                    }
                }
            }
        }

        static void Expire(List<Entry> list, long now)
        {
            for (int i = list.Count - 1; i >= 0; i--)
            {
                if (now >= list[i].ExpiresTicks)
                {
                    U8Session s = list[i].Session;
                    list.RemoveAt(i);
                    Kill(s);
                }
            }
        }

        // 命中前确认登录仍可用：LogState 仍是 "0"、cUserName 读得出且与登录时一致。任何异常都算不可用。
        static bool IsHealthy(U8Session s)
        {
            try
            {
                Func<U8Session, bool> probe = Healthy;
                if (probe != null)
                {
                    return probe(s);
                }
                object login = s.Login;
                if (login == null)
                {
                    return false;
                }
                if (Values.Text(ComUtil.Get(login, "LogState")) != "0")
                {
                    return false;
                }
                string name = Values.Text(ComUtil.Get(login, "cUserName"));
                return name != null && name.Length > 0 && string.Equals(name, s.OperatorName, StringComparison.Ordinal);
            }
            catch (Exception)
            {
                return false;
            }
        }

        // 关闭一个经本类取得的登录并计数。复用关闭时异常照原样抛出（与原来的 session.Dispose() 相同）。
        static void Close(U8Session s)
        {
            s.Dispose();
            Interlocked.Increment(ref _closed);
        }

        static void Kill(U8Session s)
        {
            if (s == null)
            {
                return;
            }
            try
            {
                Close(s);
            }
            catch (Exception)
            {
                // 关不掉的登录交给进程退出回收，不能因此打断当前任务或线程退出。
            }
        }

        static long Clock()
        {
            Func<DateTime> now = Now;
            return now != null ? now().Ticks : DateTime.UtcNow.Ticks;
        }
    }
}
