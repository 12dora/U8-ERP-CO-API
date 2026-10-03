using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的登录复用部分（LoginCache、StaExec.KeepAfter）：关闭时不缓存、同键复用、不放回即关闭、
    // 每线程上限与淘汰顺序、闲置有效期与刷新、空闲清理（Sweep）、距真实登录的最长时间、不健康不复用、
    // 读线程不留、线程隔离与 Drain、放回条件（含 QM、AR、AP 一律不放回）、计数（含 logins_closed：每一次真正关掉）、
    // 停用时段（loginReuseOffHours：解析与报错、边界、时段内用完即关、进入时段时清空缓存）和健康检查片段。
    // 经 LoginCache.Opener / Healthy / Now 和 StaExec.TranCountProbe 替换登录、健康检查、时钟和 @@TRANCOUNT；
    // 登录对象一律为空（Dispose 什么都不做），不建 COM、不连库。是否真的关掉靠 logins_closed 的增量判断。
    internal static class LoginCacheSelfTest
    {
        const int Cap = 16;
        const int TtlSeconds = 60;
        const int MaxAge = 600;
        const string BadOperator = "bad";
        static DateTime _clock = new DateTime(2026, 9, 26, 8, 0, 0, DateTimeKind.Utc);

        public static void Run()
        {
            bool was = LoginCache.Enabled;
            ReuseWindow window = LoginCache.Window;
            int maxAge = LoginCache.MaxAgeSeconds;
            LoginCache.Opener = Fake;
            LoginCache.Healthy = Probe;
            LoginCache.Now = Clock;
            LoginCache.MaxAgeSeconds = MaxAge;
            try
            {
                CheckOff();
                CheckReuse();
                CheckKeepFalse();
                CheckLru();
                CheckTtl();
                CheckSweep();
                CheckMaxAge();
                CheckUnhealthy();
                CheckNoKeepThread();
                CheckDrain();
                CheckKeepAfter();
                CheckKeepAfterSub();
                CheckWindowParse();
                CheckWindowBounds();
                CheckWindowRelease();
                CheckWindowSweep();
                CheckHealth();
            }
            finally
            {
                LoginCache.Drain();
                LoginCache.Opener = null;
                LoginCache.Healthy = null;
                LoginCache.Now = null;
                LoginCache.MaxAgeSeconds = maxAge;
                StaExec.TranCountProbe = null;
                LoginCache.Configure(was, Cap, TtlSeconds, window);
            }
        }

        // 关闭：放回也不留，每次都是新登录、用完即关，复用数不变。
        static void CheckOff()
        {
            LoginCache.Configure(false, Cap, TtlSeconds);
            long[] before = Counters();
            WorkItem item = Item("op001");
            U8Session a = LoginCache.Acquire(item, "SA");
            LoginCache.Release(a, item, "SA", true);
            Expect("login off no cache", LoginCache.CachedCountForThisThread() == 0);
            U8Session b = LoginCache.Acquire(item, "SA");
            Expect("login off fresh", !object.ReferenceEquals(a, b));
            LoginCache.Release(b, item, "SA", true);
            LoginCache.Release(null, item, "SA", true);
            Delta("login off counters", before, 2, 0, 2);
            Expect("login off flag", !LoginCache.Enabled
                && LoginCache.HealthFragment().StartsWith(",\"login_reuse\":false,", StringComparison.Ordinal));
        }

        // 开启：同键复用同一个登录；子系统、登录日期、口令任一不同都是新登录。
        static void CheckReuse()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            long[] before = Counters();
            WorkItem item = Item("op001");
            U8Session a = LoginCache.Acquire(item, "SA");
            Expect("login fresh not reused", !LoginCache.LastAcquireReused);
            LoginCache.Release(a, item, "SA", true);
            Expect("login put", LoginCache.CachedCountForThisThread() == 1);
            U8Session b = LoginCache.Acquire(item, "SA");
            Expect("login hit", object.ReferenceEquals(a, b) && LoginCache.LastAcquireReused
                && LoginCache.CachedCountForThisThread() == 0);
            LoginCache.Release(b, item, "SA", true);
            U8Session pu = LoginCache.Acquire(item, "PU");
            Expect("login sub key", !object.ReferenceEquals(a, pu));
            WorkItem other = Item("op001");
            other.Date = "2026-09-27";
            Expect("login date key", !object.ReferenceEquals(a, LoginCache.Acquire(other, "SA")));
            other = Item("op001");
            other.Password = "other";
            Expect("login password key", !object.ReferenceEquals(a, LoginCache.Acquire(other, "SA")));
            Delta("login reuse counters", before, 4, 1, 0);
            Expect("login on flag", LoginCache.Enabled
                && LoginCache.HealthFragment().StartsWith(",\"login_reuse\":true,", StringComparison.Ordinal));
            Delta("login reuse drain", before, 4, 1, 1, LoginCache.Drain);
        }

        // keep=false：当场关闭，不进缓存，下一次是新登录。
        static void CheckKeepFalse()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            WorkItem item = Item("op001");
            U8Session a = Cycle("op001");
            U8Session b = LoginCache.Acquire(item, "SA");
            Expect("login keep hit", object.ReferenceEquals(a, b));
            long[] before = Counters();
            LoginCache.Release(b, item, "SA", false);
            Delta("login keep false closed", before, 0, 0, 1);
            Expect("login keep false empty", LoginCache.CachedCountForThisThread() == 0);
            U8Session c = LoginCache.Acquire(item, "SA");
            Expect("login keep false fresh", !object.ReferenceEquals(a, c));
            LoginCache.Release(c, item, "SA", false);
            LoginCache.Drain();
        }

        // 每线程最多 16 个；放满后再放，关掉最久没用的。用过一次的排到最后，不会先被挤掉。
        static void CheckLru()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            List<U8Session> kept = new List<U8Session>();
            for (int i = 0; i < Cap; i++)
            {
                kept.Add(Cycle(Op(i)));
            }
            Expect("login lru full", LoginCache.CachedCountForThisThread() == Cap);
            Expect("login lru touch", object.ReferenceEquals(kept[0], Cycle(Op(0))));
            long[] before = Counters();
            Cycle(Op(Cap));
            Delta("login lru evict closed", before, 1, 0, 1);
            Expect("login lru cap", LoginCache.CachedCountForThisThread() == Cap);
            Expect("login lru evict oldest", !object.ReferenceEquals(kept[1], LoginCache.Acquire(Item(Op(1)), "SA")));
            Expect("login lru keep touched", object.ReferenceEquals(kept[0], LoginCache.Acquire(Item(Op(0)), "SA")));
            Expect("login lru keep newer", object.ReferenceEquals(kept[2], LoginCache.Acquire(Item(Op(2)), "SA")));
            LoginCache.Drain();
        }

        // 闲置 60 秒作废；每次放回重新计时；过期的在下一次取用时关掉。
        static void CheckTtl()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            U8Session a = Cycle("op001");
            Advance(TtlSeconds - 1);
            Expect("login ttl live", object.ReferenceEquals(a, Cycle("op001")));
            Advance(TtlSeconds - 1);
            Expect("login ttl refreshed", object.ReferenceEquals(a, Cycle("op001")));
            Advance(TtlSeconds);
            long[] before = Counters();
            WorkItem item = Item("op001");
            U8Session b = LoginCache.Acquire(item, "SA");
            Expect("login ttl expired", !object.ReferenceEquals(a, b) && LoginCache.CachedCountForThisThread() == 0);
            Delta("login ttl expired closed", before, 1, 0, 1);
            LoginCache.Release(b, item, "SA", false);
            LoginCache.Drain();
        }

        // 线程空闲时 Sweep 关掉已过闲置期的登录，没过期的不动。
        static void CheckSweep()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            Cycle(Op(0));
            Advance(TtlSeconds / 2);
            Cycle(Op(1));
            long[] before = Counters();
            LoginCache.Sweep();
            Expect("login sweep live", LoginCache.CachedCountForThisThread() == 2);
            Advance(TtlSeconds / 2);
            LoginCache.Sweep();
            Expect("login sweep one", LoginCache.CachedCountForThisThread() == 1);
            Advance(TtlSeconds);
            LoginCache.Sweep();
            Expect("login sweep all", LoginCache.CachedCountForThisThread() == 0);
            Delta("login sweep closed", before, 0, 0, 2);
            LoginCache.Sweep();
            LoginCache.Drain();
        }

        // 每 50 秒用一次、从不闲置过期，距真实登录满 600 秒也要重新登录。
        static void CheckMaxAge()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            U8Session a = Cycle("op001");
            int step = 50;
            for (int t = step; t < MaxAge; t += step)
            {
                Advance(step);
                Expect("login max age live " + t.ToString(CultureInfo.InvariantCulture),
                    object.ReferenceEquals(a, Cycle("op001")));
            }
            Advance(step);
            long[] before = Counters();
            U8Session b = Cycle("op001");
            Expect("login max age fresh", !object.ReferenceEquals(a, b));
            Delta("login max age counters", before, 1, 0, 1);
            Expect("login max age reborn", object.ReferenceEquals(b, Cycle("op001")));
            LoginCache.Drain();
        }

        // 命中但健康检查不过：关掉，按新登录处理，不算复用。
        static void CheckUnhealthy()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            U8Session a = Cycle(BadOperator);
            long[] before = Counters();
            U8Session b = LoginCache.Acquire(Item(BadOperator), "SA");
            Expect("login unhealthy fresh", !object.ReferenceEquals(a, b) && LoginCache.CachedCountForThisThread() == 0);
            Delta("login unhealthy counters", before, 1, 0, 1);
            LoginCache.Drain();
        }

        // 读线程（MarkNoKeepThread）keep=true 也不留；标记只影响那个线程。
        static void CheckNoKeepThread()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            long[] before = Counters();
            int left = -1;
            Thread reader = new Thread(delegate()
            {
                LoginCache.MarkNoKeepThread();
                Cycle("op001");
                left = LoginCache.CachedCountForThisThread();
            });
            reader.Start();
            reader.Join();
            Expect("login read thread no keep", left == 0);
            Delta("login read thread closed", before, 1, 0, 1);
            Cycle("op001");
            Expect("login write thread keeps", LoginCache.CachedCountForThisThread() == 1);
            LoginCache.Drain();
        }

        // 缓存按线程分开；Drain 只清本线程、逐个关掉，清完再取是新登录。
        static void CheckDrain()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            U8Session a = Cycle(Op(0));
            Cycle(Op(1));
            Cycle(Op(2));
            Expect("login drain before", LoginCache.CachedCountForThisThread() == 3);
            int seen = -1;
            Thread other = new Thread(delegate() { seen = LoginCache.CachedCountForThisThread(); });
            other.Start();
            other.Join();
            Expect("login thread local", seen == 0 && LoginCache.CachedCountForThisThread() == 3);
            Delta("login drain closed", Counters(), 0, 0, 3, LoginCache.Drain);
            Expect("login drain empty", LoginCache.CachedCountForThisThread() == 0);
            LoginCache.Drain();
            Expect("login drain fresh", !object.ReferenceEquals(a, LoginCache.Acquire(Item(Op(0)), "SA")));
            LoginCache.Drain();
        }

        // StaExec.KeepAfter：关闭、DropLogin、有残留事务、查不了都不放回；干净或从没开过连接才放回。
        static void CheckKeepAfter()
        {
            LoginCache.Configure(false, Cap, TtlSeconds);
            StaExec.TranCountProbe = delegate(WorkContext c) { return "0"; };
            Expect("keep after off", !StaExec.KeepAfter(new WorkContext()));
            LoginCache.Configure(true, Cap, TtlSeconds);
            Expect("keep after clean", StaExec.KeepAfter(new WorkContext()));
            Expect("keep after null ctx", !StaExec.KeepAfter(null));
            WorkContext dropped = new WorkContext();
            dropped.DropLogin();
            Expect("keep after drop", !StaExec.KeepAfter(dropped));
            StaExec.TranCountProbe = delegate(WorkContext c) { return "1"; };
            Expect("keep after dirty", !StaExec.KeepAfter(new WorkContext()));
            StaExec.TranCountProbe = delegate(WorkContext c) { throw new InvalidOperationException("probe"); };
            Expect("keep after probe throws", !StaExec.KeepAfter(new WorkContext()));
            StaExec.TranCountProbe = null;
            WorkContext idle = new WorkContext();
            Expect("keep after conn never opened", !idle.ConnOpened && StaExec.KeepAfter(idle));
        }

        // 登录子系统 QM、AR、AP 的请求一律不放回；SA、PU、未指定（按 SA）照常放回。
        static void CheckKeepAfterSub()
        {
            LoginCache.Configure(true, Cap, TtlSeconds);
            StaExec.TranCountProbe = delegate(WorkContext c) { return "0"; };
            try
            {
                Expect("keep after QM", !StaExec.KeepAfter(SubContext("QM")));
                Expect("keep after AR", !StaExec.KeepAfter(SubContext("AR")));
                Expect("keep after AP", !StaExec.KeepAfter(SubContext("AP")));
                Expect("keep after SA", StaExec.KeepAfter(SubContext("SA")));
                Expect("keep after PU", StaExec.KeepAfter(SubContext("PU")));
                Expect("keep after default sub", StaExec.KeepAfter(SubContext(null)));
            }
            finally
            {
                StaExec.TranCountProbe = null;
            }
        }

        // 停用时段的写法与报错：缺少或 null 不设；其余写错一律在加载时报错。
        static void CheckWindowParse()
        {
            Expect("window absent", Window("{}") == null);
            Expect("window null", Window("{\"loginReuseOffHours\":null}") == null);
            Expect("window label", Window(Spec("1-5", "09:00", "18:00")).Label() == "周一至周五 09:00–18:00");
            Expect("window label list", Window(Spec(" 1-5,7 ", "00:00", "23:59")).Label() == "周一至周五、周日 00:00–23:59");
            Expect("window label pair", Window(Spec("1,2,4", "09:00", "10:00")).Label() == "周一、周二、周四 09:00–10:00");
            BadWindow("window not object", "{\"loginReuseOffHours\":\"1-5\"}", "必须是对象");
            BadWindow("window unknown key", "{\"loginReuseOffHours\":{\"days\":\"1-5\",\"start\":\"09:00\",\"end\":\"18:00\",\"tz\":\"UTC\"}}",
                "不认识的键 'tz'");
            BadWindow("window days missing", "{\"loginReuseOffHours\":{\"start\":\"09:00\",\"end\":\"18:00\"}}",
                "缺少配置项 loginReuseOffHours.days");
            BadWindow("window days blank", Spec(" ", "09:00", "18:00"), "缺少配置项 loginReuseOffHours.days");
            BadWindow("window days number", "{\"loginReuseOffHours\":{\"days\":6,\"start\":\"09:00\",\"end\":\"18:00\"}}",
                "loginReuseOffHours.days 必须是字符串");
            BadWindow("window days range", Spec("1-8", "09:00", "18:00"), "days 不合法");
            BadWindow("window days form", Spec("1-", "09:00", "18:00"), "days 不合法");
            BadWindow("window days reversed", Spec("1,6-2", "09:00", "18:00"), "范围 '6-2' 反了");
            BadWindow("window days unicode digit", Spec("١-5", "09:00", "18:00"), "days 不合法");
            BadWindow("window start short", Spec("1-5", "9:00", "18:00"), "start 必须是 HH:MM");
            BadWindow("window end 24", Spec("1-5", "09:00", "24:00"), "end 必须是 HH:MM");
            BadWindow("window end before start", Spec("1-5", "18:00", "09:00"), "必须晚于 start");
            BadWindow("window end equals start", Spec("1-5", "09:00", "09:00"), "必须晚于 start");
        }

        // 含 start、不含 end，精确到秒；只在 days 里的星期生效。2026-09-25 是星期五，09-26 星期六，09-27 星期日，09-28 星期一。
        static void CheckWindowBounds()
        {
            ReuseWindow w = Window(Spec("1-5", "09:00", "18:00"));
            Expect("window before start", !w.Contains(Local(9, 25, 8, 59, 59)));
            Expect("window at start", w.Contains(Local(9, 25, 9, 0, 0)));
            Expect("window before end", w.Contains(Local(9, 25, 17, 59, 59)));
            Expect("window at end", !w.Contains(Local(9, 25, 18, 0, 0)));
            Expect("window saturday", !w.Contains(Local(9, 26, 10, 0, 0)));
            Expect("window sunday", !w.Contains(Local(9, 27, 10, 0, 0)));
            Expect("window monday", w.Contains(Local(9, 28, 9, 0, 0)));
        }

        // 时段内：放回即关，不复用；健康检查 login_reuse 仍为 true、login_reuse_active 为 false。
        // 时段外照常复用；再进入时段时，下一次取用先关掉本线程缓存的登录。
        static void CheckWindowRelease()
        {
            LoginCache.Configure(true, Cap, TtlSeconds, Window(Spec("1-5", "09:00", "18:00")));
            LoginCache.Drain();
            _clock = Local(9, 25, 9, 0, 0);
            long[] before = Counters();
            U8Session a = Cycle("op001");
            Expect("window release closed", LoginCache.CachedCountForThisThread() == 0);
            Expect("window release fresh", !object.ReferenceEquals(a, Cycle("op001")));
            Delta("window release counters", before, 2, 0, 2);
            Expect("window health", LoginCache.Enabled && !LoginCache.Active && LoginCache.HealthFragment()
                .StartsWith(",\"login_reuse\":true,\"login_reuse_active\":false,", StringComparison.Ordinal));
            _clock = Local(9, 25, 18, 0, 0);
            U8Session b = Cycle("op001");
            Expect("window after end reuse", object.ReferenceEquals(b, Cycle("op001")) && LoginCache.Active
                && LoginCache.HealthFragment().StartsWith(",\"login_reuse\":true,\"login_reuse_active\":true,", StringComparison.Ordinal));
            _clock = Local(9, 28, 9, 0, 0);
            before = Counters();
            WorkItem item = Item(Op(9));
            U8Session c = LoginCache.Acquire(item, "SA");
            Expect("window acquire drains", LoginCache.CachedCountForThisThread() == 0 && !LoginCache.LastAcquireReused);
            LoginCache.Release(c, item, "SA", true);
            Delta("window acquire drain counters", before, 1, 0, 2);
            LoginCache.Drain();
        }

        // 时段开始前空闲清理不动没过期的；一进入时段，空闲清理在本线程上关掉全部缓存的登录。
        static void CheckWindowSweep()
        {
            LoginCache.Configure(true, Cap, TtlSeconds, Window(Spec("1-5", "09:00", "18:00")));
            LoginCache.Drain();
            _clock = Local(9, 25, 8, 59, 30);
            Cycle(Op(0));
            Cycle(Op(1));
            long[] before = Counters();
            Advance(29);
            LoginCache.Sweep();
            Expect("window sweep before start", LoginCache.CachedCountForThisThread() == 2);
            Advance(1);
            LoginCache.Sweep();
            Expect("window sweep drained", LoginCache.CachedCountForThisThread() == 0);
            Delta("window sweep closed", before, 0, 0, 2);
            LoginCache.Configure(true, Cap, TtlSeconds);
            LoginCache.Drain();
        }

        static ReuseWindow Window(string json)
        {
            return ReuseWindow.FromConfig((Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json));
        }

        static string Spec(string days, string start, string end)
        {
            return "{\"loginReuseOffHours\":{\"days\":\"" + days + "\",\"start\":\"" + start + "\",\"end\":\"" + end + "\"}}";
        }

        static void BadWindow(string name, string json, string part)
        {
            try
            {
                Window(json);
            }
            catch (InvalidOperationException ex)
            {
                Expect(name + " message", ex.Message.IndexOf(part, StringComparison.Ordinal) >= 0);
                return;
            }
            Expect(name, false);
        }

        // 本地时间（Kind 不是 Utc，LoginCache 不换算），年份固定 2026。
        static DateTime Local(int month, int day, int hour, int minute, int second)
        {
            return new DateTime(2026, month, day, hour, minute, second, DateTimeKind.Local);
        }

        static WorkContext SubContext(string subId)
        {
            WorkItem item = Item("op001");
            item.SubId = subId;
            WorkContext ctx = new WorkContext();
            ctx.Item = item;
            return ctx;
        }

        static void CheckHealth()
        {
            string text = LoginCache.HealthFragment();
            Expect("login health shape", Regex.IsMatch(text,
                "^,\"login_reuse\":(true|false),\"login_reuse_active\":(true|false),"
                + "\"logins_opened\":[0-9]+,\"logins_reused\":[0-9]+,\"logins_closed\":[0-9]+$"));
            Dictionary<string, object> obj = (Dictionary<string, object>)new JavaScriptSerializer()
                .DeserializeObject("{\"ok\":true" + text + "}");
            Expect("login health json", obj["login_reuse"] is bool && obj["login_reuse_active"] is bool
                && obj["logins_opened"] is int
                && obj["logins_reused"] is int && obj["logins_closed"] is int);
        }

        // 取一个登录再原样放回（keep=true），返回取到的那个。
        static U8Session Cycle(string op)
        {
            WorkItem item = Item(op);
            U8Session s = LoginCache.Acquire(item, "SA");
            LoginCache.Release(s, item, "SA", true);
            return s;
        }

        static U8Session Fake(WorkItem item, string subId)
        {
            U8Session s = new U8Session();
            s.OperatorName = item.Operator;
            return s;
        }

        static bool Probe(U8Session s)
        {
            return s.OperatorName != BadOperator;
        }

        static DateTime Clock()
        {
            return _clock;
        }

        static void Advance(int seconds)
        {
            _clock = _clock.AddSeconds(seconds);
        }

        static string Op(int i)
        {
            return "op" + i.ToString(CultureInfo.InvariantCulture);
        }

        static WorkItem Item(string op)
        {
            WorkItem item = new WorkItem();
            item.Acc = "998";
            item.Year = "2024";
            item.Operator = op;
            item.Password = "测试Pass#01";
            item.Date = "2026-09-26";
            return item;
        }

        // 进程累计计数 {opened, reused, closed}。
        static long[] Counters()
        {
            return new long[] { Counter("logins_opened"), Counter("logins_reused"), Counter("logins_closed") };
        }

        static void Delta(string name, long[] before, long opened, long reused, long closed)
        {
            long[] now = Counters();
            Expect(name, now[0] - before[0] == opened && now[1] - before[1] == reused && now[2] - before[2] == closed);
        }

        // 先执行 act 再核对增量。
        static void Delta(string name, long[] before, long opened, long reused, long closed, Action act)
        {
            act();
            Delta(name, before, opened, reused, closed);
        }

        static long Counter(string name)
        {
            Match m = Regex.Match(LoginCache.HealthFragment(), "\"" + name + "\":([0-9]+)");
            Expect("login counter " + name, m.Success);
            return long.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
