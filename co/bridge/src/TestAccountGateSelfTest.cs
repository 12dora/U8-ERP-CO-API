using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的第二级写入部分（TestAccountGate）：总开关 enableReplicatedWrites 的配置键、缺省值、非法取值、启动警告、健康检查片段，
    // 以及每个调用点的拒绝顺序。开关关闭（缺省）时一律 403 feature_disabled（账套在测试账套名单里也一样）；开关打开后
    // 名单为空、不在名单里 403 test_account_only，在名单里放行；没有任务或配置时按开关关闭处理。只跑纯函数。
    // Check / CheckGate 由各路由的自检调用（OpeningPostSelfTest、OpeningsArapSelfTest、StockOpeningSelfTest、ArapBadSelfTest、
    // IaSelfTest、PeriodCloseSelfTest），Run 与登记表的逐行核对由 WriteClassSelfTest 调用。
    internal static class TestAccountGateSelfTest
    {
        public static void Run()
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["enableReplicatedWrites"] = true;
            BridgeConfig.CheckKeys(map);
            Expect("replicated key on", ConfigRules.OptionalBool(map, "enableReplicatedWrites", false));
            Expect("replicated key default", !ConfigRules.OptionalBool(new Dictionary<string, object>(), "enableReplicatedWrites", false));
            Expect("replicated field default", !new BridgeConfig().EnableReplicatedWrites);
            BridgeConfig cfg = new BridgeConfig();
            cfg.EnableReplicatedWrites = true;
            try
            {
                TestAccountGate.Configure(cfg);
                Expect("replicated health on", TestAccountGate.Enabled && TestAccountGate.HealthFragment() == ",\"replicated_writes\":true");
            }
            finally
            {
                TestAccountGate.Configure(null);
            }
            Expect("replicated health off", !TestAccountGate.Enabled && TestAccountGate.HealthFragment() == ",\"replicated_writes\":false");
            Expect("replicated describe", TestAccountGate.Describe(cfg).Contains("true") && TestAccountGate.Describe(null).Contains("false"));
            CheckValues();
            CheckWarning();
        }

        // 按 config.json 的读法（JavaScriptSerializer）解析：只收 JSON 布尔，null、字符串、数字都让服务拒绝启动。
        static void CheckValues()
        {
            Expect("replicated json true", Parsed("true"));
            Expect("replicated json false", !Parsed("false"));
            string[] bad = new string[] { "null", "\"true\"", "1", "0", "\"\"" };
            for (int i = 0; i < bad.Length; i++)
            {
                bool refused = false;
                try
                {
                    Parsed(bad[i]);
                }
                catch (InvalidOperationException)
                {
                    refused = true;
                }
                Expect("replicated refuses " + bad[i], refused);
            }
        }

        static bool Parsed(string value)
        {
            object obj = new JavaScriptSerializer().DeserializeObject("{\"enableReplicatedWrites\":" + value + "}");
            Dictionary<string, object> map = (Dictionary<string, object>)obj;
            BridgeConfig.CheckKeys(map);
            return ConfigRules.OptionalBool(map, "enableReplicatedWrites", false);
        }

        // 配了测试账套却没打开开关：启动日志与 --check-config 都给一行警告；打开开关或名单为空时不警告。
        static void CheckWarning()
        {
            BridgeConfig cfg = new BridgeConfig();
            cfg.AllowedAccounts = new string[] { "998" };
            cfg.TestAccounts = new string[] { "998" };
            Expect("replicated warn off", Warned(cfg));
            cfg.EnableReplicatedWrites = true;
            Expect("replicated warn on", !Warned(cfg));
            cfg.EnableReplicatedWrites = false;
            cfg.TestAccounts = new string[0];
            Expect("replicated warn empty", !Warned(cfg));
        }

        static bool Warned(BridgeConfig cfg)
        {
            return Array.IndexOf(ConfigRules.Warnings(cfg), ConfigRules.ReplicatedOffWarning) >= 0;
        }

        // 直接调用 TestAccountGate.Require 的调用点：拒绝消息必须是 text（开关关闭时前面加「第二级写入未开启：」）。
        public static void Check(string name, string text)
        {
            CheckGate(name, text, delegate(WorkItem item) { TestAccountGate.Require(item, text); });
        }

        // 经路由自己的包装（如 IaReq.RequireTestAccount）调用：text 为 null 时不核对消息原文。
        public static void CheckGate(string name, string text, Action<WorkItem> require)
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Acc = "998";
            Expect(name + " flag off", Outcome(require, item, text) == TestAccountGate.FeatureCode);
            item.Config.TestAccounts = new string[] { "998" };
            Expect(name + " flag off listed", Outcome(require, item, text) == TestAccountGate.FeatureCode);
            item.Config.EnableReplicatedWrites = true;
            Expect(name + " test listed", Outcome(require, item, text) == null);
            item.Acc = "997";
            Expect(name + " test other", Outcome(require, item, text) == TestAccountGate.Code);
            item.Acc = "998";
            item.Config.TestAccounts = new string[0];
            Expect(name + " test empty", Outcome(require, item, text) == TestAccountGate.Code);
            Expect(name + " test null", Outcome(require, null, text) == TestAccountGate.FeatureCode);
            item.Config = null;
            Expect(name + " test null cfg", Outcome(require, item, text) == TestAccountGate.FeatureCode);
        }

        // 放行时返回 null；按约定拒绝（403、提示非空、消息与 text 一致）时返回错误码；其他情况返回 "unexpected"。
        static string Outcome(Action<WorkItem> require, WorkItem item, string text)
        {
            try
            {
                require(item);
            }
            catch (BridgeException ex)
            {
                return Conforms(ex, text) ? ex.Code : "unexpected";
            }
            return null;
        }

        static bool Conforms(BridgeException ex, string text)
        {
            if (ex.Status != 403)
            {
                return false;
            }
            if (ex.Code == TestAccountGate.FeatureCode)
            {
                return ex.Hint == TestAccountGate.FeatureHint && (text == null || ex.Message == TestAccountGate.FeaturePrefix + text);
            }
            return ex.Code == TestAccountGate.Code && ex.Hint == TestAccountGate.Hint && (text == null || ex.Message == text);
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
