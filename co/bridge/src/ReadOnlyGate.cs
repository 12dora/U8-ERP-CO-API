using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 只读账套（config.json 的 readOnlyAccounts）：名单里的账套只开放读取。WriteGate 登记的每条写路由一律
    // 403 account_read_only，含预演与幂等重放；写入策略文件、测试账套名单都不能放行。
    // readOnlyExceptions 匹配的写入放行，审计 policy 记 read_only_exception；不匹配的仍拒绝。匹配 = 账套、操作员
    // （只忽略 ASCII 大小写，AsciiIgnoreCase）、写入分类 (archives, op) 都对上，路径恰为 archives/<op>；op=create / delete 另要求请求体
    // archive 在规则的 archives 里（create 可列 district_class、customer，delete 只有 district_class；ConfigRulesReadOnly.Eligible）。type=vouchers 的规则按登记表 ReadOnlyVoucherOps.Rows：
    // 路径恰为 vouchers/<op>、请求体 type 等于规则的 voucher（head_fix、so_contract 省略时取固定类型）、按 action 映射的写入分类 op
    // 在该行里（verify 含弃审，close 不含打开）、generate 的 source_type 须是 sale_order（可省）；其它单据类型和单据路由仍拒绝。登录前在 WriteClassGate.Pre（账套名单之后、解密之前，先于写入策略）
    // 查，入队后在 WriteClassGate.Post（登录 U8 之前）再查一次。
    // callers 规则（按调用系统放行）：账套对上、请求体 caller_trust（API 发来的已验签信任项 name，IdemReq.TrustField，已在
    // Requests 里取进 AuditDraft.Trust / WorkItem.Trust）逐字在 callers 里、路由部分同上，任何操作员都放行；不带 caller_trust
    // （直连、旧版 API）一律不命中。不从 caller（幂等与审计用的完整标识，过长时 API 换成摘要）里拆信任项。caller_trust 在请求体里，
    // 受 HMAC 签名（X-U8co-Sig 覆盖整个请求体的 SHA-256）保护，只有持共享密钥的一方能写；API 用验过签的令牌生成它，不收调用方
    // 自报的值。命中种类记进审计 read_only_match（operator / caller）。
    internal static class ReadOnlyGate
    {
        public const string Code = "account_read_only";
        public const string ExceptionCode = "read_only_exception";
        // 审计 read_only_match：命中的是按操作员的规则还是按调用系统的规则。
        public const string MatchOperator = "operator";
        public const string MatchCaller = "caller";
        public const string Text = "该账套只开放读取";
        public const string Hint = "这个账套在桥的 config.json 的 readOnlyAccounts 里，只能调用读取接口";

        static volatile string[] _accounts = new string[0];
        static volatile ReadOnlyExceptionRule[] _rules = new ReadOnlyExceptionRule[0];

        public static void Configure(BridgeConfig cfg)
        {
            string[] list = cfg == null ? null : cfg.ReadOnlyAccounts;
            _accounts = list == null ? new string[0] : (string[])list.Clone();
            ReadOnlyExceptionRule[] rules = cfg == null ? null : cfg.ReadOnlyExceptions;
            _rules = rules == null ? new ReadOnlyExceptionRule[0] : (ReadOnlyExceptionRule[])rules.Clone();
        }

        public static string[] Accounts()
        {
            return (string[])_accounts.Clone();
        }

        // 健康检查与 meta 用的例外摘要（非机密）。
        public static object[] ExceptionSummaries()
        {
            ReadOnlyExceptionRule[] rules = _rules;
            object[] list = new object[rules.Length];
            for (int i = 0; i < rules.Length; i++)
            {
                list[i] = Summary(rules[i]);
            }
            return list;
        }

        // 登录前：写路由且账套在只读名单里时，例外匹配则记 read_only_exception，否则记审计并抛出。
        public static void Pre(
            BridgeConfig cfg, AuditDraft draft, string acc, string user, Dictionary<string, object> body,
            WriteRequestInfo info)
        {
            if (draft == null || !WriteGate.IsWrite(draft.Path) || !BridgeConfig.IsReadOnlyAccount(cfg, acc))
            {
                return;
            }
            string kind = Matches(cfg, AskOf(acc, user, info, body, draft.Trust));
            draft.ReadOnlyMatch = kind ?? "";
            if (kind != null)
            {
                draft.Policy = ExceptionCode;
                return;
            }
            draft.Policy = Code;
            throw Refused();
        }

        // 入队后：同样的判断，审计 policy 记在任务上。
        public static void Post(WorkItem item)
        {
            if (item == null || !WriteGate.IsWrite(item.Path) || !BridgeConfig.IsReadOnlyAccount(item.Config, item.Acc))
            {
                return;
            }
            WriteRequestInfo info = item.WriteInfo ?? WriteClass.Of(item);
            string kind = Matches(item.Config, AskOf(item.Acc, item.Operator, info, item.Body, item.Trust));
            item.ReadOnlyMatch = kind ?? "";
            if (kind != null)
            {
                item.Policy = ExceptionCode;
                return;
            }
            item.Policy = Code;
            throw Refused();
        }

        // 命中种类：operator 优先于 caller（与 API match_of 一致）；都不命中为 null。
        static string Matches(BridgeConfig cfg, Ask ask)
        {
            ReadOnlyExceptionRule[] rules = cfg == null ? null : cfg.ReadOnlyExceptions;
            if (rules == null || ask == null)
            {
                return null;
            }
            string found = null;
            for (int i = 0; i < rules.Length; i++)
            {
                string kind = RuleMatch(rules[i], ask);
                if (kind == MatchOperator)
                {
                    return kind;
                }
                found = found ?? kind;
            }
            return found;
        }

        static Ask AskOf(string acc, string user, WriteRequestInfo info, Dictionary<string, object> body, string trust)
        {
            Ask ask = new Ask();
            ask.Acc = acc;
            ask.User = user;
            ask.Trust = IdemReq.IsTrustName(trust) ? trust : null;
            ask.Type = info == null ? null : info.Type;
            ask.Op = info == null ? null : info.Op;
            ask.Path = info == null ? null : info.Path;
            ask.Archive = body == null ? null : Requests.Field(body, "archive") as string;
            ask.Body = body;
            return ask;
        }

        // 命中种类（operator / caller），不命中为 null。
        static string RuleMatch(ReadOnlyExceptionRule rule, Ask ask)
        {
            if (rule == null || ask == null)
            {
                return null;
            }
            if (!Contains(rule.Accounts, ask.Acc, StringComparison.Ordinal))
            {
                return null;
            }
            string kind = IdentityMatch(rule, ask);
            return kind != null && RouteMatch(rule, ask) ? kind : null;
        }

        // 身份部分：callers 规则看 caller_trust，operators 规则看操作员；不命中为 null。
        static string IdentityMatch(ReadOnlyExceptionRule rule, Ask ask)
        {
            if (rule.Callers != null)
            {
                return CallerRuleMatches(rule, ask) ? MatchCaller : null;
            }
            return ContainsOperator(rule.Operators, ask.User) ? MatchOperator : null;
        }

        // 直接构造的规则同时带 operators、或用于 CallerOps 之外，一律不放行。
        static bool CallerRuleMatches(ReadOnlyExceptionRule rule, Ask ask)
        {
            return rule.Operators == null
                && string.Equals(rule.Type, ConfigRulesReadOnly.TypeVouchers, StringComparison.Ordinal)
                && Array.IndexOf(ConfigRulesReadOnly.CallerOps, rule.Op) >= 0
                && Contains(rule.Callers, ask.Trust, StringComparison.Ordinal);
        }

        // 路由部分：type=vouchers 的规则按登记表 ReadOnlyVoucherOps 看路径、单据类型、写入分类 op 和生单来源；
        // type=archives 的规则认路径 archives/<op>，op=create / delete 另看 archives。
        static bool RouteMatch(ReadOnlyExceptionRule rule, Ask ask)
        {
            if (string.Equals(rule.Type, ConfigRulesReadOnly.TypeVouchers, StringComparison.Ordinal))
            {
                return ReadOnlyVoucherOps.Covers(rule, ask.Path, ask.Type, ask.Op, ask.Body);
            }
            if (!string.Equals(rule.Op, ask.Op, StringComparison.Ordinal)
                || !string.Equals(rule.Type, ask.Type, StringComparison.Ordinal)
                || !string.Equals(ask.Path, Requests.ArcRoot + rule.Op, StringComparison.Ordinal)
                || !KnownArchiveOp(rule.Op))
            {
                return false;
            }
            // op=create / delete 的规则必须带 archives，请求的档案类型要在里面（缺省或为空不匹配任何请求），
            // 且仍在该 op 的可开放集合里（直接构造的规则也不能放行客户删除）；其它 op 不带。
            if (Array.IndexOf(ConfigRulesReadOnly.ArchiveOps, rule.Op) >= 0)
            {
                return Contains(rule.Archives, ask.Archive, StringComparison.Ordinal)
                    && Contains(ConfigRulesReadOnly.Eligible(rule.Op), ask.Archive, StringComparison.Ordinal);
            }
            return rule.Archives == null;
        }

        // type=archives 可开放的 op：客户专用路由或 create / delete。配置加载已拒绝其它 op，
        // 这里再挡一次，直接构造的规则（如 op=update）也不放行。
        static bool KnownArchiveOp(string op)
        {
            return ArcCustomerOps.Is(op) || Array.IndexOf(ConfigRulesReadOnly.ArchiveOps, op) >= 0;
        }

        // 操作员只忽略 ASCII 大小写（AsciiIgnoreCase，与 API ascii_fold 一致），不去首尾空白。
        static bool ContainsOperator(string[] list, string user)
        {
            if (list == null || user == null)
            {
                return false;
            }
            for (int i = 0; i < list.Length; i++)
            {
                if (AsciiIgnoreCase.Instance.Equals(list[i], user))
                {
                    return true;
                }
            }
            return false;
        }

        static bool Contains(string[] list, string value, StringComparison cmp)
        {
            if (list == null || value == null)
            {
                return false;
            }
            for (int i = 0; i < list.Length; i++)
            {
                if (string.Equals(list[i], value, cmp))
                {
                    return true;
                }
            }
            return false;
        }

        static BridgeException Refused()
        {
            return new BridgeException(403, Code, Text).WithHint(Hint);
        }

        // 追加到健康检查 JSON：,"read_only_accounts":[...],"read_only_exceptions":[...]。
        public static string HealthFragment()
        {
            string[] list = _accounts;
            StringBuilder sb = new StringBuilder(",\"read_only_accounts\":[");
            JsonStrings(sb, list);
            sb.Append("],\"read_only_exceptions\":[");
            ReadOnlyExceptionRule[] rules = _rules;
            for (int i = 0; i < rules.Length; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                AppendRule(sb, rules[i]);
            }
            return sb.Append(']').ToString();
        }

        static void AppendRule(StringBuilder sb, ReadOnlyExceptionRule rule)
        {
            sb.Append("{\"accounts\":[");
            JsonStrings(sb, rule.Accounts);
            sb.Append(rule.Callers != null ? "],\"callers\":[" : "],\"operators\":[");
            JsonStrings(sb, rule.Callers ?? rule.Operators);
            sb.Append("],\"type\":").Append(Quote(rule.Type));
            sb.Append(",\"op\":").Append(Quote(rule.Op));
            if (rule.Archives != null)
            {
                sb.Append(",\"archives\":[");
                JsonStrings(sb, rule.Archives);
                sb.Append(']');
            }
            if (rule.Voucher != null)
            {
                sb.Append(",\"voucher\":").Append(Quote(rule.Voucher));
            }
            sb.Append('}');
        }

        static void JsonStrings(StringBuilder sb, string[] list)
        {
            if (list == null)
            {
                return;
            }
            for (int i = 0; i < list.Length; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                sb.Append(Quote(list[i]));
            }
        }

        static string Quote(string text)
        {
            return new JavaScriptSerializer().Serialize(text ?? "");
        }

        static Dictionary<string, object> Summary(ReadOnlyExceptionRule rule)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["accounts"] = rule.Accounts;
            if (rule.Callers != null)
            {
                map["callers"] = rule.Callers;
            }
            else
            {
                map["operators"] = rule.Operators;
            }
            map["type"] = rule.Type;
            map["op"] = rule.Op;
            if (rule.Archives != null)
            {
                map["archives"] = rule.Archives;
            }
            if (rule.Voucher != null)
            {
                map["voucher"] = rule.Voucher;
            }
            return map;
        }

        // --check-config 的说明行（账套名单与例外各一行）。
        public static string Describe(BridgeConfig cfg)
        {
            string[] list = cfg == null || cfg.ReadOnlyAccounts == null ? new string[0] : cfg.ReadOnlyAccounts;
            string accounts = "readOnlyAccounts（只开放读取的账套）: "
                + (list.Length == 0 ? "无" : string.Join(",", list) + "（这些账套的写入一律 403 account_read_only，含预演）");
            return accounts + Environment.NewLine + DescribeExceptions(cfg);
        }

        static string DescribeExceptions(BridgeConfig cfg)
        {
            ReadOnlyExceptionRule[] rules = cfg == null || cfg.ReadOnlyExceptions == null
                ? new ReadOnlyExceptionRule[0] : cfg.ReadOnlyExceptions;
            if (rules.Length == 0)
            {
                return "readOnlyExceptions（只读账套允许的写入）: 无";
            }
            return "readOnlyExceptions（只读账套允许的写入）: " + rules.Length.ToString()
                + " 条，其中按调用系统（callers）" + CallerRules(rules).ToString()
                + " 条（匹配的写入放行，审计 policy 记 read_only_exception、read_only_match 记 operator 或 caller，其余仍 403 account_read_only）";
        }

        static int CallerRules(ReadOnlyExceptionRule[] rules)
        {
            int n = 0;
            for (int i = 0; i < rules.Length; i++)
            {
                if (rules[i] != null && rules[i].Callers != null)
                {
                    n++;
                }
            }
            return n;
        }

        sealed class Ask
        {
            public string Acc;
            public string User;
            // 请求体 caller_trust（IdemReq.TrustField）；没有或不合写法时为 null。
            public string Trust;
            public string Type;
            public string Op;
            public string Path;
            public string Archive;
            public Dictionary<string, object> Body;
        }
    }
}
