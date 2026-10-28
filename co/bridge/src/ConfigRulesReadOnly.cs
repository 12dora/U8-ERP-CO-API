using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // config.json 的 readOnlyExceptions：只读账套上允许穿过闸门的精确写入。账套必须同时在
    // readOnlyAccounts 里；type 是 archives 或 vouchers。archives 的 op 是客户专用路由（ArcCustomerOps.Ops：customer_person、
    // customer_fields、customer_address、customer_address_add、customer_address_delete）或 create、delete。
    // op=create / delete 必须另写 archives（非空列表，每项须在该 op 的可开放集合里：create 是 EligibleArchives
    // ＝ district_class、customer，delete 是 EligibleDelete ＝ district_class；delete 用于回滚刚新增的地区分类，
    // 客户不能删，新增错的客户用 customer_fields 写停用日期）；其余 op 不能写 archives。archives/update 不能开放
    // （客户 EAI 修改会清银行账号等，见 ArcCusFields）。vouchers 只允许 op=head_fix（历史发货单表头修正，
    // 路径恰为 vouchers/head_fix），vouchers/update 等其它单据路由不能开放。operators 比较不区分大小写，
    // 且不能含双引号、反斜杠或控制字符（健康检查 JSON 仍会转义）。
    internal static class ConfigRulesReadOnly
    {
        internal const string TypeArchives = "archives";
        internal const string TypeVouchers = "vouchers";
        // type=vouchers 允许的 op（目前只有历史发货单表头修正）。
        internal static readonly string[] VoucherOps = new string[] { VoucherHeadFix.Op };
        // 带 archives 的 op：请求体 archive 必须在规则的 archives 里。
        internal static readonly string[] ArchiveOps = new string[] { "create", "delete" };
        // 客户档案的类型名（ArcKind.Customer 的 Name）。
        internal const string CustomerArchive = "customer";
        // 允许走只读例外新增的档案类型（官方 EAI 组件写入，INSERT 不经 diffedit）。
        internal static readonly string[] EligibleArchives = new string[] { ArcKindRwMore.District, CustomerArchive };
        // 允许走只读例外删除的档案类型：只有地区分类（回滚刚新增的）；客户不开放删除。
        internal static readonly string[] EligibleDelete = new string[] { ArcKindRwMore.District };
        static readonly string[] Keys = new string[]
        {
            "accounts", "archives", "op", "operators", "type"
        };

        public static ReadOnlyExceptionRule[] ReadOnlyExceptions(Dictionary<string, object> map, string[] readOnly)
        {
            object raw;
            if (map == null || !map.TryGetValue("readOnlyExceptions", out raw))
            {
                return new ReadOnlyExceptionRule[0];
            }
            IList list = raw as IList;
            if (list == null || raw is string)
            {
                throw new InvalidOperationException("readOnlyExceptions 必须是对象数组");
            }
            ReadOnlyExceptionRule[] rules = new ReadOnlyExceptionRule[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string at = "readOnlyExceptions[" + i.ToString(CultureInfo.InvariantCulture) + "]";
                rules[i] = Parse(list[i], at, readOnly ?? new string[0]);
            }
            return rules;
        }

        // 例外里的账套不在 allowedAccounts 时，请求仍会在白名单被拒。
        internal static void Warn(BridgeConfig cfg, List<string> list)
        {
            string stray = StrayAccount(cfg);
            if (stray != null)
            {
                list.Add("readOnlyExceptions 里的 " + stray + " 不在 allowedAccounts 里：这个账套的请求仍会被拒绝");
            }
        }

        static ReadOnlyExceptionRule Parse(object raw, string at, string[] readOnly)
        {
            Dictionary<string, object> obj = raw as Dictionary<string, object>;
            if (obj == null)
            {
                throw new InvalidOperationException(at + " 必须是对象");
            }
            CheckKeys(obj, at);
            ReadOnlyExceptionRule rule = new ReadOnlyExceptionRule();
            rule.Accounts = ExceptionCodes(obj, at, readOnly);
            rule.Operators = ExceptionOperators(obj, at);
            rule.Type = TypeOf(obj, at);
            rule.Op = OpOf(obj, at, rule.Type);
            rule.Archives = ArchivesOf(obj, at, rule.Op);
            return rule;
        }

        static void CheckKeys(Dictionary<string, object> obj, string at)
        {
            List<string> extra = new List<string>();
            foreach (string key in obj.Keys)
            {
                if (Array.IndexOf(Keys, key) < 0)
                {
                    extra.Add(key);
                }
            }
            if (extra.Count > 0)
            {
                extra.Sort(StringComparer.Ordinal);
                throw new InvalidOperationException(at + " 含未知字段 " + extra[0] + "；可用的键："
                    + string.Join(", ", Keys));
            }
        }

        static string[] ExceptionCodes(Dictionary<string, object> obj, string at, string[] readOnly)
        {
            string[] list = NeedStrings(obj, "accounts", at, StringComparer.Ordinal);
            ConfigRules.CheckCodes(list, at + ".accounts");
            for (int i = 0; i < list.Length; i++)
            {
                if (!ContainsCode(readOnly, list[i]))
                {
                    throw new InvalidOperationException(at + ".accounts 的账套必须同时在 readOnlyAccounts 里：" + list[i]);
                }
            }
            return list;
        }

        static string[] ExceptionOperators(Dictionary<string, object> obj, string at)
        {
            string[] list = NeedStrings(obj, "operators", at, StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < list.Length; i++)
            {
                if (!ConfigRules.OperatorCode(list[i]))
                {
                    throw new InvalidOperationException(at + ".operators 的每一项必须是有效的 U8 操作员编码：" + list[i]);
                }
                JsonSafe(list[i], at + ".operators");
            }
            return list;
        }

        // type：archives 或 vouchers。
        static string TypeOf(Dictionary<string, object> obj, string at)
        {
            string text = NeedText(obj, "type", at);
            if (text != TypeArchives && text != TypeVouchers)
            {
                throw new InvalidOperationException(at + ".type 目前只允许 " + TypeArchives + ", " + TypeVouchers + "：" + text);
            }
            return text;
        }

        // op：archives 是客户专用路由或 create / delete；vouchers 只有 head_fix。
        static string OpOf(Dictionary<string, object> obj, string at, string type)
        {
            string text = NeedText(obj, "op", at);
            if (type == TypeVouchers)
            {
                if (Array.IndexOf(VoucherOps, text) < 0)
                {
                    throw new InvalidOperationException(at + ".op 在 type=vouchers 下目前只允许 "
                        + string.Join(", ", VoucherOps) + "：" + text);
                }
                return text;
            }
            if (Array.IndexOf(ArchiveOps, text) < 0 && !ArcCustomerOps.Is(text))
            {
                List<string> allowed = new List<string>(ArcCustomerOps.Ops);
                allowed.AddRange(ArchiveOps);
                throw new InvalidOperationException(at + ".op 目前只允许 " + string.Join(", ", allowed.ToArray())
                    + "：" + text);
            }
            return text;
        }

        // op=create / delete 必须有 archives 且每项在该 op 的可开放集合里（Eligible）；其它 op 不能写 archives（返回 null）。
        static string[] ArchivesOf(Dictionary<string, object> obj, string at, string op)
        {
            if (Array.IndexOf(ArchiveOps, op) < 0)
            {
                if (obj.ContainsKey("archives"))
                {
                    throw new InvalidOperationException(at + ".archives 只能用于 op=" + string.Join(" / ", ArchiveOps));
                }
                return null;
            }
            string[] list = NeedStrings(obj, "archives", at, StringComparer.Ordinal);
            string[] eligible = Eligible(op);
            for (int i = 0; i < list.Length; i++)
            {
                if (Array.IndexOf(eligible, list[i]) < 0)
                {
                    throw new InvalidOperationException(at + ".archives 在 op=" + op + " 下目前只允许 "
                        + string.Join(", ", eligible) + "：" + list[i]);
                }
            }
            return list;
        }

        // 某个 op 可开放的档案类型：create → EligibleArchives，delete → EligibleDelete，其它 op 没有。
        internal static string[] Eligible(string op)
        {
            if (op == "create")
            {
                return EligibleArchives;
            }
            return op == "delete" ? EligibleDelete : new string[0];
        }

        static string NeedText(Dictionary<string, object> map, string key, string at)
        {
            object raw;
            if (!map.TryGetValue(key, out raw) || !(raw is string) || ((string)raw).Length == 0)
            {
                throw new InvalidOperationException(at + "." + key + " 必须是非空字符串");
            }
            return (string)raw;
        }

        static string[] NeedStrings(Dictionary<string, object> map, string key, string at, StringComparer cmp)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                throw new InvalidOperationException(at + " 缺少 " + key);
            }
            IList list = raw as IList;
            if (list == null || raw is string)
            {
                throw new InvalidOperationException(at + "." + key + " 必须是字符串数组");
            }
            if (list.Count == 0)
            {
                throw new InvalidOperationException(at + "." + key + " 不能为空");
            }
            return UniqueStrings(list, at + "." + key, cmp);
        }

        static string[] UniqueStrings(IList list, string at, StringComparer cmp)
        {
            string[] values = new string[list.Count];
            HashSet<string> seen = new HashSet<string>(cmp);
            for (int i = 0; i < list.Count; i++)
            {
                string item = list[i] as string;
                if (item == null)
                {
                    throw new InvalidOperationException(at + " 含非字符串");
                }
                if (!seen.Add(item))
                {
                    throw new InvalidOperationException(at + " 含重复项：" + item);
                }
                values[i] = item;
            }
            return values;
        }

        static void JsonSafe(string text, string at)
        {
            if (text == null)
            {
                return;
            }
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '"' || c == '\\' || char.IsControl(c))
                {
                    throw new InvalidOperationException(at + " 不能含双引号、反斜杠或控制字符");
                }
            }
        }

        static bool ContainsCode(string[] list, string acc)
        {
            if (list == null)
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

        static string StrayAccount(BridgeConfig cfg)
        {
            ReadOnlyExceptionRule[] rules = cfg.ReadOnlyExceptions ?? new ReadOnlyExceptionRule[0];
            for (int i = 0; i < rules.Length; i++)
            {
                string[] accounts = rules[i].Accounts ?? new string[0];
                for (int j = 0; j < accounts.Length; j++)
                {
                    if (!BridgeConfig.AllowsAccount(cfg, accounts[j]))
                    {
                        return accounts[j];
                    }
                }
            }
            return null;
        }
    }

    // config.json 的 readOnlyExceptions 一项。字段在加载时已校验。
    internal sealed class ReadOnlyExceptionRule
    {
        public string[] Accounts;
        public string[] Operators;
        public string Type;
        public string Op;
        // 仅 op=create / delete：放行的档案类型；其它 op 为 null。
        public string[] Archives;
    }
}
