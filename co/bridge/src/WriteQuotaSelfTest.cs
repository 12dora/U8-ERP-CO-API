using System;
using System.Collections.Generic;
using System.IO;

namespace U8Co
{
    // --selftest 的写入限额（WriteQuota、QuotaBook）与许可保护（LicenseHold）部分。
    // 不连库、不建 COM；日计数文件写在系统临时目录下的一次性目录里，跑完删掉。
    internal static class WriteQuotaSelfTest
    {
        const string Acc = "801";
        static DateTime _now;

        public static void Run()
        {
            string dir = Path.Combine(Path.GetTempPath(), "u8co-quota-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                Hook();
                CheckMinute(dir);
                CheckDay(dir);
                CheckPersist(dir);
                CheckSkips(dir);
                CheckUnknown(dir);
                CheckBook();
                CheckLicense();
                CheckLoginRelief();
                CheckLoginWarnings();
                CheckFreeCodes();
            }
            finally
            {
                Unhook();
                try
                {
                    Directory.Delete(dir, true);
                }
                catch (Exception)
                {
                    // 临时目录删不掉不算失败。
                }
            }
        }

        static int[] _limits = new int[] { 0, 0 };

        static void Hook()
        {
            _now = new DateTime(2026, 10, 3, 10, 0, 0, DateTimeKind.Local);
            WriteQuota.Now = delegate { return _now; };
            WriteQuota.Limits = delegate(string acc) { return acc == Acc ? _limits : new int[] { 0, 0 }; };
            WriteQuota.Active = delegate { return true; };
            WriteQuota.ResetForTest();
        }

        static void Unhook()
        {
            WriteQuota.ResetForTest();
            WriteQuota.Now = null;
            WriteQuota.Limits = null;
            WriteQuota.Active = null;
            LicenseHold.LimitsHook = null;
            LicenseHold.StateHook = null;
            LicenseHold.ResetForTest();
            LoginCache.Opener = null;
        }

        // 每分钟 2 次：两次提交后第三次 429，等待到最早一次滑出窗口；失败的写入退回名额，不计数。
        static void CheckMinute(string dir)
        {
            Fresh(dir);
            _limits = new int[] { 2, 0 };
            Expect("minute 1", Write(dir, false, 200) == null);
            _now = _now.AddSeconds(10);
            Expect("minute fail released", Write(dir, false, 409) == null);
            Expect("minute 2", Write(dir, false, 200) == null);
            BridgeException ex = Write(dir, false, 200);
            Expect("minute full", ex != null && ex.Status == 429 && ex.Code == WriteQuota.Code && ex.Message == WriteQuota.Message);
            Expect("minute wait", WaitOf(ex) == 50);
            Expect("minute dry run refused too", Write(dir, true, 200) != null);
            _now = _now.AddSeconds(51);
            Expect("minute slid", Write(dir, false, 200) == null);
            Expect("minute day count", WriteQuota.DayCount(Acc) == 3);
        }

        // 每天 3 次：第四次 429，等待到本地零点；过了零点清零。
        static void CheckDay(string dir)
        {
            Fresh(dir);
            _now = new DateTime(2026, 10, 3, 23, 50, 0, DateTimeKind.Local);
            _limits = new int[] { 0, 3 };
            for (int i = 0; i < 3; i++)
            {
                Expect("day " + i, Write(dir, false, 201) == null);
            }
            BridgeException ex = Write(dir, false, 200);
            Expect("day full", ex != null && ex.Status == 429);
            Expect("day wait", WaitOf(ex) == 600);
            _now = _now.AddMinutes(11);
            Expect("day rolled", Write(dir, false, 200) == null && WriteQuota.DayCount(Acc) == 1);
        }

