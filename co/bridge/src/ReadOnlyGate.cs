using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 只读账套（config.json 的 readOnlyAccounts）：名单里的账套只开放读取。WriteGate 登记的每条写路由一律
    // 403 account_read_only，含预演与幂等重放；写入策略文件、测试账套名单都不能放行。
    // readOnlyExceptions 匹配的写入放行，审计 policy 记 read_only_exception；不匹配的仍拒绝。匹配 = 账套、操作员
    // （不分大小写）、写入分类 (archives, op) 都对上，路径恰为 archives/<op>；op=create / delete 另要求请求体
    // archive 在规则的 archives 里（目前只有 district_class）。type=vouchers 的规则只放行 op=head_fix：
    // 路径恰为 vouchers/head_fix 且请求体 type 为 dispatch，vouchers/update 等其它单据路由仍拒绝。登录前在 WriteClassGate.Pre（账套名单之后、解密之前，先于写入策略）
    // 查，入队后在 WriteClassGate.Post（登录 U8 之前）再查一次。
    internal static class ReadOnlyGate
    {
        public const string Code = "account_read_only";
        public const string ExceptionCode = "read_only_exception";
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
            if (Matches(cfg, AskOf(acc, user, info, body)))
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
            if (Matches(item.Config, AskOf(item.Acc, item.Operator, info, item.Body)))
            {
                item.Policy = ExceptionCode;
                return;
            }
            item.Policy = Code;
            throw Refused();
        }

        static bool Matches(BridgeConfig cfg, Ask ask)
        {
            ReadOnlyExceptionRule[] rules = cfg == null ? null : cfg.ReadOnlyExceptions;
            if (rules == null || ask == null)
            {
                return false;
            }
            for (int i = 0; i < rules.Length; i++)
            {
                if (RuleMatch(rules[i], ask))
                {
                    return true;
                }
            }
            return false;
        }

        static Ask AskOf(string acc, string user, WriteRequestInfo info, Dictionary<string, object> body)
        {
            Ask ask = new Ask();
            ask.Acc = acc;
            ask.User = user;
            ask.Type = info == null ? null : info.Type;
            ask.Op = info == null ? null : info.Op;
            ask.Path = info == null ? null : info.Path;
            ask.Archive = body == null ? null : Requests.Field(body, "archive") as string;
            return ask;
        }

        static bool RuleMatch(ReadOnlyExceptionRule rule, Ask ask)
        {
            if (rule == null || ask == null)
            {
                return false;
            }
            if (!Contains(rule.Accounts, ask.Acc, StringComparison.Ordinal))
            {
                return false;
            }
            if (!Contains(rule.Operators, ask.User, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            return RouteMatch(rule, ask);
        }

        // 路由部分：type=vouchers 的规则只认路径恰为 vouchers/head_fix、单据类型 dispatch；
        // type=archives 的规则认路径 archives/<op>，op=create / delete 另看 archives。
        static bool RouteMatch(ReadOnlyExceptionRule rule, Ask ask)
        {
            if (!string.Equals(rule.Op, ask.Op, StringComparison.Ordinal))
            {
                return false;
            }
            if (string.Equals(rule.Type, ConfigRulesReadOnly.TypeVouchers, StringComparison.Ordinal))
            {
                return rule.Archives == null && string.Equals(ask.Path, VoucherHeadFix.Path, StringComparison.Ordinal)
                    && string.Equals(ask.Type, VoucherHeadFix.Kind, StringComparison.Ordinal);
            }
            if (!string.Equals(rule.Type, ask.Type, StringComparison.Ordinal)
                || !string.Equals(ask.Path, Requests.ArcRoot + rule.Op, StringComparison.Ordinal))
            {
                return false;
            }
            // op=create / delete 的规则必须带 archives，请求的档案类型要在里面（缺省或为空不匹配任何请求）；其它 op 不带。
            if (Array.IndexOf(ConfigRulesReadOnly.ArchiveOps, rule.Op) >= 0)
            {
                return Contains(rule.Archives, ask.Archive, StringComparison.Ordinal);
            }
            return rule.Archives == null;
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
            sb.Append("],\"operators\":[");
            JsonStrings(sb, rule.Operators);
            sb.Append("],\"type\":").Append(Quote(rule.Type));
            sb.Append(",\"op\":").Append(Quote(rule.Op));
            if (rule.Archives != null)
            {
                sb.Append(",\"archives\":[");
                JsonStrings(sb, rule.Archives);
                sb.Append(']');
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
            map["operators"] = rule.Operators;
            map["type"] = rule.Type;
            map["op"] = rule.Op;
            if (rule.Archives != null)
            {
                map["archives"] = rule.Archives;
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
                + " 条（匹配的写入放行，审计 policy 记 read_only_exception，其余仍 403 account_read_only）";
        }

        sealed class Ask
        {
            public string Acc;
            public string User;
            public string Type;
            public string Op;
            public string Path;
            public string Archive;
        }
    }
}
