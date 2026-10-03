using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 许可相关的离线自检：配置键的范围与形状、加密服务器名的读取、状态判定、产品包输出、重试分类。不连 U8、不连库。
    internal static class LicenseSelfTest
    {
        public static void Run()
        {
            CheckDefaults();
            CheckBadConfig();
            CheckStates();
            CheckRetry();
            CheckServer();
            CheckRank();
            CheckPackView();
            CheckLeasesLabel();
            // 读取实现的自检在私有部分里；不在时核对回落。
            LicenseHooks.SelfTest();
            LicenseStateSelfTest.Run();
        }

        static void CheckDefaults()
        {
            BridgeConfig cfg = new BridgeConfig();
            BridgeConfig.LoadLicense(cfg, Map("{\"licenseLimits\":{\"SA\":12,\"GL\":5}}"));
            if (cfg.LicenseRetries != 2 || cfg.LicenseSampleMinutes != 5 || cfg.LicenseWarnFree != 1
                || cfg.LicenseLimits.Count != 2 || cfg.LicenseLimits["SA"] != 12 || cfg.CleanOrphanTasks)
            {
                throw new InvalidOperationException("license defaults");
            }
            BridgeConfig.CheckKeys(Map("{\"licenseRetries\":0,\"licenseSampleMinutes\":0,\"licenseWarnFree\":50,"
                + "\"licenseLimits\":{},\"cleanOrphanTasks\":true}"));
        }

        static void CheckBadConfig()
        {
            Bad("retries range", "{\"licenseRetries\":6}");
            Bad("retries type", "{\"licenseRetries\":\"2\"}");
            Bad("sample range", "{\"licenseSampleMinutes\":61}");
            Bad("warn range", "{\"licenseWarnFree\":-1}");
            Bad("limits array", "{\"licenseLimits\":[12]}");
            Bad("limits key", "{\"licenseLimits\":{\"sa\":12}}");
            Bad("limits key len", "{\"licenseLimits\":{\"SAX\":12}}");
            Bad("limits zero", "{\"licenseLimits\":{\"SA\":0}}");
            Bad("limits big", "{\"licenseLimits\":{\"SA\":10000}}");
            Bad("limits text", "{\"licenseLimits\":{\"SA\":\"12\"}}");
            Expect("unknown key", delegate { BridgeConfig.CheckKeys(Map("{\"licenseRetry\":2}")); });
            Expect("orphan bool", delegate
            {
                ConfigRules.OptionalBool(Map("{\"cleanOrphanTasks\":1}"), "cleanOrphanTasks", false);
            });
        }

        static void CheckStates()
        {
            long now = DateTime.UtcNow.Ticks;
            Dictionary<string, LicenseSub> subs = new Dictionary<string, LicenseSub>(StringComparer.Ordinal);
            Same("unknown", "unknown", LicenseView.Overall(subs, false, 1, now));
            Same("sampled", "ok", LicenseView.Overall(subs, true, 1, now));
            LicenseSub sa = new LicenseSub();
            sa.Used = 11;
            sa.Limit = 12;
            subs["SA"] = sa;
            Same("near", "near", LicenseView.Overall(subs, true, 1, now));
            Same("warn 0", "ok", LicenseView.Overall(subs, true, 0, now));
            LicenseSub pu = new LicenseSub();
            pu.LastFull = now - 5L * TimeSpan.TicksPerMinute;
            subs["PU"] = pu;
            Same("full", "full", LicenseView.Overall(subs, false, 1, now));
            pu.LastFull = now - 20L * TimeSpan.TicksPerMinute;
            Same("full old", "near", LicenseView.Overall(subs, false, 1, now));
        }

        static void CheckRetry()
        {
            if (!LicenseRetry.Transient(new BridgeException(503, LicenseRetry.FullCode, "x"))
                || !LicenseRetry.Transient(new BridgeException(422, "login_failed", LicenseRetry.StateMessage))
                || LicenseRetry.Transient(new BridgeException(422, "login_failed", "口令不正确")))
            {
                throw new InvalidOperationException("license transient");
            }
            if (LicenseRetry.Count(0) != 0 || LicenseRetry.Count(1) != 1 || LicenseRetry.Count(5) != 2)
            {
                throw new InvalidOperationException("license retry count");
            }
            long now = DateTime.UtcNow.Ticks;
            if (!LicenseRetry.Fits(now, 0, 1000) || LicenseRetry.Fits(now - 15L * TimeSpan.TicksPerSecond, 1, 1000)
                || LicenseRetry.Fits(now, 1, 13000))
            {
                throw new InvalidOperationException("license retry budget");
            }
        }

        static void CheckServer()
        {
            BridgeConfig cfg = new BridgeConfig();
            BridgeConfig.LoadLicense(cfg, Map("{}"));
            Same("server default", "", cfg.LicenseServer);
            BridgeConfig.LoadLicense(cfg, Map("{\"licenseServer\":\"lic-srv.example.com\"}"));
            Same("server set", "lic-srv.example.com", cfg.LicenseServer);
            BridgeConfig.CheckKeys(Map("{\"licenseServer\":\"\"}"));
            string[] bad = new string[] { "{\"licenseServer\":\"a b\"}", "{\"licenseServer\":1}",
                "{\"licenseServer\":\"a;b\"}", "{\"licenseServer\":\"" + new string('a', 81) + "\"}" };
            for (int i = 0; i < bad.Length; i++)
            {
                Bad("server bad " + i, bad[i]);
            }
            string xml = "<?xml version=\"1.0\"?><configuration><appSettings>"
                + "<add name=\"Other\" value=\"x\"/><add name=\"U8.AA.AppServerConfig.RightServerName\" value=\" LIC-SRV \"/>"
                + "</appSettings></configuration>";
            Same("bo config", "LIC-SRV", LicenseServer.FromXml(xml));
            Same("bo config none", null, LicenseServer.FromXml("<configuration/>"));
            Same("bo config bad", null, LicenseServer.FromXml(
                "<c><add name=\"U8.AA.AppServerConfig.RightServerName\" value=\"a b\"/></c>"));
        }

        // 租约来源时已用 ≥ 总数就是 full；UA_TaskLog 来源同样的数字只是 near。
        static void CheckRank()
        {
            long now = DateTime.UtcNow.Ticks;
            Dictionary<string, LicenseSub> subs = new Dictionary<string, LicenseSub>(StringComparer.Ordinal);
            LicenseSub sa = new LicenseSub();
            sa.Used = 10;
            sa.Limit = 10;
            subs["SA"] = sa;
            Same("tasklog at limit", "near", LicenseView.Overall(subs, true, 1, now));
            sa.Exact = true;
            Same("leases at limit", "full", LicenseView.Overall(subs, true, 1, now));
            sa.Used = 9;
            Same("leases near", "near", LicenseView.Overall(subs, true, 1, now));
        }

        // license_packs 只在租约来源时输出，只收两位码；UA_TaskLog 来源是空对象。
        static void CheckPackView()
        {
            Dictionary<string, LicensePack> packs = new Dictionary<string, LicensePack>(StringComparer.Ordinal);
            LicensePack hl = new LicensePack();
            hl.Used = 2;
            hl.Limit = 10;
            hl.Modules = new List<string>(new string[] { "SA", "bad", "U8" });
            packs["HL"] = hl;
            packs["bad"] = new LicensePack();
            StringBuilder buf = new StringBuilder();
            LicensePackView.Append(buf, LicenseState.SourceLeases, packs);
            Same("pack view", ",\"license_source\":\"leases\",\"license_packs\":{\"HL\":{\"used\":2,\"limit\":10,\"modules\":[\"SA\",\"U8\"]}}",
                buf.ToString());
            StringBuilder task = new StringBuilder();
            LicensePackView.Append(task, LicenseState.SourceTaskLog, packs);
            Same("tasklog packs", ",\"license_source\":\"tasklog\",\"license_packs\":{}", task.ToString());
            if (!LicensePackView.Code("A1") || LicensePackView.Code("ab") || LicensePackView.Code("ABC"))
            {
                throw new InvalidOperationException("pack code");
            }
        }

        static void CheckLeasesLabel()
        {
            Same("leases label off", "false（只用 UA_TaskLog）", LicenseRules.LeasesLabel(false, false));
            Same("leases label on", "true", LicenseRules.LeasesLabel(true, true));
            Same("leases label missing", "true（" + LicenseHooks.Missing + "，只用 UA_TaskLog）", LicenseRules.LeasesLabel(true, false));
        }

        static void Bad(string name, string json)
        {
            Expect(name, delegate
            {
                BridgeConfig.LoadLicense(new BridgeConfig(), Map(json));
            });
        }

        static void Expect(string name, Action act)
        {
            try
            {
                act();
            }
            catch (InvalidOperationException)
            {
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Same(string name, string want, string got)
        {
            if (!string.Equals(want, got, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name);
            }
        }

        static Dictionary<string, object> Map(string json)
        {
            return (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
        }
    }
}
