using System;
using System.Collections.Generic;
using System.IO;

namespace U8Co
{
    // --selftest 的「登录状态不是 0」自愈部分（WedgedState、FailChain、WedgedLogin、WedgedRestart.Policy、WedgedRules、健康检查片段）：
    // 连续次数与窗口、登录成功清零、阈值 0 关闭、本进程持有该操作员登录（在用或另一个正在进行的登录）时不记、
    // 换线程只一次之后升级、重启限频（含进程重启前落盘的时刻）、按 SCM 失败计数留一次重启（失败链含看门狗记录）、
    // 结束进程记录文件的读写（临时目录）、重启策略的取值表、配置解析、健康检查形状（不含操作员）、
    // 新线程上的探测登录（口令副本清掉、换线程请求丢掉、失败升级、成功清零）。
    // 经 U8Session.Once 代替 clsLogin（登录对象为空），WedgedLogin.Now / RestartPolicy / RestartHook 代替时钟、策略和重启；
    // 探测用 Watchdog.SelfTestSlot。不起线程、不建 COM、不连库；只在系统临时目录下写一个文件，用完删掉。
    internal static class WedgedLoginSelfTest
    {
        const string Op = "OP001";
        const long Minute = 60L * TimeSpan.TicksPerSecond;
        const long Hour = 60L * Minute;
        static readonly long T0 = new DateTime(2026, 10, 30, 1, 0, 0, DateTimeKind.Utc).Ticks;
        static long _clock;
        static bool _fail;
        static int _restarts;

        public static void Run()
        {
            CheckStrikes();
            CheckEscalate();
            CheckRateLimit();
            CheckChain();
            CheckFile();
            CheckOff();
            CheckSwitch();
            CheckRules();
            CheckHealth();
            RunGlue();
        }

        // 相邻不超过 10 分钟才算连续；成功清零；到阈值换线程，计数清零。
        static void CheckStrikes()
        {
            WedgedState st = new WedgedState(2);
            int n;
            Expect("wedged first", st.Strike(Op, T0, Wide, out n) == WedgedAction.Counted && n == 1);
            st.Ok(Op);
            Expect("wedged ok resets", st.StrikesOf(Op) == 0);
            Expect("wedged again", st.Strike(Op, T0 + Minute, Wide, out n) == WedgedAction.Counted && n == 1);
            Expect("wedged window", st.Strike(Op, T0 + 12 * Minute, Wide, out n) == WedgedAction.Counted && n == 1);
            Expect("wedged recycle", st.Strike(Op, T0 + 13 * Minute, Wide, out n) == WedgedAction.Recycle && n == 2);
            Expect("wedged recycle clears", st.StrikesOf(Op) == 0);
            Expect("wedged other op", st.Strike("OP002", T0 + 13 * Minute, Wide, out n) == WedgedAction.Counted);
        }

        // 换线程之后 30 分钟内再判为卡住：升级，不再换线程；之后成功清掉换线程时刻，重新从第一步数。
        static void CheckEscalate()
        {
            WedgedState st = new WedgedState(2);
            int n;
            st.Strike(Op, T0, Wide, out n);
            Expect("esc recycle", st.Strike(Op, T0 + Minute, Wide, out n) == WedgedAction.Recycle);
            Expect("esc restart", st.Strike(Op, T0 + 2 * Minute, Wide, out n) == WedgedAction.Restart && n == 1);
            Expect("esc limited", st.Strike(Op, T0 + 3 * Minute, Wide, out n) == WedgedAction.RestartLimited);
            Expect("esc off", new WedgedState(1).Strike(Op, T0, Off, out n) == WedgedAction.Recycle);
            WedgedState off = new WedgedState(1);
            off.Strike(Op, T0, Off, out n);
            Expect("esc restart off", off.Strike(Op, T0 + Minute, Off, out n) == WedgedAction.RestartOff);
            off.Ok(Op);
            Expect("esc ok back to step a", off.Strike(Op, T0 + 2 * Minute, Off, out n) == WedgedAction.Recycle);
            WedgedState late = new WedgedState(1);
            late.Strike(Op, T0, Wide, out n);
            Expect("esc after window", late.Strike(Op, T0 + 31 * Minute, Wide, out n) == WedgedAction.Recycle);
        }

