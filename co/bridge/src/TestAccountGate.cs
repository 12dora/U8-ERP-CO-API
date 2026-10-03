namespace U8Co
{
    // 写入分级的第二级：U8 没有官方组件、复现 U8 界面 SQL，或改跨模块期初 / 结账状态的写入（月末结账、存货核算、期初记账、
    // 期初单据等）。两道条件依次检查（含预演）：config.json 的 enableReplicatedWrites 为 true（缺省 false，否则
    // 403 feature_disabled），且账套在 testAccounts 里（否则 403 test_account_only）。
    // 登录前（Requests 的 Apply*）和入队后（各路由的 Run 开头）各查一次。
    internal static class TestAccountGate
    {
        public const string Code = "test_account_only";
        public const string FeatureCode = "feature_disabled";
        public const string Hint = "在桥的 config.json 的 testAccounts 里加上这个账套（只加测试账套），重启服务";
        // 开关关闭时消息以此开头，再接调用点原文：账套已在测试账套名单里的调用方也先看到真正的原因。
        public const string FeaturePrefix = "第二级写入未开启：";
        public const string FeatureHint = "在桥的 config.json 设 enableReplicatedWrites 为 true 并把测试账套加入 testAccounts，重启服务；"
            + "这类写入复现 U8 界面的 SQL，只用于测试账套";
        public const string OpeningPostText = "期初记账只对配置为测试账套的账套开放";
        public const string OpeningsArapText = "期初单据只对配置为测试账套的账套开放";

        // 健康检查与 meta 用的开关值（AppHost 启动时设一次）；拦截按任务上的配置判断。
        static volatile bool _enabled;

        public static void Configure(BridgeConfig cfg)
        {
            _enabled = cfg != null && cfg.EnableReplicatedWrites;
        }

        public static bool Enabled
        {
            get { return _enabled; }
        }

        // 开关关闭（缺省）时 403 feature_disabled，消息为前缀加 text；开关打开但账套不在测试账套名单里时 403 test_account_only，消息用 text。
        public static void Require(WorkItem item, string text)
        {
            if (item == null || item.Config == null || !item.Config.EnableReplicatedWrites)
            {
                throw new BridgeException(403, FeatureCode, FeaturePrefix + text).WithHint(FeatureHint);
            }
            if (!BridgeConfig.IsTestAccount(item.Config, item.Acc))
            {
                throw new BridgeException(403, Code, text).WithHint(Hint);
            }
        }

        // 追加到健康检查 JSON 末尾（以逗号开头）：,"replicated_writes":false。
        public static string HealthFragment()
        {
            return _enabled ? ",\"replicated_writes\":true" : ",\"replicated_writes\":false";
        }

        // --check-config 的说明行。
        public static string Describe(BridgeConfig cfg)
        {
            bool on = cfg != null && cfg.EnableReplicatedWrites;
            return "enableReplicatedWrites（第二级写入总开关）: "
                + (on ? "true（仍只对 testAccounts 里的账套开放）" : "false（第二级写入一律 403 feature_disabled）");
        }
    }
}
