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
    // （客户 EAI 修改会清银行账号等，见 ArcCusFields）。vouchers 的 op 与单据类型 voucher 只能是登记表
    // ReadOnlyVoucherOps.Rows 里的一对（一项一对；head_fix、so_contract 单据类型固定，voucher 可省，写了须一致；
    // create、update、close、verify、generate 必须写 voucher），vouchers/delete、vouchers/lock 等其它单据路由不能开放；
    // archives 不能写 voucher。operators 比较只忽略 ASCII 大小写（AsciiIgnoreCase，非 ASCII 逐字比较），且不能含双引号、反斜杠或控制字符（健康检查 JSON 仍会转义）。
    // operators 与 callers 恰好写一个且非空（两个都写或都不写启动失败）。callers 是 API 信任项的 name（调用系统）：只能用于
    // type=vouchers 且 op 在 CallerOps（登记表里单据类型不固定的行：create、update、close、verify、generate）；archives、head_fix、
    // so_contract 只能写 operators。命中时任何操作员都放行，按请求体 caller（API 经 HMAC 签名通道发来的「信任项:客户端」）第一个冒号前的部分逐字比较。
    internal static class ConfigRulesReadOnly
    {
        internal const string TypeArchives = "archives";
        internal const string TypeVouchers = "vouchers";
        // type=vouchers 允许的 op：登记表 ReadOnlyVoucherOps.Rows（每个 op 只放行它自己的路径和列出的单据类型）。
        internal static readonly string[] VoucherOps = ReadOnlyVoucherOps.Ops();
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
            "accounts", "archives", "callers", "op", "operators", "type", "voucher"
        };
        // callers 可用的 op：登记表里 Fixed 为 false 的行（与 API readonly_exceptions.CALLER_OPS 一致）。
        internal static readonly string[] CallerOps = NonFixedOps();
        // API 调用方标识过长时 caller 换成 sha256:<摘要>（co_idem.caller_tag），信任项不能叫这个名字。
        internal const string DigestTag = "sha256";

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
            bool hasOperators = obj.ContainsKey("operators");
            bool byCaller = obj.ContainsKey("callers");
            if (hasOperators == byCaller)
            {
                throw new InvalidOperationException(at + " 必须恰好写 operators 或 callers 之一（两个都写或都不写都不行）");
            }
            ReadOnlyExceptionRule rule = new ReadOnlyExceptionRule();
            rule.Accounts = ExceptionCodes(obj, at, readOnly);
            rule.Operators = byCaller ? null : ExceptionOperators(obj, at);
            rule.Type = TypeOf(obj, at);
            rule.Op = OpOf(obj, at, rule.Type);
            rule.Archives = rule.Type == TypeVouchers ? NoArchives(obj, at) : ArchivesOf(obj, at, rule.Op);
            rule.Voucher = VoucherOf(obj, at, rule.Type, rule.Op);
            rule.Callers = byCaller ? CallersOf(obj, at, rule.Type, rule.Op) : null;
            return rule;
        }

        // callers：只用于 type=vouchers 的 CallerOps；每项是信任项 name（字母开头，字母、数字、_、-，1 到 63 个字符，
        // 同 API trust._NAME），逐字比较、不能重复、不能是 sha256。
        static string[] CallersOf(Dictionary<string, object> obj, string at, string type, string op)
        {
            if (type != TypeVouchers || Array.IndexOf(CallerOps, op) < 0)
            {
                throw new InvalidOperationException(at + ".callers 只能用于 type=vouchers 的 op="
                    + string.Join(" / ", CallerOps) + "；其它项写 operators");
            }
            string[] list = NeedStrings(obj, "callers", at, StringComparer.Ordinal);
            for (int i = 0; i < list.Length; i++)
            {
                if (!TrustName(list[i]))
                {
                    throw new InvalidOperationException(at + ".callers 的每一项必须是信任项的 name（字母开头，字母、数字、_、-，不能是 "
                        + DigestTag + "）：" + list[i]);
                }
            }
            return list;
        }

        // 写法同 IdemReq.IsTrustName（同 API trust._NAME），另外不能是 sha256。
        internal static bool TrustName(string text)
        {
            return text != DigestTag && IdemReq.IsTrustName(text);
        }

        static string[] NonFixedOps()
        {
            List<string> ops = new List<string>();
            for (int i = 0; i < ReadOnlyVoucherOps.Rows.Length; i++)
            {
                if (!ReadOnlyVoucherOps.Rows[i].Fixed)
                {
                    ops.Add(ReadOnlyVoucherOps.Rows[i].Op);
                }
            }
            return ops.ToArray();
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
            string[] list = NeedStrings(obj, "operators", at, AsciiIgnoreCase.Instance);
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

        // op：archives 是客户专用路由或 create / delete；vouchers 是 VoucherOps（登记表 ReadOnlyVoucherOps.Rows）。
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

        // type=vouchers 不能写 archives（op 名和 archives 的 create / delete 重名也不行）。
        static string[] NoArchives(Dictionary<string, object> obj, string at)
        {
            if (obj.ContainsKey("archives"))
            {
                throw new InvalidOperationException(at + ".archives 只能用于 type=archives 的 op=" + string.Join(" / ", ArchiveOps));
            }
            return null;
        }

        // voucher：type=vouchers 时须在登记表该 op 的那一行里，单据类型固定的 op 可省；其它 type 不能写。
        static string VoucherOf(Dictionary<string, object> obj, string at, string type, string op)
        {
            if (type != TypeVouchers)
            {
                if (obj.ContainsKey("voucher"))
                {
                    throw new InvalidOperationException(at + ".voucher 只能用于 type=" + TypeVouchers);
                }
                return null;
            }
            ReadOnlyVoucherOps.Row row = ReadOnlyVoucherOps.Find(op);
            if (!obj.ContainsKey("voucher"))
            {
                if (row.Fixed)
                {
                    return null;
                }
                throw new InvalidOperationException(at + ".voucher 在 op=" + op + " 时必须给出");
            }
            string text = obj["voucher"] as string;
            if (text == null || ReadOnlyVoucherOps.VoucherOf(op, text) == null)
            {
                throw new InvalidOperationException(at + ".voucher 在 op=" + op + " 下目前只允许 "
                    + string.Join(", ", row.Kinds) + "：" + (text ?? ""));
            }
            return text;
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
        // 仅 type=archives 的 op=create / delete：放行的档案类型；其它为 null。
        public string[] Archives;
        // 仅 type=vouchers：配置的单据类型；head_fix、so_contract 省略时为 null（取固定的那一种）。
        public string Voucher;
        // 按调用系统放行（API 信任项 name）；按操作员放行的行为 null。与 Operators 恰好一个非 null。
        public string[] Callers;
    }
}