        // 距桥上一次结束进程不到 30 分钟限频，进程重启前落盘的时刻（SeedFailures）同样算。
        static void CheckRateLimit()
        {
            int n;
            WedgedState st = new WedgedState(1);
            st.SeedFailures(Recs(true, T0 - 10 * Minute));
            st.Strike(Op, T0, Wide, out n);
            Expect("rate seeded limited", st.Strike(Op, T0 + Minute, Wide, out n) == WedgedAction.RestartLimited);
            WedgedState old = new WedgedState(1);
            old.SeedFailures(Recs(true, T0 - 31 * Minute));
            old.Strike(Op, T0, Wide, out n);
            Expect("rate seeded old", old.Strike(Op, T0 + Minute, Wide, out n) == WedgedAction.Restart);
            Expect("rate after restart", old.Strike(Op, T0 + 20 * Minute, Wide, out n) == WedgedAction.RestartLimited);
            Expect("rate counted", old.HealthFragment(T0 + 20 * Minute).Contains("\"restarts_24h\":2"));
            WedgedState dog = new WedgedState(1);
            dog.SeedFailures(Recs(false, T0 - 10 * Minute));
            dog.Strike(Op, T0, Wide, out n);
            Expect("rate watchdog limited", dog.Strike(Op, T0 + Minute, Wide, out n) == WedgedAction.RestartLimited
                && dog.HealthFragment(T0 + Minute).Contains("\"restarts_24h\":0"));
        }

        // SCM 的失败链：相邻间隔都小于重置周期、以 now 结尾；链长 + 1 ≤ K − 1 才重启（留一次给看门狗卡死）。
        static void CheckChain()
        {
            long day = 24L * Hour;
            Expect("chain two", FailChain.Length(new long[] { T0 - 25L * Hour, T0 - 2 * Hour }, T0, day) == 2);
            Expect("chain broken", FailChain.Length(new long[] { T0 - 50L * Hour, T0 - 25L * Hour }, T0, day) == 0);
            Expect("chain gap", FailChain.Length(new long[] { T0 - 50L * Hour, T0 - 2 * Hour }, T0, day) == 1);
            Expect("chain infinite", FailChain.Length(new long[] { T0 - 100L * Hour, T0 - 50L * Hour }, T0, 0) == 2);
            Expect("chain future", FailChain.Length(new long[] { T0 + Minute }, T0, day) == 0);
            Expect("chain empty", FailChain.Allows(new long[0], T0, Install()));
            Expect("chain reserve", !FailChain.Allows(new long[] { T0 - 2 * Hour }, T0, Install()));
            Expect("chain k1", !FailChain.Allows(new long[0], T0, new ScmPolicy(1, day)));
            Expect("chain k3", FailChain.Allows(new long[] { T0 - 2 * Hour }, T0, new ScmPolicy(3, day)));
            Expect("chain null", !FailChain.Allows(new long[0], T0, null));
            int n;
            // 看门狗卡死 2 小时前用过一次：现装的「重启、重启、无操作」只剩留给卡死的那一次，不重启。
            WedgedState dog = new WedgedState(1);
            dog.SeedFailures(Recs(false, T0 - 2 * Hour));
            dog.Strike(Op, T0, Install, out n);
            Expect("chain watchdog reserve", dog.Strike(Op, T0 + Minute, Install, out n) == WedgedAction.RestartReserve);
            Expect("chain label", WedgedAction.Label(WedgedAction.RestartReserve) == "restart_scm_reserve");
            // 自愈重启 23 小时前用过：仍在一个重置周期内，不重启；超过 24 小时没有失败才再用一次。
            WedgedState recent = new WedgedState(1);
            recent.SeedFailures(Recs(true, T0 - 23 * Hour));
            recent.Strike(Op, T0, Install, out n);
            Expect("chain wedged reserve", recent.Strike(Op, T0 + Minute, Install, out n) == WedgedAction.RestartReserve);
            WedgedState aged = new WedgedState(1);
            aged.SeedFailures(Recs(true, T0 - 24 * Hour - Minute));
            aged.Strike(Op, T0, Install, out n);
            Expect("chain aged restart", aged.Strike(Op, T0 + Minute, Install, out n) == WedgedAction.Restart);
            // 换线程窗口过后再卡住：先换线程，再升级时上一次自愈重启已在链里，不重启。
            Expect("chain recycle again", aged.Strike(Op, T0 + 40 * Minute, Install, out n) == WedgedAction.Recycle);
            Expect("chain then reserve", aged.Strike(Op, T0 + 41 * Minute, Install, out n) == WedgedAction.RestartReserve);
        }

