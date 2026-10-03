using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace U8Co
{
    internal sealed class BridgeConfig
    {
        public string ListenPrefix;
        public string U8Server;
        // 两个白名单缺省为空：不配置就什么都不放行。
        public string[] AllowedAccounts = new string[0];
        public string[] AllowedClients = new string[0];
        // 测试账套：月末结账 periods/close 只对这些账套开放。缺省为空，一个都不开放。
        public string[] TestAccounts = new string[0];
        // 第二级写入的总开关（TestAccountGate）：缺省 false，第二级写入一律 403 feature_disabled，即使账套在 testAccounts 里。
        public bool EnableReplicatedWrites;
        // 只读账套：名单里的账套只开放读取，WriteGate 的写路由一律 403 account_read_only（ReadOnlyGate）。缺省为空。
        public string[] ReadOnlyAccounts = new string[0];
        // 权限评估（perm/evaluate）：可以查询其他操作员权限的调用操作员编码。缺省为空，一个都不放行；账套主管不例外。
        public string[] PermEvaluateOperators = new string[0];
        public string AuditLog;
        public string U8Home = Paths.DefaultU8Home;
        public byte[] Secret;
        public byte[] MacKey;
        public byte[] EncKey;
        public string SqlUser;
        public string SqlPassword;
        // 线程和队列。缺省即可，config.json 不必写这些键。
        public int StaWorkers = 4;
        public int ReadWorkers = 4;
        public int QueueCap = 32;
        public int AuthCacheSeconds = 600;
        // 空白模板缓存（TplCache）。缺省开启，false 时每次现取。
        public bool TemplateCache = true;
        // 移动审批（友空间）推送。缺省关闭：审批引擎的 YonYou.U8.MA.* 程序集不加载，经本服务审批不推手机。
        public bool MobilePush;
        // U8 许可点数（LicenseState / LicenseSampler）。饱和或登录状态不是 0 时的重试次数、采样间隔（分钟，0 关闭）、
        // 「接近用满」的余量、U8 读不到许可总数时的备用总数（键为两位大写子系统号）。
        public int LicenseRetries = 2;
        public int LicenseSampleMinutes = 5;
        public int LicenseWarnFree = 1;
        public Dictionary<string, int> LicenseLimits = new Dictionary<string, int>(StringComparer.Ordinal);
        // 加密服务器（读点数租约用），缺省 "" 表示取 U8 应用服务器配置里登记的加密服务器。
        public string LicenseServer = "";
        // 是否经 U8 的许可客户端库读加密服务器的点数租约。缺省：编译进了读取实现时开启，否则关闭；false 时从不调这个库，只用 UA_TaskLog。
        // 点数租约读取的实现不在本源码树中；没有实现时（LicenseHooks.Available 为 false）写 true 也只用 UA_TaskLog。
        public bool LicenseLeases = LicenseHooks.Available();
        // 审批流终审后清掉 U8 插件在本机留下的孤儿任务行（写 UFSystem，缺省关闭）
        public bool CleanOrphanTasks;
        // 写线程池的全局写闸门（WriteGate）：经 U8 组件的写入一律串行。缺省开启，false 时只按单据锁排程。
        public bool SerializeWrites = true;
        // serializeWrites 写 "account" 时为 true：闸门键按账套（WriteGate.KeyOf），同一账套串行、不同账套并行。
        public bool SerializePerAccount;
        // 登录复用（LoginCache）：每个 STA 工作线程复用同键的 U8 登录。缺省关闭，关闭时每个任务一次登录。
        public bool LoginReuse;
        // 登录复用的停用时段（ReuseWindow）：时段内不复用。null 表示不设，复用只看 loginReuse。
        public ReuseWindow LoginReuseOffHours;
        // 存货核算脚本（ia/post、ia/period_end、存货月末结账）的命令超时（秒）。生产规模的月份要一到数分钟。
        public int IaCommandSeconds = 900;
        // 写入策略文件（WritePolicy）的完整路径，位于运行目录之下。null 表示不启用写入策略。
        public string WritePolicyFile;

        static readonly string[] ConfigKeys = new string[]
        {
            "listenPrefix", "u8Server", "allowedAccounts", "allowedClients", "auditLog", "u8Home",
            "staWorkers", "readWorkers", "queueCap", "authCacheSeconds",
            // 空白模板缓存
            "templateCache",
            // 移动审批推送
            "mobilePush",
            // 许可点数
            "licenseRetries", "licenseSampleMinutes", "licenseLimits", "licenseWarnFree", "licenseServer",
            "licenseLeases",
            // 终审后清孤儿任务行
            "cleanOrphanTasks",
            // 全局写闸门
            "serializeWrites",
            // 登录复用
            "loginReuse", "loginReuseOffHours",
            // 测试账套（月末结账）
            "testAccounts",
            // 第二级写入总开关
            "enableReplicatedWrites",
            // 只读账套
            "readOnlyAccounts",
            // 权限评估的调用操作员
            "permEvaluateOperators",
            // 存货核算脚本超时
            "iaCommandSeconds",
            // 写入策略文件
            "writePolicyFile"
        };

        static readonly string[] SqlKeys = new string[] { "user", "password" };

        // 只在进程启动时读。请求中途换文件不会半新半旧，重启才生效。
        public static BridgeConfig Load()
        {
            // 读之前核对运行目录的每一级上级（见 Paths.CheckAncestors）。
            Paths.CheckAncestors();
            Dictionary<string, object> map = ReadObject(Paths.ConfigFile);
            CheckKeys(map);
            BridgeConfig cfg = new BridgeConfig();
            LoadSite(cfg, map);
            LoadPools(cfg, map);
            cfg.TemplateCache = ConfigRules.OptionalBool(map, "templateCache", true);
            cfg.MobilePush = ConfigRules.OptionalBool(map, "mobilePush", false);
            LoadLicense(cfg, map);
            cfg.CleanOrphanTasks = ConfigRules.OptionalBool(map, "cleanOrphanTasks", false);
            WriteGate.LoadMode(cfg, map);
            cfg.LoginReuse = ConfigRules.OptionalBool(map, "loginReuse", false);
            cfg.LoginReuseOffHours = ReuseWindow.FromConfig(map);
            FileAcl.RefuseExtraReaders(Paths.SecretFile);
            cfg.Secret = ReadSecret(Paths.SecretFile);
            cfg.MacKey = Crypto.MacKey(cfg.Secret);
            cfg.EncKey = Crypto.EncKey(cfg.Secret);
            if (File.Exists(Paths.SqlFile))
            {
                FileAcl.RefuseExtraReaders(Paths.SqlFile);
            }
            LoadSql(cfg);
            return cfg;
        }

        // 站点相关的值都在这里：没有写死的服务器、账套或来源地址。
        static void LoadSite(BridgeConfig cfg, Dictionary<string, object> map)
        {
            cfg.ListenPrefix = NeedString(map, "listenPrefix");
            ConfigRules.CheckPrefix(cfg.ListenPrefix);
            cfg.U8Server = NeedString(map, "u8Server");
            ConfigRules.CheckServer(cfg.U8Server);
            cfg.AllowedAccounts = OptionalList(map, "allowedAccounts");
            ConfigRules.CheckAccounts(cfg.AllowedAccounts);
            cfg.AllowedClients = OptionalList(map, "allowedClients");
            ConfigRules.CheckClients(cfg.AllowedClients);
            cfg.TestAccounts = OptionalList(map, "testAccounts");
            ConfigRules.CheckTestAccounts(cfg.TestAccounts);
            cfg.EnableReplicatedWrites = ConfigRules.OptionalBool(map, "enableReplicatedWrites", false);
            cfg.ReadOnlyAccounts = OptionalList(map, "readOnlyAccounts");
            ConfigRules.CheckReadOnlyAccounts(cfg.ReadOnlyAccounts);
            cfg.PermEvaluateOperators = OptionalList(map, "permEvaluateOperators");
            ConfigRules.CheckPermEvaluateOperators(cfg.PermEvaluateOperators);
            cfg.AuditLog = Paths.UnderRoot(OptionalString(map, "auditLog", Paths.DefaultAuditLog));
            cfg.U8Home = ConfigRules.CheckU8Home(OptionalString(map, "u8Home", Paths.DefaultU8Home));
            cfg.WritePolicyFile = WritePolicy.ResolvePath(map);
        }

        public static bool AllowsAccount(BridgeConfig cfg, string acc)
        {
            return cfg != null && Contains(cfg.AllowedAccounts, acc);
        }

        // 测试账套名单（testAccounts）里有这个账套。
        public static bool IsTestAccount(BridgeConfig cfg, string acc)
        {
            return cfg != null && Contains(cfg.TestAccounts, acc);
        }

        // 只读账套名单（readOnlyAccounts）里有这个账套。
        public static bool IsReadOnlyAccount(BridgeConfig cfg, string acc)
        {
            return cfg != null && Contains(cfg.ReadOnlyAccounts, acc);
        }

        static bool Contains(string[] list, string acc)
        {
            if (list == null || acc == null)
            {
                return false;
            }
            for (int i = 0; i < list.Length; i++)
            {
                if (string.Equals(list[i], acc, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            return false;
        }

        static void LoadPools(BridgeConfig cfg, Dictionary<string, object> map)
        {
            cfg.StaWorkers = OptionalInt(map, "staWorkers", 4, 1, 8);
            cfg.ReadWorkers = OptionalInt(map, "readWorkers", 4, 1, 16);
            cfg.QueueCap = OptionalInt(map, "queueCap", 32, 1, 256);
            cfg.AuthCacheSeconds = OptionalInt(map, "authCacheSeconds", 600, 0, 3600);
            cfg.IaCommandSeconds = OptionalInt(map, "iaCommandSeconds", 900, 300, 7200);
        }

        // 许可相关的键。自检（LicenseSelfTest）也直接调用。
        public static void LoadLicense(BridgeConfig cfg, Dictionary<string, object> map)
        {
            cfg.LicenseRetries = OptionalInt(map, "licenseRetries", 2, 0, 5);
            cfg.LicenseSampleMinutes = OptionalInt(map, "licenseSampleMinutes", 5, 0, 60);
            cfg.LicenseWarnFree = OptionalInt(map, "licenseWarnFree", 1, 0, 50);
            cfg.LicenseLimits = ConfigRules.LicenseLimits(map, "licenseLimits");
            cfg.LicenseServer = U8Co.LicenseServer.FromConfig(map);
            cfg.LicenseLeases = ConfigRules.OptionalBool(map, "licenseLeases", LicenseHooks.Available());
        }

        // config.json 的未知键启动前就拒绝。
        public static void CheckKeys(Dictionary<string, object> map)
        {
            RejectUnknown(map, ConfigKeys, "config.json");
        }

        static int OptionalInt(Dictionary<string, object> map, string key, int fallback, int min, int max)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return fallback;
            }
            if (!(raw is int) || (int)raw < min || (int)raw > max)
            {
                throw new InvalidOperationException(key + " 必须是 " + min + " 到 " + max + " 的整数");
            }
            return (int)raw;
        }

        static void LoadSql(BridgeConfig cfg)
        {
            if (!File.Exists(Paths.SqlFile))
            {
                return;
            }
            Dictionary<string, object> map = ReadObject(Paths.SqlFile);
            RejectUnknown(map, SqlKeys, "sql.json");
            cfg.SqlUser = NeedString(map, "user");
            cfg.SqlPassword = NeedString(map, "password");
            // 拒绝 sa，避免误把特权登录写进配置。
            if (string.Equals(cfg.SqlUser, "sa", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("sql.json 禁止使用 sa");
            }
            if (cfg.SqlUser.IndexOf(';') >= 0 || cfg.SqlPassword.IndexOf(';') >= 0)
            {
                throw new InvalidOperationException("sql.json 的用户或口令含分号");
            }
        }

        static byte[] ReadSecret(string path)
        {
            if (!File.Exists(path))
            {
                throw new InvalidOperationException("缺少 secret.hex");
            }
            string text = File.ReadAllText(path, Encoding.UTF8).Trim();
            if (text.Length > 0 && text[0] == '\uFEFF')
            {
                text = text.Substring(1).Trim();
            }
            byte[] raw = Crypto.ParseHexLower(text);
            if (raw == null || raw.Length != 32)
            {
                throw new InvalidOperationException("secret.hex 必须是 64 位小写十六进制");
            }
            return raw;
        }

        static Dictionary<string, object> ReadObject(string path)
        {
            if (!File.Exists(path))
            {
                throw new InvalidOperationException("缺少 " + path);
            }
            string text = File.ReadAllText(path, Encoding.UTF8);
            object obj;
            try
            {
                obj = new JavaScriptSerializer().DeserializeObject(text);
            }
            catch (ArgumentException ex)
            {
                throw new InvalidOperationException(Path.GetFileName(path) + " 不是有效的 JSON：" + ex.Message);
            }
            Dictionary<string, object> map = obj as Dictionary<string, object>;
            if (map == null)
            {
                throw new InvalidOperationException(Path.GetFileName(path) + " 必须是 JSON 对象");
            }
            return map;
        }

        static void RejectUnknown(Dictionary<string, object> map, string[] known, string file)
        {
            foreach (string key in map.Keys)
            {
                if (!Listed(known, key))
                {
                    throw new InvalidOperationException(file + " 含未知字段 " + key);
                }
            }
        }

        static bool Listed(string[] known, string key)
        {
            for (int i = 0; i < known.Length; i++)
            {
                if (known[i] == key)
                {
                    return true;
                }
            }
            return false;
        }

        static string NeedString(Dictionary<string, object> map, string key)
        {
            if (!map.ContainsKey(key) || !(map[key] is string))
            {
                throw new InvalidOperationException("缺少字符串字段 " + key);
            }
            return (string)map[key];
        }

        static string OptionalString(Dictionary<string, object> map, string key, string fallback)
        {
            if (!map.ContainsKey(key))
            {
                return fallback;
            }
            string text = map[key] as string;
            if (text == null || text.Trim().Length == 0)
            {
                throw new InvalidOperationException(key + " 必须是非空字符串；不需要时删掉这个键");
            }
            return text;
        }

        // 缺少这个键等于空数组。
        static string[] OptionalList(Dictionary<string, object> map, string key)
        {
            if (!map.ContainsKey(key))
            {
                return new string[0];
            }
            IList list = map[key] as IList;
            if (list == null)
            {
                throw new InvalidOperationException(key + " 必须是字符串数组");
            }
            string[] values = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string item = list[i] as string;
                if (item == null)
                {
                    throw new InvalidOperationException(key + " 含非字符串");
                }
                values[i] = item;
            }
            return values;
        }
    }

    // 登录复用的停用时段（config.json 的 loginReuseOffHours）：时段内不复用登录，用完即关。
    // 写法见 docs/configuration.md「登录复用」：
    // days 是 ISO 星期（1 是星期一，7 是星期日），写成 "1-5"、"1,3,5" 或 "1-3,6"；start、end 是 HH:MM，
    // 含 start、不含 end，不支持跨午夜。按桥所在机器的本地时间算，没有 tz。
    internal sealed class ReuseWindow
    {
        public const string ConfigKey = "loginReuseOffHours";
        static readonly string[] Keys = new string[] { "days", "end", "start" };
        static readonly string[] DayNames = new string[] { "", "周一", "周二", "周三", "周四", "周五", "周六", "周日" };
        static readonly Regex DaysPattern = new Regex(
            "^[1-7](?:-[1-7])?(?:,[1-7](?:-[1-7])?)*\\z", RegexOptions.CultureInvariant);
        static readonly Regex ClockPattern = new Regex(
            "^([01][0-9]|2[0-3]):([0-5][0-9])\\z", RegexOptions.CultureInvariant);

        // 下标 1..7 为 ISO 星期；时刻是当天零点起的秒数。
        readonly bool[] _days;
        readonly int _start;
        readonly int _end;

        ReuseWindow(bool[] days, int start, int end)
        {
            _days = days;
            _start = start;
            _end = end;
        }

        // 缺少这个键或写 null：不设停用时段。写错了在加载配置时就报错。
        public static ReuseWindow FromConfig(Dictionary<string, object> map)
        {
            object raw;
            if (map == null || !map.TryGetValue(ConfigKey, out raw) || raw == null)
            {
                return null;
            }
            Dictionary<string, object> sec = raw as Dictionary<string, object>;
            if (sec == null)
            {
                throw Error("配置项 " + ConfigKey + " 必须是对象");
            }
            CheckKeys(sec);
            string daysText = Required(sec, "days");
            int start = Clock(Required(sec, "start"), "start");
            int end = Clock(Required(sec, "end"), "end");
            if (end <= start)
            {
                throw Error("配置项 " + ConfigKey + ".end 必须晚于 start（不支持跨午夜）");
            }
            return new ReuseWindow(ParseDays(daysText), start, end);
        }

        // local 按本地时间理解：星期在 days 里，且 start ≤ 时刻 < end（精确到秒）。
        public bool Contains(DateTime local)
        {
            int iso = local.DayOfWeek == DayOfWeek.Sunday ? 7 : (int)local.DayOfWeek;
            int clock = local.Hour * 3600 + local.Minute * 60 + local.Second;
            return _days[iso] && clock >= _start && clock < _end;
        }

        // 例如「周一至周五 09:00–18:00」。
        public string Label()
        {
            return DaysLabel() + " " + Hm(_start) + "–" + Hm(_end);
        }

        string DaysLabel()
        {
            List<string> parts = new List<string>();
            int d = 1;
            while (d <= 7)
            {
                if (!_days[d])
                {
                    d++;
                    continue;
                }
                int last = d;
                while (last < 7 && _days[last + 1])
                {
                    last++;
                }
                parts.Add(last == d ? DayNames[d] : DayNames[d] + (last == d + 1 ? "、" : "至") + DayNames[last]);
                d = last + 1;
            }
            return string.Join("、", parts.ToArray());
        }

        static string Hm(int seconds)
        {
            return (seconds / 3600).ToString("00", CultureInfo.InvariantCulture) + ":"
                + (seconds / 60 % 60).ToString("00", CultureInfo.InvariantCulture);
        }

        static void CheckKeys(Dictionary<string, object> sec)
        {
            List<string> extra = new List<string>();
            foreach (string key in sec.Keys)
            {
                if (Array.IndexOf(Keys, key) < 0)
                {
                    extra.Add(key);
                }
            }
            if (extra.Count > 0)
            {
                extra.Sort(StringComparer.Ordinal);
                throw Error("配置项 " + ConfigKey + " 里有不认识的键 '" + extra[0] + "'；可用的键："
                    + string.Join(", ", Keys));
            }
        }

        // 缺少、null 或空白都算缺少；不是字符串单独报错。返回去掉首尾空白的值。
        static string Required(Dictionary<string, object> sec, string key)
        {
            object raw;
            if (sec.TryGetValue(key, out raw) && raw != null && !(raw is string))
            {
                throw Error("配置项 " + ConfigKey + "." + key + " 必须是字符串");
            }
            string text = raw == null ? "" : ((string)raw).Trim();
            if (text.Length == 0)
            {
                throw Error("缺少配置项 " + ConfigKey + "." + key);
            }
            return text;
        }

        static int Clock(string text, string key)
        {
            Match m = ClockPattern.Match(text);
            if (!m.Success)
            {
                throw Error("配置项 " + ConfigKey + "." + key + " 必须是 HH:MM：'" + text + "'");
            }
            return int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture) * 3600
                + int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture) * 60;
        }

        static bool[] ParseDays(string text)
        {
            if (!DaysPattern.IsMatch(text))
            {
                throw Error("配置项 " + ConfigKey + ".days 不合法：'" + text + "'（写成 1-5 或 1,3,5）");
            }
            bool[] found = new bool[8];
            string[] parts = text.Split(',');
            for (int i = 0; i < parts.Length; i++)
            {
                string part = parts[i];
                int first = part[0] - '0';
                int last = part.Length > 1 ? part[2] - '0' : first;
                if (last < first)
                {
                    throw Error("配置项 " + ConfigKey + ".days 的范围 '" + part + "' 反了");
                }
                for (int d = first; d <= last; d++)
                {
                    found[d] = true;
                }
            }
            return found;
        }

        static InvalidOperationException Error(string message)
        {
            return new InvalidOperationException(message);
        }
    }
}
