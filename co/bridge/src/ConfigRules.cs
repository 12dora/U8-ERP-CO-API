using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Text.RegularExpressions;

namespace U8Co
{
    // config.json 各字段的校验。报错说清是哪个键、应该怎么写。
    internal static class ConfigRules
    {
        // 路径固定为 /u8co/（路由按 /u8co/v1/... 分派），端口必须写明，安装脚本据此开 urlacl 和防火墙。
        static readonly Regex PrefixPattern = new Regex(
            @"^http://(\+|\*|[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]):([0-9]{1,5})/u8co/$", RegexOptions.CultureInvariant);

        public static void CheckPrefix(string prefix)
        {
            Match m = prefix == null ? Match.Empty : PrefixPattern.Match(prefix);
            if (!m.Success || !PortOk(m.Groups[2].Value))
            {
                throw new InvalidOperationException(
                    "listenPrefix 必须形如 http://+:18089/u8co/（只支持 http，端口 1 到 65535，路径固定为 /u8co/）");
            }
        }

        static bool PortOk(string text)
        {
            int port;
            if (!int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out port))
            {
                return false;
            }
            return port >= 1 && port <= 65535;
        }

        // U8 应用服务器：原样交给 U8Login.Login 的服务器参数。只挡明显写错的值。
        public static void CheckServer(string server)
        {
            if (server == null || server.Length == 0 || server.Length > 255)
            {
                throw new InvalidOperationException("u8Server 不能为空：填 U8 应用服务器的计算机名或 IP");
            }
            for (int i = 0; i < server.Length; i++)
            {
                char c = server[i];
                if (char.IsWhiteSpace(c) || char.IsControl(c) || "\"';<>&|".IndexOf(c) >= 0)
                {
                    throw new InvalidOperationException("u8Server 无效：不能含空白、引号、分号等字符");
                }
            }
        }

        // 账套白名单。空数组表示一个账套都不放行。
        public static void CheckAccounts(string[] accounts)
        {
            CheckCodes(accounts, "allowedAccounts");
        }

        // 测试账套名单（月末结账只对它们开放）。空数组表示一个都不开放。
        public static void CheckTestAccounts(string[] accounts)
        {
            CheckCodes(accounts, "testAccounts");
        }

        // 只读账套名单（ReadOnlyGate）。空数组表示没有只读账套。
        public static void CheckReadOnlyAccounts(string[] accounts)
        {
            CheckCodes(accounts, "readOnlyAccounts");
        }

        static void CheckCodes(string[] accounts, string key)
        {
            for (int i = 0; i < accounts.Length; i++)
            {
                if (!ThreeDigits(accounts[i]))
                {
                    throw new InvalidOperationException(key + " 的每一项必须是 3 位数字的账套号：" + accounts[i]);
                }
            }
        }

        // 权限评估名单（PermEvaluate）：可以查询其他操作员权限的 U8 操作员编码。空数组表示一个都不放行。
        public static void CheckPermEvaluateOperators(string[] operators)
        {
            for (int i = 0; i < operators.Length; i++)
            {
                if (!OperatorCode(operators[i]))
                {
                    throw new InvalidOperationException("permEvaluateOperators 的每一项必须是有效的 U8 操作员编码（1 到 20 个字符，不含空白、单引号、分号）：" + operators[i]);
                }
            }
        }

        // U8 操作员编码的格式，同请求的 operator（Requests.CheckOperator）。
        public static bool OperatorCode(string code)
        {
            if (code == null || code.Length == 0 || code.Length > 20)
            {
                return false;
            }
            for (int i = 0; i < code.Length; i++)
            {
                if (code[i] <= ' ' || code[i] == '\'' || code[i] == ';')
                {
                    return false;
                }
            }
            return true;
        }