        // 结束进程记录文件：不存在为空；追加、读回种类；坏行丢掉；只留最近 48 行。
        static void CheckFile()
        {
            string dir = Path.Combine(Path.GetTempPath(), "u8co-wedged-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            string path = Path.Combine(dir, "bridge-failfast.log");
            try
            {
                Expect("file missing", WedgedRestart.Load(path).Length == 0);
                Expect("file null", WedgedRestart.Load(null).Length == 0);
                WedgedRestart.Record(path, T0 - Hour, WedgedRestart.KindWatchdog);
                WedgedRestart.Record(path, T0, WedgedRestart.KindWedged);
                File.AppendAllText(path, "bad\r\n12 x y\r\n");
                FailRecord[] got = WedgedRestart.Load(path);
                Expect("file read", got.Length == 2 && got[0].Ticks == T0 - Hour && !got[0].Wedged
                    && got[1].Ticks == T0 && got[1].Wedged);
                for (int i = 0; i < 60; i++)
                {
                    WedgedRestart.Record(path, T0 + i, WedgedRestart.KindCrash);
                }
                Expect("file trimmed", File.ReadAllLines(path).Length == 48);
                WedgedRestart.Record(null, T0, WedgedRestart.KindCrash);
            }
            finally
            {
                try
                {
                    Directory.Delete(dir, true);
                }
                catch (Exception)
                {
                    // 删不掉的临时目录不影响自检结论。
                }
            }
        }

        static void CheckOff()
        {
            WedgedState st = new WedgedState(0);
            int n;
            for (int i = 0; i < 5; i++)
            {
                Expect("off none " + i, st.Strike(Op, T0 + i, Wide, out n) == WedgedAction.None);
            }
            Expect("off empty op", new WedgedState(2).Strike("", T0, Wide, out n) == WedgedAction.None);
            Expect("off health", st.HealthFragment(T0).Contains("\"count_24h\":0"));
        }

        // 控制台一律不重启（配置写 true 也不）；服务：配置不写或 true，且查得到失败动作、第一条为重启才返回策略。
        static void CheckSwitch()
        {
            ScmPolicy scm = Install();
            Expect("switch console default", WedgedRestart.Policy(false, null, scm) == null);
            Expect("switch console true", WedgedRestart.Policy(false, true, scm) == null);
            Expect("switch service default", object.ReferenceEquals(WedgedRestart.Policy(true, null, scm), scm));
            Expect("switch service true", object.ReferenceEquals(WedgedRestart.Policy(true, true, scm), scm));
            Expect("switch service false", WedgedRestart.Policy(true, false, scm) == null);
            Expect("switch service no restart", WedgedRestart.Policy(true, null, new ScmPolicy(0, Hour)) == null);
            Expect("switch service unknown", WedgedRestart.Policy(true, true, null) == null);
            Expect("switch labels", WedgedRestart.ScmLabel(null).Length > 0 && WedgedRestart.ScmLabel(scm).Contains("2")
                && WedgedRestart.ScmLabel(new ScmPolicy(1, 0)).Contains("不重置"));
        }

        static void CheckRules()
        {
            BridgeConfig cfg = new BridgeConfig();
            Dictionary<string, object> map = new Dictionary<string, object>();
            WedgedRules.Load(cfg, map);
            Expect("rules default", cfg.WedgedLoginRecycle == 2 && !cfg.WedgedLoginRestart.HasValue);
            map["wedgedLoginRecycle"] = 0;
            map["wedgedLoginRestart"] = false;
            WedgedRules.Load(cfg, map);
            Expect("rules set", cfg.WedgedLoginRecycle == 0 && cfg.WedgedLoginRestart.HasValue && !cfg.WedgedLoginRestart.Value);
            map["wedgedLoginRestart"] = true;
            WedgedRules.Load(cfg, map);
            Expect("rules true", cfg.WedgedLoginRestart.HasValue && cfg.WedgedLoginRestart.Value);
            ExpectBad("rules over", "wedgedLoginRecycle", 11);
            ExpectBad("rules negative", "wedgedLoginRecycle", -1);
            ExpectBad("rules text", "wedgedLoginRecycle", "2");
            ExpectBad("rules restart text", "wedgedLoginRestart", "true");
            Dictionary<string, object> keys = new Dictionary<string, object>();
            keys["wedgedLoginRecycle"] = 2;
            keys["wedgedLoginRestart"] = true;
            BridgeConfig.CheckKeys(keys);
            string[] lines = WedgedRules.Describe(cfg, "u8co", null);
            Expect("rules describe", lines.Length == 2 && lines[0].Contains("wedgedLoginRecycle")
                && lines[1].Contains("wedgedLoginRestart") && lines[1].Contains("u8co"));
        }

        static void ExpectBad(string name, string key, object value)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map[key] = value;
            try
            {
                WedgedRules.Load(new BridgeConfig(), map);
            }
            catch (InvalidOperationException ex)
            {
                Expect(name + " message", ex.Message.Contains(key));
                return;
            }
            Expect(name, false);
        }