        // 日计数落盘：丢掉内存后按文件接着算；不是当天的文件不计；坏文件从零开始。
        static void CheckPersist(string dir)
        {
            Fresh(dir);
            _now = new DateTime(2026, 10, 4, 9, 0, 0, DateTimeKind.Local);
            _limits = new int[] { 0, 2 };
            Expect("persist 1", Write(dir, false, 200) == null);
            WriteQuota.ResetForTest();
            Expect("persist 2", Write(dir, false, 200) == null);
            WriteQuota.ResetForTest();
            Expect("persist kept", Write(dir, false, 200) != null && WriteQuota.DayCount(Acc) == 2);
            WriteQuota.ResetForTest();
            _now = _now.AddDays(1);
            Expect("persist other day", Write(dir, false, 200) == null);
            File.WriteAllText(Path.Combine(dir, WriteQuota.FileName), "{\"date\":1}");
            WriteQuota.ResetForTest();
            Expect("persist bad file", WriteQuota.DayCount(Acc) == 0 && Write(dir, false, 200) == null);
        }

        // 读路由、策略关闭、没设限额的账套都不拦。
        static void CheckSkips(string dir)
        {
            Fresh(dir);
            _limits = new int[] { 1, 1 };
            Expect("skip first", Write(dir, false, 200) == null);
            WorkItem read = Item(dir, "/u8co/v1/vouchers/load", false);
            WriteQuota.Require(read);
            WriteQuota.Active = delegate { return false; };
            Expect("skip off", Write(dir, false, 200) == null);
            WriteQuota.Active = delegate { return true; };
            WorkItem other = Item(dir, "/u8co/v1/vouchers/create", false);
            other.Acc = "802";
            WriteQuota.Require(other);
            WriteQuota.Settle(other);
            Expect("skip limited", Write(dir, false, 200) != null);
        }

        // 504 outcome_unknown 可能已在 U8 提交：从严计入日额度；其他 5xx 退回名额。
        static void CheckUnknown(string dir)
        {
            Fresh(dir);
            _limits = new int[] { 0, 1 };
            Expect("unknown other 504", Write(dir, false, 504, "u8_timeout") == null && WriteQuota.DayCount(Acc) == 0);
            Expect("unknown counted", Write(dir, false, 504, "outcome_unknown") == null && WriteQuota.DayCount(Acc) == 1);
            Expect("unknown limited", Write(dir, false, 200) != null);
        }

        // 在途名额也占额度；Render / Parse 往返。
        static void CheckBook()
        {
            QuotaBook book = new QuotaBook();
            DateTime now = new DateTime(2026, 10, 3, 12, 0, 0, DateTimeKind.Local);
            book.Reserve(Acc);
            Expect("book pending", book.WaitSeconds(Acc, 1, 0, now) == 5 && book.WaitSeconds(Acc, 2, 0, now) == 0);
            book.Release(Acc);
            Expect("book released", book.WaitSeconds(Acc, 1, 1, now) == 0);
            book.Commit(Acc, now);
            QuotaBook copy = new QuotaBook();
            copy.Parse(book.Render());
            Expect("book roundtrip", copy.DayOf(Acc, now) == 1 && copy.DayOf(Acc, now.AddDays(1)) == 0);
        }

        static void CheckLicense()
        {
            HoldLimits lim = new HoldLimits();
            lim.MaxLogins = 1;
            lim.HoldWhen = new string[] { "near", "full" };
            Expect("hold off", LicenseHold.Decide(true, null, "full", 9) == null);
            BridgeException full = LicenseHold.Decide(true, lim, "near", 0);
            Expect("hold write near", full != null && full.Status == 503 && full.Code == LicenseRetry.FullCode);
            Expect("hold read near", LicenseHold.Decide(false, lim, "near", 0) == null);
            Expect("hold write ok", LicenseHold.Decide(true, lim, "ok", 0) == null);
            BridgeException cap = LicenseHold.Decide(false, lim, null, 1);
            Expect("hold cap", cap != null && cap.Status == 503 && cap.Code == LicenseHold.HoldCode && cap.Message == LicenseHold.HoldMessage);
            lim.MaxLogins = 0;
            Expect("hold no cap", LicenseHold.Decide(false, lim, null, 50) == null);
            CheckLiveCount();
        }

