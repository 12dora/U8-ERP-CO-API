using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 写入策略文件（writePolicyFile）解析后的一份快照。加载后不再改动，请求只读同一份快照；重载时整份替换。
    // 解析与校验在 WritePolicyParse.cs（同一个 partial 类）。
    internal sealed partial class WritePolicySnapshot
    {
        public int Version { get; private set; }
        public int ReloadSeconds { get; private set; }
        public bool FreezeGlobal { get; private set; }
        // 冻结的账套与原因（原因可空）。
        public string[] FreezeAccounts { get; private set; }
        public string FreezeReason { get; private set; }
        // 写入时段；null 表示不限时段。
        public PolicyWindow[] Windows { get; private set; }
        // 整天禁止写入的日期（yyyy-MM-dd，本机日期）。
        public string[] DenyDates { get; private set; }
        public PolicyLicense License { get; private set; }
        public WriteQuotaLimits Defaults { get; private set; }
        // 未列出的账套：false 拒绝（缺省），true 放行。
        public bool UnlistedAllow { get; private set; }
        public DateTime LoadedAtUtc { get; private set; }
        Dictionary<string, PolicyAccount> _accounts;

        WritePolicySnapshot()
        {
        }

        // 列出的账套；未列出时返回 null。
        public PolicyAccount Account(string acc)
        {
            PolicyAccount found;
            if (acc == null || !_accounts.TryGetValue(acc, out found))
            {
                return null;
            }
            return found;
        }

        // 账套的限额：账套 quotas 按键覆盖 defaults；未列出的账套用 defaults。
        public WriteQuotaLimits Quota(string acc)
        {
            PolicyAccount found = Account(acc);
            return found == null ? Defaults : found.Quota;
        }

        public bool IsFrozen(string acc)
        {
            return FreezeGlobal || (acc != null && Array.IndexOf(FreezeAccounts, acc) >= 0);
        }

        // 本机时间 local 是否可写：不在 denyDates 里，且（没有设时段或落在任一时段内）。
        public bool WindowOpen(DateTime local)
        {
            string day = local.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (Array.IndexOf(DenyDates, day) >= 0)
            {
                return false;
            }
            if (Windows == null)
            {
                return true;
            }
            for (int i = 0; i < Windows.Length; i++)
            {
                if (Windows[i].Contains(local))
                {
                    return true;
                }
            }
            return false;
        }
    }

    // 一个账套的策略：操作员名单、限额、放行规则。
    internal sealed class PolicyAccount
    {
        public readonly string Code;
        public readonly PolicyOperators Operators;
        public readonly WriteQuotaLimits Quota;
        public readonly PolicyRule[] Rules;

        public PolicyAccount(string code, PolicyOperators operators, WriteQuotaLimits quota, PolicyRule[] rules)
        {
            Code = code;
            Operators = operators;
            Quota = quota;
            Rules = rules;
        }

        public bool Allows(string type, string op)
        {
            for (int i = 0; i < Rules.Length; i++)
            {
                if (Rules[i].Matches(type, op))
                {
                    return true;
                }
            }
            return false;
        }
    }

    // 放行规则：type 等于请求的类型或为 "*"，且 ops 含请求的操作或 "*"。
    internal sealed class PolicyRule
    {
        public readonly string Type;
        public readonly string[] Ops;

        public PolicyRule(string type, string[] ops)
        {
            Type = type;
            Ops = ops;
        }

        public bool Matches(string type, string op)
        {
            if (Type != "*" && !string.Equals(Type, type, StringComparison.Ordinal))
            {
                return false;
            }
            return Array.IndexOf(Ops, "*") >= 0 || (op != null && Array.IndexOf(Ops, op) >= 0);
        }
    }

    // 操作员名单：deny 优先；allow 为空表示任何操作员。比较去首尾空白、不区分大小写（U8 操作员编码不区分大小写）。
    internal sealed class PolicyOperators
    {
        public static readonly PolicyOperators Any = new PolicyOperators(new string[0], new string[0]);
        public readonly string[] Allow;
        public readonly string[] Deny;

        public PolicyOperators(string[] allow, string[] deny)
        {
            Allow = allow;
            Deny = deny;
        }

        public bool Permits(string op)
        {
            string who = op == null ? "" : op.Trim();
            if (Listed(Deny, who))
            {
                return false;
            }
            return Allow.Length == 0 || Listed(Allow, who);
        }

        static bool Listed(string[] list, string who)
        {
            if (who.Length == 0)
            {
                return false;
            }
            for (int i = 0; i < list.Length; i++)
            {
                if (string.Equals(list[i].Trim(), who, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }
    }

    // 限额。0 表示不限。
    internal sealed class WriteQuotaLimits
    {
        public static readonly WriteQuotaLimits None = new WriteQuotaLimits(0, 0, 0, 0m);
        public readonly int PerMinute;
        public readonly int PerDay;
        public readonly int MaxLines;
        public readonly decimal MaxAmount;

        public WriteQuotaLimits(int writesPerMinute, int writesPerDay, int maxLines, decimal maxAmount)
        {
            PerMinute = writesPerMinute;
            PerDay = writesPerDay;
            MaxLines = maxLines;
            MaxAmount = maxAmount;
        }
    }

    // 许可保护（只在桥上生效）：同时在线的接口登录上限（0 不限），以及许可状态为哪些值时暂停写入。
    internal sealed class PolicyLicense
    {
        public static readonly PolicyLicense None = new PolicyLicense(0, new string[0]);
        public readonly int MaxConcurrentLogins;
        public readonly string[] HoldWritesWhen;

        public PolicyLicense(int maxConcurrentLogins, string[] holdWritesWhen)
        {
            MaxConcurrentLogins = maxConcurrentLogins;
            HoldWritesWhen = holdWritesWhen;
        }

        // state 为健康检查 license 的取值（ok / near / full / unknown）。
        public bool Holds(string state)
        {
            return state != null && Array.IndexOf(HoldWritesWhen, state) >= 0;
        }
    }

    // 写入时段：days 为 ISO 星期（1 是星期一），含 start、不含 end，不跨午夜，按本机时间。
    internal sealed class PolicyWindow
    {
        readonly bool[] _days;
        readonly int _start;
        readonly int _end;

        public PolicyWindow(bool[] days, int start, int end)
        {
            _days = days;
            _start = start;
            _end = end;
        }

        public bool Contains(DateTime local)
        {
            int iso = local.DayOfWeek == DayOfWeek.Sunday ? 7 : (int)local.DayOfWeek;
            int clock = local.Hour * 3600 + local.Minute * 60 + local.Second;
            return _days[iso] && clock >= _start && clock < _end;
        }
    }
}
