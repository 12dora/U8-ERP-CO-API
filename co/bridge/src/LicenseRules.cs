using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // --check-config 打印许可相关的键（含 licenseLeases；licenseServer 未配置时打印从应用服务器配置读到的地址）、cleanOrphanTasks、serializeWrites 和 loginReuse（含停用时段 loginReuseOffHours）的实际取值（含缺省），
    // 以及写入策略 license.maxConcurrentLogins 与 staWorkers、loginReuse 搭配不当的警告。
    internal static class LicenseRules
    {
        public static string[] Describe(BridgeConfig cfg)
        {
            List<string> lines = new List<string>();
            lines.Add("licenseRetries（许可饱和时的登录重试）: " + Num(cfg.LicenseRetries)
                + "，实际最多 " + Num(LicenseRetry.Count(cfg.LicenseRetries)) + " 次");
            lines.Add("licenseSampleMinutes（许可采样间隔，分钟）: " + Num(cfg.LicenseSampleMinutes)
                + (cfg.LicenseSampleMinutes == 0 ? "（不采样）" : ""));
            lines.Add("licenseWarnFree（剩余点数不超过此数算接近用满）: " + Num(cfg.LicenseWarnFree));
            lines.Add("licenseLimits（读不到 U8 许可总数时的备用值）: " + Limits(cfg.LicenseLimits));
            lines.Add("licenseServer（加密服务器，读点数租约）: " + Server(cfg));
            lines.Add("licenseLeases（经 U8 许可客户端库读点数租约）: " + LeasesLabel(cfg.LicenseLeases, LicenseHooks.Available()));
            lines.Add("cleanOrphanTasks（终审后清孤儿任务行，写 UFSystem）: " + (cfg.CleanOrphanTasks ? "true" : "false"));
            lines.Add("serializeWrites（写闸门）: " + WriteGate.ModeLabel(cfg));
            lines.Add("loginReuse（登录复用）: " + Reuse(cfg));
            PolicyLicense lic = WritePolicy.LicenseOf(cfg);
            string[] warn = LoginWarnings(lic == null ? 0 : lic.MaxConcurrentLogins, cfg.LoginReuse, cfg.StaWorkers);
            for (int i = 0; i < warn.Length; i++)
            {
                lines.Add("警告: " + warn[i]);
            }
            return lines.ToArray();
        }

        // 写入策略 license.maxConcurrentLogins（0 不限）与本配置的搭配：太小会让正常请求一直 503 u8_license_hold。
        // 开着 loginReuse 时每个写线程都可能留着缓存登录，上限不超过 staWorkers 时容易被闲置登录占满。
        internal static string[] LoginWarnings(int maxLogins, bool reuse, int staWorkers)
        {
            List<string> warn = new List<string>();
            if (maxLogins <= 0)
            {
                return warn.ToArray();
            }
            if (maxLogins < 2)
            {
                warn.Add("写入策略 license.maxConcurrentLogins 为 " + Num(maxLogins)
                    + "，小于 2：读写并发时请求会频繁 503 u8_license_hold，建议至少 2");
            }
            if (reuse && maxLogins <= staWorkers)
            {
                warn.Add("写入策略 license.maxConcurrentLogins（" + Num(maxLogins) + "）不大于 staWorkers（" + Num(staWorkers)
                    + "），又开着 loginReuse：各写线程缓存的闲置登录会占满名额，建议大于 staWorkers 或关闭 loginReuse");
            }
            return warn.ToArray();
        }

        // 开着且设了停用时段时带上时段，例如「开（工作时间 周一至周五 09:00–18:00 关闭）」；关着时停用时段不起作用。
        static string Reuse(BridgeConfig cfg)
        {
            if (!cfg.LoginReuse)
            {
                return cfg.LoginReuseOffHours == null ? "关" : "关（loginReuseOffHours 不起作用）";
            }
            if (cfg.LoginReuseOffHours == null)
            {
                return "开";
            }
            string now = cfg.LoginReuseOffHours.Contains(DateTime.Now) ? "在" : "不在";
            return "开（工作时间 " + cfg.LoginReuseOffHours.Label() + " 关闭；按本机时区 " + TimeZoneInfo.Local.Id + "，此刻" + now + "关闭时段内）";
        }

        // 打开了但此版本不含租约读取时写明原因，采样照样只用 UA_TaskLog。
        internal static string LeasesLabel(bool enabled, bool available)
        {
            if (!enabled)
            {
                return "false（只用 UA_TaskLog）";
            }
            return available ? "true" : "true（" + LicenseHooks.Missing + "，只用 UA_TaskLog）";
        }

        static string Server(BridgeConfig cfg)
        {
            if (!string.IsNullOrEmpty(cfg.LicenseServer))
            {
                return cfg.LicenseServer;
            }
            string found = LicenseServer.TryFromBoConfig(cfg.U8Home);
            return "未配置，取应用服务器配置 AppServer\\UFSoft.U8.Framework.Login.BO.config：" + (found ?? "没找到");
        }

        static string Limits(Dictionary<string, int> limits)
        {
            if (limits == null || limits.Count == 0)
            {
                return "未配置";
            }
            List<string> keys = new List<string>(limits.Keys);
            keys.Sort(StringComparer.Ordinal);
            List<string> parts = new List<string>();
            for (int i = 0; i < keys.Count; i++)
            {
                parts.Add(keys[i] + ":" + Num(limits[keys[i]]));
            }
            return string.Join(",", parts.ToArray());
        }

        static string Num(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