        // 在用登录数：放行加一、Leave 减一；到上限拒绝且不加。
        static void CheckLiveCount()
        {
            HoldLimits lim = new HoldLimits();
            lim.MaxLogins = 2;
            lim.HoldWhen = new string[] { "full" };
            LicenseHold.LimitsHook = delegate { return lim; };
            LicenseHold.StateHook = delegate { return "full"; };
            LicenseHold.ResetForTest();
            U8Open read = new U8Open();
            U8Open write = new U8Open();
            write.Write = true;
            LicenseHold.Enter(read);
            Expect("live write held", Throws(write, LicenseRetry.FullCode) && LicenseHold.Live == 1);
            LicenseHold.Enter(read);
            Expect("live cap", Throws(read, LicenseHold.HoldCode) && LicenseHold.Live == 2);
            LicenseHold.Leave();
            LicenseHold.Leave();
            LicenseHold.Leave();
            Expect("live floor", LicenseHold.Live == 0);
            CheckReusedHold();
        }

        // 出队后的 holdWritesWhen（RequireWrite）：复用缓存登录的写任务同样暂停，读路由、状态不在名单、策略关闭都放行。
        static void CheckReusedHold()
        {
            HoldLimits lim = new HoldLimits();
            lim.HoldWhen = new string[] { "full" };
            string state = "full";
            LicenseHold.LimitsHook = delegate { return lim; };
            LicenseHold.StateHook = delegate { return state; };
            WorkItem write = Item(Path.GetTempPath(), "/u8co/v1/vouchers/create", false);
            WorkItem dry = Item(Path.GetTempPath(), "/u8co/v1/vouchers/create", true);
            WorkItem read = Item(Path.GetTempPath(), "/u8co/v1/vouchers/load", false);
            Expect("reused write held", HeldWrite(write) == LicenseRetry.FullCode && HeldWrite(dry) == LicenseRetry.FullCode);
            Expect("reused read free", HeldWrite(read) == null);
            state = "near";
            Expect("reused other state", HeldWrite(write) == null);
            LicenseHold.LimitsHook = delegate { return null; };
            state = "full";
            Expect("reused policy off", HeldWrite(write) == null);
            LicenseHold.LimitsHook = null;
            LicenseHold.StateHook = null;
        }

        static string HeldWrite(WorkItem item)
        {
            try
            {
                LicenseHold.RequireWrite(item);
                return null;
            }
            catch (BridgeException ex)
            {
                return ex.Code;
            }
        }

        // 到上限时先关本线程闲置的缓存登录再判；同一请求内的嵌套登录不占名额；本线程腾不出时请其他线程空闲时腾退。
        // 经 LoginCache.Opener 换成只过许可保护、不登录 U8 的会话（U8Session.HeldForTest）。
        static void CheckLoginRelief()
        {
            bool was = LoginCache.Enabled;
            ReuseWindow window = LoginCache.Window;
            int cap = LoginCache.CapPerWorker;
            int ttl = LoginCache.TtlSeconds;
            HoldLimits lim = new HoldLimits();
            lim.MaxLogins = 1;
            LicenseHold.LimitsHook = delegate { return lim; };
            LicenseHold.ResetForTest();
            LoginCache.Opener = delegate(WorkItem item, string sub) { return U8Session.HeldForTest(new U8Open()); };
            LoginCache.Configure(true, 4, 60, null);
            try
            {
                WorkItem a = Login("opa");
                WorkItem b = Login("opb");
                U8Session first = LoginCache.Acquire(a, "SA");
                LoginCache.Release(first, a, "SA", true);
                Expect("relief cached", LoginCache.CachedCountForThisThread() == 1 && LicenseHold.Live == 1);
                U8Session second = LoginCache.Acquire(b, "SA");
                Expect("relief dropped idle", LoginCache.CachedCountForThisThread() == 0 && LicenseHold.Live == 1);
                LicenseHold.BeginNested();
                U8Session nested;
                try
                {
                    nested = LoginCache.Acquire(Login("opc"), "SA");
                }
                finally
                {
                    LicenseHold.EndNested();
                }
                Expect("relief nested free", LicenseHold.Live == 1);
                LoginCache.Release(nested, a, "SA", false);
                Expect("relief nested release", LicenseHold.Live == 1);
                Expect("relief busy refused", ThrowsOpen(Login("opd")) && LicenseHold.Live == 1);
                LoginCache.Release(second, b, "SA", true);
                LoginCache.RequestEvict();
                LoginCache.Sweep();
                Expect("relief evicted", LoginCache.CachedCountForThisThread() == 0 && LicenseHold.Live == 0);
            }
            finally
            {
                LoginCache.Drain();
                LoginCache.Opener = null;
                LoginCache.Configure(was, cap, ttl, window);
                LicenseHold.LimitsHook = null;
                LicenseHold.ResetForTest();
            }
        }

