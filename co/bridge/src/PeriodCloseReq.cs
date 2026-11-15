using System.Collections.Generic;

namespace U8Co
{
    // periods/close 的请求：module（through 时可省略）、fiscal_year、period、action（close 结账 / reopen 取消结账）、through。
    internal sealed class PeriodAsk
    {
        // 模块下标（PeriodModules）；through 且没给 module 时为 -1。
        public int Mod = -1;
        public int Year;
        public int Period;
        public bool Close;
        public bool Through;

        public string ActionText()
        {
            return Close ? "close" : "reopen";
        }
    }

    // periods/close（月末结账 / 取消结账）在登录前的校验（400）和测试账套名单（403）：不带 type、id。
    // 登录子系统按模块（through 用 GL）。处理在 PeriodClose；锁键 "period:<模块>"（through 锁全部模块），另持全局写闸门。
    internal static class PeriodCloseReq
    {
        internal const string Path = "/u8co/v1/periods/close";
        // 审计 action：period_close / period_reopen（入队前的拒绝一律记 period_close）。
        public const string Action = "period_close";
        public const string UndoAction = "period_reopen";
        public const string TestOnly = "月末结账只对配置为测试账套的账套开放";
        const string ThroughHint = "through 只能是 true 或 false，且只能和 action=close 一起用";

        public static PeriodAsk Parse(Dictionary<string, object> body)
        {
            PeriodAsk ask = new PeriodAsk();
            ask.Through = Through(Requests.Field(body, "through"));
            ask.Mod = Module(Requests.Field(body, "module"), ask.Through);
            ask.Year = Int(body, "fiscal_year", 1000, 9999, "fiscal_year 必须是 4 位年度（整数）");
            ask.Period = Int(body, "period", 1, 12, "period 必须是 1 到 12 的整数");
            string action = Requests.Field(body, "action") as string;
            if (action != "close" && action != "reopen")
            {
                throw BridgeException.BadField("action", "action 只能是 close 或 reopen");
            }
            ask.Close = action == "close";
            if (ask.Through && !ask.Close)
            {
                throw BridgeException.BadField("through", "取消结账不支持 through").WithHint(ThroughHint);
            }
            return ask;
        }

        static bool Through(object raw)
        {
            if (raw == null)
            {
                return false;
            }
            if (!(raw is bool))
            {
                throw BridgeException.BadField("through", "through 必须是 true 或 false").WithHint(ThroughHint);
            }
            return (bool)raw;
        }

        // through 时 module 可省略（给了也要合法，处理时不看）。
        static int Module(object raw, bool through)
        {
            if (raw == null && through)
            {
                return -1;
            }
            string code = raw as string;
            int mod = PeriodModules.IndexOf(code);
            if (mod < 0)
            {
                string text = raw == null ? "缺少字段 module" : code == null ? "module 必须是字符串" : "不支持的模块 " + Clip(code);
                throw BridgeException.BadField("module", text).WithHint(PeriodModules.ModulesHint);
            }
            return through ? -1 : mod;
        }

        static int Int(Dictionary<string, object> body, string key, int min, int max, string message)
        {
            object raw = Requests.Field(body, key);
            if (!(raw is int) || (int)raw < min || (int)raw > max)
            {
                throw BridgeException.BadField(key, message);
            }
            return (int)raw;
        }

        static string Clip(string text)
        {
            return text.Length > 20 ? text.Substring(0, 20) + "…" : text;
        }

        // 登录子系统：模块的子系统号；through 用 GL。
        public static string SubOf(PeriodAsk ask)
        {
            return ask.Through ? "GL" : PeriodModules.Sub(ask.Mod);
        }

        public static string AuditAction(PeriodAsk ask)
        {
            return ask.Close ? Action : UndoAction;
        }

        // 权限规则键（PermRegistryPeriod）：write:period:<模块>:close；总账取消结账另有 write:period:gl:reopen，
        // 其余模块的取消结账在 U8 里与结账同一个窗体、同一个功能 id。
        public static string RuleKey(int mod, bool close)
        {
            bool glReopen = !close && mod == PeriodModules.Gl;
            return "write:period:" + PeriodModules.Code(mod) + (glReopen ? ":reopen" : ":close");
        }

        // 同一模块的结账 / 取消结账串行；through 锁全部模块。请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            PeriodAsk ask;
            try
            {
                ask = Parse(body);
            }
            catch (BridgeException)
            {
                return new string[0];
            }
            if (!ask.Through)
            {
                return new string[] { "period:" + PeriodModules.Code(ask.Mod) };
            }
            string[] keys = new string[PeriodModules.Count];
            for (int m = 0; m < keys.Length; m++)
            {
                keys[m] = "period:" + PeriodModules.Code(m);
            }
            return keys;
        }

        // 用户决定：结账、取消结账（含预演）只对 config.json 的 testAccounts 里的账套开放，其余登录前 403。
        public static void RequireTestAccount(WorkItem item)
        {
            TestAccountGate.Require(item, TestOnly);
        }
    }
}