        // 来源 IP 白名单，与请求的远端地址逐字比较。空数组表示拒绝所有来源。
        public static void CheckClients(string[] clients)
        {
            for (int i = 0; i < clients.Length; i++)
            {
                IPAddress ip;
                string item = clients[i];
                if (item == null || !IPAddress.TryParse(item, out ip) || !string.Equals(ip.ToString(), item, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException(
                        "allowedClients 只能写单个 IP 地址的规范写法（不支持网段、主机名）：" + item);
                }
            }
        }

        // U8 安装目录不能和运行目录互相包含：桥只在运行目录里写文件，只在 U8 目录里读文件。
        public static string CheckU8Home(string dir)
        {
            string full = Paths.CheckDir(dir, "u8Home");
            if (Paths.Overlaps(full, Paths.Root))
            {
                throw new InvalidOperationException("u8Home 与运行目录 " + Paths.Root + " 不能相同或互相包含");
            }
            return full;
        }

        // 配置合法但什么都不会放行时给出提示，不当成错误。
        public static string[] Warnings(BridgeConfig cfg)
        {
            List<string> list = new List<string>();
            if (cfg.AllowedAccounts == null || cfg.AllowedAccounts.Length == 0)
            {
                list.Add("allowedAccounts 为空：所有账套的请求都会被拒绝");
            }
            if (cfg.AllowedClients == null || cfg.AllowedClients.Length == 0)
            {
                list.Add("allowedClients 为空：所有来源的请求都会被拒绝");
            }
            AddTestAccountWarnings(cfg, list);
            if (cfg.U8Home == null || !Directory.Exists(cfg.U8Home))
            {
                list.Add("u8Home 目录不存在：" + cfg.U8Home + "，生产订单审核和档案读写会失败");
            }
            else if (cfg.MobilePush && !Directory.Exists(Path.Combine(cfg.U8Home, @"U8AuditWebSite\bin")))
            {
                list.Add("mobilePush 已开启，但 u8Home 下没有 U8AuditWebSite\\bin：移动审批推送不会生效");
            }
            return list.ToArray();
        }

        // testAccounts 相关的警告：名单里有不在 allowedAccounts 里的账套；名单不为空而第二级写入总开关未打开。
        static void AddTestAccountWarnings(BridgeConfig cfg, List<string> list)
        {
            string stray = StrayTestAccount(cfg);
            if (stray != null)
            {
                list.Add("testAccounts 里的 " + stray + " 不在 allowedAccounts 里：这个账套的请求仍会被拒绝");
            }
            if (ReplicatedOff(cfg))
            {
                list.Add(ReplicatedOffWarning);
            }
        }

        // 配了 testAccounts 却没打开 enableReplicatedWrites：升级前只靠测试账套名单开放的部署会在升级后静默关闭第二级写入。
        internal const string ReplicatedOffWarning = "testAccounts 不为空，但 enableReplicatedWrites 未设为 true：第二级写入（月末结账、存货核算、期初等）"
            + "一律 403 feature_disabled，设置该开关并重启服务后才对 testAccounts 开放";

        internal static bool ReplicatedOff(BridgeConfig cfg)
        {
            return cfg.TestAccounts != null && cfg.TestAccounts.Length > 0 && !cfg.EnableReplicatedWrites;
        }

        // testAccounts 里第一个不在 allowedAccounts 里的账套；都在时返回 null。
        static string StrayTestAccount(BridgeConfig cfg)
        {
            string[] tests = cfg.TestAccounts ?? new string[0];
            for (int i = 0; i < tests.Length; i++)
            {
                if (!BridgeConfig.AllowsAccount(cfg, tests[i]))
                {
                    return tests[i];
                }
            }
            return null;
        }

        // 可选的 true/false 键；写成字符串、数字都拒绝。
        public static bool OptionalBool(Dictionary<string, object> map, string key, bool fallback)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return fallback;
            }
            if (!(raw is bool))
            {
                throw new InvalidOperationException(key + " 必须是 true 或 false");
            }
            return (bool)raw;
        }

        // licenseLimits：可选对象 {"SA":12,...}，键是两位大写字母的子系统号，值是 1 到 9999 的整数。
        public static Dictionary<string, int> LicenseLimits(Dictionary<string, object> map, string key)
        {
            Dictionary<string, int> limits = new Dictionary<string, int>(StringComparer.Ordinal);
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return limits;
            }
            Dictionary<string, object> obj = raw as Dictionary<string, object>;
            if (obj == null)
            {
                throw new InvalidOperationException(key + " 必须是对象，形如 {\"SA\":12}");
            }
            foreach (KeyValuePair<string, object> pair in obj)
            {
                if (!SubCode(pair.Key))
                {
                    throw new InvalidOperationException(key + " 的键必须是两位大写字母的子系统号：" + pair.Key);
                }
                if (!(pair.Value is int) || (int)pair.Value < 1 || (int)pair.Value > 9999)
                {
                    throw new InvalidOperationException(key + "." + pair.Key + " 必须是 1 到 9999 的整数");
                }
                limits[pair.Key] = (int)pair.Value;
            }
            return limits;
        }

        // 两位大写字母（SA、PU、GL……）。
        public static bool SubCode(string text)
        {
            return text != null && text.Length == 2 && text[0] >= 'A' && text[0] <= 'Z' && text[1] >= 'A' && text[1] <= 'Z';
        }

        static bool ThreeDigits(string text)
        {
            if (text == null || text.Length != 3)
            {
                return false;
            }
            for (int i = 0; i < 3; i++)
            {
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }
    }
}