        static WorkItem Login(string op)
        {
            WorkItem item = new WorkItem();
            item.Acc = "998";
            item.Year = "2024";
            item.Operator = op;
            item.Password = "测试Pass#01";
            item.Date = "2026-10-03";
            return item;
        }

        static bool ThrowsOpen(WorkItem item)
        {
            try
            {
                LoginCache.Acquire(item, "SA");
                return false;
            }
            catch (BridgeException ex)
            {
                return ex.Code == LicenseHold.HoldCode;
            }
        }

        // --check-config 的上限搭配警告：0 不限不警告；小于 2；开着 loginReuse 时不大于 staWorkers。
        static void CheckLoginWarnings()
        {
            Expect("warn unlimited", LicenseRules.LoginWarnings(0, true, 4).Length == 0);
            Expect("warn one", LicenseRules.LoginWarnings(1, false, 4).Length == 1);
            Expect("warn reuse", LicenseRules.LoginWarnings(4, true, 4).Length == 1);
            Expect("warn both", LicenseRules.LoginWarnings(1, true, 4).Length == 2);
            Expect("warn fine", LicenseRules.LoginWarnings(5, true, 4).Length == 0 && LicenseRules.LoginWarnings(2, false, 4).Length == 0);
        }

        static bool Throws(U8Open ask, string code)
        {
            try
            {
                LicenseHold.Enter(ask);
                return false;
            }
            catch (BridgeException ex)
            {
                return ex.Code == code;
            }
        }

        // 新错误码都不占幂等键。
        static void CheckFreeCodes()
        {
            string[] codes = new string[]
            {
                "write_policy_unavailable", "write_frozen", "write_window", "write_quota", "u8_license_hold"
            };
            for (int i = 0; i < codes.Length; i++)
            {
                Expect("free " + codes[i], IdemFlow.StateOf(503, codes[i]) == null);
            }
        }

        // 每组用例从空计数开始：删掉上一组留下的日计数文件。
        static void Fresh(string dir)
        {
            File.Delete(Path.Combine(dir, WriteQuota.FileName));
            WriteQuota.ResetForTest();
        }

        // 一次完整的写任务：出队检查、执行结果、结算。被拒时返回异常（不结算也不留名额）。
        static BridgeException Write(string dir, bool dryRun, int status)
        {
            return Write(dir, dryRun, status, "u8_rejected");
        }

        static BridgeException Write(string dir, bool dryRun, int status, string code)
        {
            WorkItem item = Item(dir, "/u8co/v1/vouchers/create", dryRun);
            try
            {
                WriteQuota.Require(item);
            }
            catch (BridgeException ex)
            {
                WriteQuota.Settle(item);
                return ex;
            }
            item.Result = status < 300
                ? ApiResult.Ok(new Dictionary<string, object>())
                : ApiResult.From(new BridgeException(status, code, "x"));
            WriteQuota.Settle(item);
            return null;
        }

        static WorkItem Item(string dir, string path, bool dryRun)
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Config.AuditLog = dir;
            item.Path = path;
            item.Acc = Acc;
            item.DryRun = dryRun;
            return item;
        }

        static int WaitOf(BridgeException ex)
        {
            object v;
            if (ex == null || ex.Detail == null || !ex.Detail.TryGetValue("retry_after_seconds", out v) || !(v is int))
            {
                return -1;
            }
            return (int)v;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("write quota " + name);
            }
        }
    }
}