        // 形状固定、不含操作员；active 在卡住期间为真，成功后为假。
        static void CheckHealth()
        {
            WedgedState st = new WedgedState(2);
            string empty = st.HealthFragment(T0);
            Expect("health empty", empty == ",\"wedged_logins\":{\"active\":false,\"count_24h\":0,\"last_at\":null,"
                + "\"recycles_24h\":0,\"restarts_24h\":0}");
            int n;
            st.Strike(Op, T0, Wide, out n);
            st.Strike(Op, T0 + Minute, Wide, out n);
            string busy = st.HealthFragment(T0 + 2 * Minute);
            Expect("health busy", busy == ",\"wedged_logins\":{\"active\":true,\"count_24h\":2,\"last_at\":\"2026-10-30T01:01:00Z\","
                + "\"recycles_24h\":1,\"restarts_24h\":0}");
            Expect("health no operator", busy.IndexOf(Op, StringComparison.OrdinalIgnoreCase) < 0);
            st.Ok(Op);
            Expect("health ok", st.HealthFragment(T0 + 3 * Minute).Contains("\"active\":false"));
            Expect("health day", st.HealthFragment(T0 + 25L * 60L * Minute).Contains("\"count_24h\":0"));
        }

        // 经 U8Session.Open 的整条判定：不在写线程上不记；本进程持有该操作员登录时不记；换线程一次之后请求重启。
        static void RunGlue()
        {
            Func<BridgeConfig, U8Open, string, U8Session> once = U8Session.Once;
            U8Session.Once = FakeLogin;
            WedgedLogin.Now = Clock;
            WedgedLogin.RestartPolicy = Wide;
            WedgedLogin.RestartHook = Restarted;
            _clock = T0;
            _restarts = 0;
            try
            {
                CheckGlueUnbound();
                CheckGlueHeld();
                CheckGlueEscalate();
                CheckProbeFail();
                CheckProbeOk();
            }
            finally
            {
                U8Session.Once = once;
                WedgedLogin.Now = null;
                WedgedLogin.RestartPolicy = null;
                WedgedLogin.RestartHook = null;
                WedgedLogin.BindWorker(-1);
                WedgedLogin.Reset(2);
            }
        }

        static void CheckGlueUnbound()
        {
            WedgedLogin.Reset(2);
            WedgedLogin.BindWorker(-1);
            Fail();
            Fail();
            Expect("glue unbound", WedgedLogin.State.StrikesOf(Op) == 0 && WedgedLogin.TakeRecycle() == null);
        }

        // 在用登录（含复用缓存里的）和另一个正在进行的登录都压住判定；Dispose 之后照常记。
        static void CheckGlueHeld()
        {
            WedgedLogin.Reset(2);
            WedgedLogin.BindWorker(1);
            U8Session held = Open(false);
            Expect("glue held count", WedgedLogin.HeldCount(Op) == 1 && WedgedLogin.OpeningCount(Op) == 0);
            Fail();
            Expect("glue held suppressed", WedgedLogin.State.StrikesOf(Op) == 0);
            held.Dispose();
            Expect("glue released", WedgedLogin.HeldCount(Op) == 0);
            WedgedLogin.Begin(Op);
            Fail();
            WedgedLogin.End(Op);
            Expect("glue opening suppressed", WedgedLogin.State.StrikesOf(Op) == 0);
            Fail();
            Expect("glue strike", WedgedLogin.State.StrikesOf(Op) == 1 && WedgedLogin.OpeningCount(Op) == 0);
            Open(false).Dispose();
            Expect("glue ok resets", WedgedLogin.State.StrikesOf(Op) == 0);
        }

