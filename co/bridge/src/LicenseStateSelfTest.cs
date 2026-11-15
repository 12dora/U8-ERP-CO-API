using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 许可状态的离线自检：只看桥登录的子系统、租约过期与回落、licenseLeases 开关。
    // 会改进程里的 LicenseState，最后恢复成缺省配置。样例全是编的。由 LicenseSelfTest.Run 调用。
    internal static class LicenseStateSelfTest
    {
        public static void Run()
        {
            CheckLoginModules();
            CheckConfig();
            try
            {
                CheckForeignPack();
                CheckFallback();
                CheckStale();
            }
            finally
            {
                LicenseState.Configure(new BridgeConfig(), LicenseSampler.Modules());
                LicenseState.NoteError(null);
            }
        }

        // 各单据类型、质量、总账、应收应付的登录子系统都在 LoginModules 里。
        static void CheckLoginModules()
        {
            List<string> subs = new List<string>(new string[] { QmCo.LoginSub, ArcGlKinds.WriteSub, "AR", "AP", "AS" });
            foreach (VoucherKind kind in Kinds.All())
            {
                subs.Add(kind.SubId);
                subs.Add(kind.VerifySub);
            }
            for (int i = 0; i < subs.Count; i++)
            {
                string sub = subs[i];
                True("login module " + sub, !ConfigRules.SubCode(sub) || Array.IndexOf(LicenseSampler.LoginModules, sub) >= 0);
            }
        }

        static void CheckConfig()
        {
            BridgeConfig cfg = new BridgeConfig();
            True("leases default", cfg.LicenseLeases == LicenseHooks.Available());
            BridgeConfig.LoadLicense(cfg, Map("{}"));
            True("leases default load", cfg.LicenseLeases == LicenseHooks.Available());
            BridgeConfig.LoadLicense(cfg, Map("{\"licenseLeases\":false}"));
            True("leases off", !cfg.LicenseLeases);
            BridgeConfig.CheckKeys(Map("{\"licenseLeases\":true}"));
            bool rejected = false;
            try
            {
                BridgeConfig.LoadLicense(new BridgeConfig(), Map("{\"licenseLeases\":1}"));
            }
            catch (InvalidOperationException)
            {
                rejected = true;
            }
            True("leases type", rejected);
        }

        // 只含 CA 的包满了（桥不登录 CA）：总体仍是 ok，CA 不进 license_detail，包照样进 license_packs；
        // 桥登录的 SA 所在包满了才是 full。
        static void CheckForeignPack()
        {
            Configure();
            LicenseState.ApplyLeases(Snap(3));
            string json = LicenseView.HealthFragment();
            True("foreign pack ok", json.StartsWith(",\"license\":\"ok\"", StringComparison.Ordinal));
            True("foreign pack detail", !json.Contains("\"CA\":{\"used\""));
            True("foreign pack listed", json.Contains("\"HK\":{\"used\":3,\"limit\":3,"));
            LicenseState.ApplyLeases(Snap(10));
            True("own pack full", LicenseView.HealthFragment().StartsWith(",\"license\":\"full\"", StringComparison.Ordinal));
        }

        // 租约切回 UA_TaskLog、还没采到：unknown，不是 ok。
        static void CheckFallback()
        {
            Configure();
            LicenseState.ApplyLeases(Snap(3));
            LicenseState.UseTaskLog();
            Same("fallback unknown", "unknown", Overall());
            Same("fallback source", LicenseState.SourceTaskLog, LicenseState.Source);
        }

        // 采样间隔 5 分钟：租约数字 15 分钟内有效，过了回落到 UA_TaskLog。
        static void CheckStale()
        {
            Configure();
            LicenseState.ApplyLeases(Snap(3));
            long now = DateTime.UtcNow.Ticks;
            LicenseState.ExpireStale(now + 14L * TimeSpan.TicksPerMinute);
            Same("lease fresh", LicenseState.SourceLeases, LicenseState.Source);
            LicenseState.ExpireStale(now + 16L * TimeSpan.TicksPerMinute);
            Same("lease stale", LicenseState.SourceTaskLog, LicenseState.Source);
            Same("lease stale unknown", "unknown", Overall());
        }

        static void Configure()
        {
            BridgeConfig cfg = new BridgeConfig();
            cfg.LicenseSampleMinutes = 5;
            LicenseState.Configure(cfg, LicenseSampler.Modules());
        }

        static string Overall()
        {
            bool sampled;
            Dictionary<string, LicenseSub> subs = LicenseState.Snapshot(out sampled);
            return LicenseView.Overall(subs, sampled, 1, DateTime.UtcNow.Ticks);
        }

        // HK 只含 CA（3 点全占）；HL 含 SA、PU，占 hlUsed / 10。
        static LicenseLeaseSnapshot Snap(int hlUsed)
        {
            LicenseLeaseSnapshot snap = new LicenseLeaseSnapshot();
            snap.Packs["HK"] = Pack(3, 3, new string[] { "CA" });
            snap.Packs["HL"] = Pack(hlUsed, 10, new string[] { "SA", "PU" });
            snap.Modules["CA"] = new int[] { 3, 3 };
            snap.Modules["SA"] = new int[] { hlUsed, 10 };
            snap.Modules["PU"] = new int[] { hlUsed, 10 };
            return snap;
        }

        static LicensePack Pack(int used, int limit, string[] modules)
        {
            LicensePack pack = new LicensePack();
            pack.Used = used;
            pack.Limit = limit;
            pack.Modules = new List<string>(modules);
            return pack;
        }

        static void True(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }

        static void Same(string name, string want, string got)
        {
            True(name, string.Equals(want, got, StringComparison.Ordinal));
        }

        static Dictionary<string, object> Map(string json)
        {
            return (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
        }
    }
}
