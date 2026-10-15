using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 登录复用的停用时段部分（loginReuseOffHours）：解析与报错、边界、时段内用完即关、进入时段时清空缓存。
    internal static partial class LoginCacheSelfTest
    {
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
    }
}