        // 第 2 次请求换线程（参数副本带口令、不写），换线程后再失败请求重启一次，之后限频。
        static void CheckGlueEscalate()
        {
            WedgedLogin.Reset(2);
            WedgedLogin.BindWorker(1);
            Fail();
            Expect("glue no recycle yet", WedgedLogin.TakeRecycle() == null);
            Fail();
            WedgedProbe probe = WedgedLogin.TakeRecycle();
            Expect("glue recycle", probe != null && probe.Worker == 1 && probe.Operator == Op && probe.Strikes == 2
                && probe.Ask != null && probe.Ask.Password == "pw" && !probe.Ask.Write);
            Expect("glue taken once", WedgedLogin.TakeRecycle() == null);
            Fail();
            Expect("glue restart", _restarts == 1 && WedgedLogin.TakeRecycle() == null);
            Fail();
            Expect("glue restart limited", _restarts == 1);
            Expect("glue health", WedgedLogin.HealthFragment().Contains("\"recycles_24h\":1,\"restarts_24h\":1"));
        }

        // 换线程后的探测登录仍失败：经同一套判定升级为重启；口令副本清掉；探测里产生的换线程请求丢掉。
        static void CheckProbeFail()
        {
            WedgedProbe probe = Recycled();
            U8Open ask = probe.Ask;
            _fail = true;
            int before = _restarts;
            _clock += Minute;
            WedgedLogin.Probe(probe, Watchdog.SelfTestSlot);
            Expect("probe fail escalates", _restarts == before + 1);
            Expect("probe fail cleared", probe.Ask == null && ask.Password == null && WedgedLogin.TakeRecycle() == null);
            Expect("probe fail opening", WedgedLogin.OpeningCount(Op) == 0 && WedgedLogin.HeldCount(Op) == 0);
        }

        // 探测登录成功：清零、登录关掉、不重启。
        static void CheckProbeOk()
        {
            WedgedProbe probe = Recycled();
            U8Open ask = probe.Ask;
            _fail = false;
            int before = _restarts;
            WedgedLogin.Probe(probe, Watchdog.SelfTestSlot);
            Expect("probe ok", _restarts == before && WedgedLogin.State.StrikesOf(Op) == 0
                && WedgedLogin.HeldCount(Op) == 0 && ask.Password == null);
            Expect("probe ok health", WedgedLogin.HealthFragment().Contains("\"active\":false"));
            WedgedLogin.Probe(null, Watchdog.SelfTestSlot);
        }

        // 新状态下连续失败两次，取出换线程请求。
        static WedgedProbe Recycled()
        {
            WedgedLogin.Reset(2);
            WedgedLogin.BindWorker(1);
            Fail();
            Fail();
            WedgedProbe probe = WedgedLogin.TakeRecycle();
            Expect("probe recycle", probe != null && probe.Ask != null);
            return probe;
        }

        static void Fail()
        {
            _clock += Minute;
            try
            {
                Open(true).Dispose();
            }
            catch (BridgeException ex)
            {
                Expect("glue fail code", ex.Status == 422 && ex.Code == "login_failed");
                return;
            }
            Expect("glue fail threw", false);
        }

        static U8Session Open(bool fail)
        {
            _fail = fail;
            BridgeConfig cfg = new BridgeConfig();
            cfg.LicenseRetries = 0;
            U8Open ask = new U8Open();
            ask.SubId = "SA";
            ask.Acc = "801";
            ask.Year = "2026";
            ask.User = " op001 ";
            ask.Password = "pw";
            ask.Date = "2026-10-30";
            // 只登录不写：不受写入策略的 holdWritesWhen 影响（其他自检可能留着策略状态）。
            ask.Write = false;
            return U8Session.Open(cfg, ask);
        }

        static U8Session FakeLogin(BridgeConfig cfg, U8Open ask, string subId)
        {
            if (_fail)
            {
                throw new BridgeException(422, "login_failed", LicenseRetry.StateMessage);
            }
            return new U8Session();
        }

        static DateTime Clock()
        {
            return new DateTime(_clock, DateTimeKind.Utc);
        }

        // 宽松的失败动作：开头 10 条重启、1 小时重置；现装的：开头 2 条重启、24 小时重置；没开。
        static ScmPolicy Wide()
        {
            return new ScmPolicy(10, Hour);
        }

        static ScmPolicy Install()
        {
            return new ScmPolicy(2, 24L * Hour);
        }

        static ScmPolicy Off()
        {
            return null;
        }

        static FailRecord[] Recs(bool wedged, long ticks)
        {
            FailRecord r = new FailRecord();
            r.Ticks = ticks;
            r.Wedged = wedged;
            return new FailRecord[] { r };
        }

        static void Restarted(long ticks)
        {
            _restarts++;
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
