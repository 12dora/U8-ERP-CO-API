using System;

namespace U8Co
{
    // --selftest 的「登录状态不是 0」自愈里 SCM 失败链和停止过程的部分（由 WedgedLoginSelfTest.Run 调用）：
    // FailChain 的链长与「留一次重启给看门狗卡死」、失败动作全部为重启时不按次数封顶（仍受 30 分钟限频）、
    // 现装的「重启、重启、无操作」照旧留一次、失败动作分类（WedgedRestart.Classify）、--check-config 的失败动作说明、
    // 结束进程时记不记失败链（StopExit：只有本桥自己报已停止成功才不记，其余偏保守照记）及其先后顺序。不起线程、不建 COM、不连库、不碰 SCM、不写文件。
    internal static class WedgedChainSelfTest
    {
        const string Op = "OP001";
        const long Minute = 60L * TimeSpan.TicksPerSecond;
        const long Hour = 60L * Minute;
        const long Day = 24L * Hour;
        static readonly long T0 = new DateTime(2026, 10, 30, 1, 0, 0, DateTimeKind.Utc).Ticks;
        static int _reports;

        public static void Run()
        {
            CheckChain();
            CheckUnlimited();
            CheckLabels();
            CheckClassify();
            CheckDecide();
            CheckInterleavings();
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

        // 失败动作全部为重启：限次策略要留给看门狗的那条链照样重启；现装的策略不变，仍不重启。
        static void CheckUnlimited()
        {
            long[] two = new long[] { T0 - 3 * Hour, T0 - 2 * Hour };
            Expect("unlimited limited reserve", !FailChain.Allows(two, T0, Limited3()));
            Expect("unlimited allows", FailChain.Allows(two, T0, Unlimited3()));
            Expect("unlimited single", FailChain.Allows(two, T0, new ScmPolicy(1, Day, true)));
            Expect("unlimited needs restart", !new ScmPolicy(0, Day, true).Unlimited);
            Expect("unlimited null", !FailChain.Allows(two, T0, null));
            Expect("unlimited install", !Install().Unlimited && !Limited3().Unlimited);
            int n;
            WedgedState limited = new WedgedState(1);
            limited.SeedFailures(Recs(T0 - 3 * Hour, T0 - 2 * Hour));
            limited.Strike(Op, T0, Limited3, out n);
            Expect("unlimited strike limited", limited.Strike(Op, T0 + Minute, Limited3, out n) == WedgedAction.RestartReserve);
            WedgedState all = new WedgedState(1);
            all.SeedFailures(Recs(T0 - 3 * Hour, T0 - 2 * Hour));
            all.Strike(Op, T0, Unlimited3, out n);
            Expect("unlimited strike restart", all.Strike(Op, T0 + Minute, Unlimited3, out n) == WedgedAction.Restart);
            // 不按次数封顶，但距上一次结束进程不到 30 分钟仍限频。
            WedgedState gap = new WedgedState(1);
            gap.SeedFailures(Recs(T0 - 10 * Minute));
            gap.Strike(Op, T0, Unlimited3, out n);
            Expect("unlimited still gapped", gap.Strike(Op, T0 + Minute, Unlimited3, out n) == WedgedAction.RestartLimited);
        }

        // --check-config：全部为重启写明不封顶（仍间隔 30 分钟），不再说留一次；限次的照旧。
        static void CheckLabels()
        {
            string all = WedgedRestart.ScmLabel(Unlimited3());
            Expect("label unlimited", all.Contains("全部为重启，第二步不按失败次数封顶（仍间隔 30 分钟）") && !all.Contains("留一次"));
            Expect("label limited", WedgedRestart.ScmLabel(Install()).Contains("留一次")
                && !WedgedRestart.ScmLabel(Install()).Contains("全部为重启"));
            BridgeConfig cfg = new BridgeConfig();
            string[] described = WedgedRules.Describe(cfg, "u8co", Unlimited3());
            Expect("describe unlimited", described[1].Contains("全部为重启") && !described[1].Contains("留一次"));
            Expect("describe limited", WedgedRules.Describe(cfg, "u8co", Install())[1].Contains("留一次"));
            Expect("describe unknown", WedgedRules.Describe(cfg, "u8co", null)[1].Contains("留一次"));
        }

        // 失败动作分类（WedgedRestart.Classify，ReadActions 用的同一个函数）：1 为重启，0 为无操作。
        static void CheckClassify()
        {
            Expect("classify none", Shape(WedgedRestart.Classify(new int[0], 86400), 0, false));
            Expect("classify null", Shape(WedgedRestart.Classify(null, 86400), 0, false));
            Expect("classify none policy", WedgedRestart.Policy(true, null, WedgedRestart.Classify(new int[0], 86400)) == null);
            Expect("classify single", Shape(WedgedRestart.Classify(new int[] { 1 }, 86400), 1, true));
            Expect("classify all", Shape(WedgedRestart.Classify(new int[] { 1, 1, 1 }, 86400), 3, true));
            ScmPolicy installed = WedgedRestart.Classify(new int[] { 1, 1, 0 }, 86400);
            Expect("classify trailing none", Shape(installed, 2, false));
            Expect("classify trailing reserve", !FailChain.Allows(new long[] { T0 - 2 * Hour }, T0, installed));
            Expect("classify gap", Shape(WedgedRestart.Classify(new int[] { 1, 0, 1 }, 86400), 1, false));
            Expect("classify reset", installed.ResetTicks == Day);
            Expect("classify infinite", WedgedRestart.Classify(new int[] { 1 }, -1).ResetTicks == 0);
        }

        static bool Shape(ScmPolicy p, int restarts, bool unlimited)
        {
            return p != null && p.Restarts == restarts && p.Unlimited == unlimited;
        }

        // 结束进程的决定（StopExit.Decide）：只有本桥自己报已停止成功过才不记；在 OnStop 里先报已停止；其余照记（偏保守）。
        static void CheckDecide()
        {
            Expect("decide idle", StopExit.Decide(false, false) == StopExit.Record);
            Expect("decide in stop", StopExit.Decide(true, false) == StopExit.ReportStopped);
            Expect("decide own reported", StopExit.Decide(true, true) == StopExit.Skip);
            Expect("decide own reported after", StopExit.Decide(false, true) == StopExit.Skip);
        }

        // 按顺序驱动 StopExit。看门狗、自愈重启、停止兜底和崩溃都走 StopGuard.Exit 这一条路径：锁里决定、锁里 FailFast，
        // 所以「先记了崩溃、再有人报已停止干净退出」不会发生（记账之后进程在锁里结束），只剩下面这些先后。
        static void CheckInterleavings()
        {
            _reports = 0;
            // 平时结束进程（看门狗先决定、停止请求后到，Arm 等锁时进程已结束）：照记，不报。
            StopExit s = new StopExit();
            Expect("order idle", s.Exit(ReportOk) && _reports == 0);
            // 先 Arm、再结束进程（看门狗、停止兜底或崩溃，同一条规则）：自己报已停止成功，不记。
            s = new StopExit();
            s.Arm();
            Expect("order in stop", !s.Exit(ReportOk) && s.OwnReported && _reports == 1);
            // OnStop 已返回、ServiceBase 还没真正报已停止时结束进程：确认不了，照记，也不重复上报。
            s = new StopExit();
            s.Arm();
            s.Returned();
            Expect("order after return", s.Exit(ReportOk) && !s.OwnReported && _reports == 1);
            // 自己报已停止失败：SCM 会计失败，照记。
            s = new StopExit();
            s.Arm();
            Expect("order report failed", s.Exit(ReportFail) && !s.OwnReported && _reports == 2);
            // 没有服务对象可报（report 为 null）时同样照记。
            s = new StopExit();
            s.Arm();
            Expect("order no report", s.Exit(null) && !s.OwnReported);
        }

        static bool ReportOk()
        {
            _reports++;
            return true;
        }

        static bool ReportFail()
        {
            _reports++;
            return false;
        }

        // 现装的：开头 2 条重启、24 小时重置；开头 3 条重启后有非重启条；3 条全部为重启。
        static ScmPolicy Install()
        {
            return new ScmPolicy(2, Day);
        }

        static ScmPolicy Limited3()
        {
            return new ScmPolicy(3, Day);
        }

        static ScmPolicy Unlimited3()
        {
            return new ScmPolicy(3, Day, true);
        }

        // 每个时刻一条看门狗记录（Wedged 为假）。
        static FailRecord[] Recs(params long[] ticks)
        {
            FailRecord[] list = new FailRecord[ticks.Length];
            for (int i = 0; i < ticks.Length; i++)
            {
                list[i] = new FailRecord();
                list[i].Ticks = ticks[i];
            }
            return list;
        }

        static FailRecord[] Recs(bool wedged, long ticks)
        {
            FailRecord[] list = Recs(ticks);
            list[0].Wedged = wedged;
            return list;
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
