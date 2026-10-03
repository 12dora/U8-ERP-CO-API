using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 写入策略文件的解析与校验（version 1）。任何对象层级都可以写字符串 "comment"；其余未知键一律拒绝（整份无效）。
    // 报错是 InvalidOperationException，消息写明键路径，例如「写入策略 accounts.801.allow[0].ops 含未知操作 'sell'」。
    internal sealed partial class WritePolicySnapshot
    {
        // 操作词表（封闭）。规则里另可写 "*"。
        public static readonly string[] OpNames = new string[]
        {
            "create", "update", "delete", "verify", "unverify", "close", "open", "generate", "lock", "unlock",
            "workflow", "writeoff", "voucher", "post", "process", "other"
        };

        static readonly string[] TopKeys = new string[]
        {
            "comment", "version", "reloadSeconds", "freeze", "windows", "denyDates", "license", "defaults", "unlisted", "accounts"
        };
        static readonly string[] FreezeKeys = new string[] { "comment", "global", "accounts", "reason" };
        static readonly string[] WindowKeys = new string[] { "comment", "days", "start", "end" };
        static readonly string[] LicenseKeys = new string[] { "comment", "maxConcurrentLogins", "holdWritesWhen" };
        static readonly string[] QuotaKeys = new string[] { "comment", "writesPerMinute", "writesPerDay", "maxLines", "maxAmount" };
        static readonly string[] AccountKeys = new string[] { "comment", "operators", "quotas", "allow" };
        static readonly string[] OperatorKeys = new string[] { "comment", "allow", "deny" };
        static readonly string[] RuleKeys = new string[] { "comment", "type", "ops" };
        static readonly string[] LicenseStates = new string[] { "near", "full", "unknown" };
        static readonly Regex DaysPattern = new Regex(
            "^[1-7](?:-[1-7])?(?:,[1-7](?:-[1-7])?)*\\z", RegexOptions.CultureInvariant);
        static readonly Regex ClockPattern = new Regex(
            "^([01][0-9]|2[0-3]):([0-5][0-9])\\z", RegexOptions.CultureInvariant);
        static readonly Regex TypePattern = new Regex("^(\\*|[a-z][a-z0-9_]{0,63})\\z", RegexOptions.CultureInvariant);
        static readonly Regex AccPattern = new Regex("^[0-9]{3}\\z", RegexOptions.CultureInvariant);

        public static WritePolicySnapshot Parse(string json, DateTime loadedAtUtc)
        {
            Dictionary<string, object> map = ParseObject(json);
            CheckKeys(map, TopKeys, "");
            object version;
            if (!map.TryGetValue("version", out version) || !(version is int) || (int)version != 1)
            {
                throw Error("version 必须是 1");
            }
            WritePolicySnapshot snap = new WritePolicySnapshot();
            snap.Version = 1;
            snap.LoadedAtUtc = loadedAtUtc;
            snap.ReloadSeconds = Int(map, "reloadSeconds", "reloadSeconds", 2, 1, 3600);
            ReadFreeze(snap, map);
            snap.Windows = ReadWindows(map);
            snap.DenyDates = ReadDates(map);
            snap.License = ReadLicense(map);
            snap.Defaults = ReadQuota(Section(map, "defaults", "defaults"), "defaults", WriteQuotaLimits.None);
            snap.UnlistedAllow = ReadUnlisted(map);
            snap._accounts = ReadAccounts(map, snap.Defaults);
            return snap;
        }

        static Dictionary<string, object> ParseObject(string json)
        {
            object obj;
            try
            {
                obj = new JavaScriptSerializer().DeserializeObject(json ?? "");
            }
            catch (ArgumentException ex)
            {
                throw Error("不是有效的 JSON：" + ex.Message);
            }
            Dictionary<string, object> map = obj as Dictionary<string, object>;
            if (map == null)
            {
                throw Error("必须是 JSON 对象");
            }
            return map;
        }

        static void ReadFreeze(WritePolicySnapshot snap, Dictionary<string, object> map)
        {
            Dictionary<string, object> sec = Section(map, "freeze", "freeze");
            CheckKeys(sec, FreezeKeys, "freeze");
            snap.FreezeGlobal = Bool(sec, "global", "freeze.global");
            snap.FreezeAccounts = Accounts(sec, "accounts", "freeze.accounts");
            snap.FreezeReason = Text(sec, "reason", "freeze.reason");
        }

        static PolicyWindow[] ReadWindows(Dictionary<string, object> map)
        {
            if (!map.ContainsKey("windows"))
            {
                return null;
            }
            IList list = AsList(map, "windows", "windows");
            if (list.Count == 0)
            {
                throw Error("windows 不能是空数组；不限时段时删掉这个键");
            }
            PolicyWindow[] windows = new PolicyWindow[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string at = "windows[" + i.ToString(CultureInfo.InvariantCulture) + "]";
                Dictionary<string, object> sec = AsObject(list[i], at);
                CheckKeys(sec, WindowKeys, at);
                int start = Clock(Need(sec, "start", at + ".start"), at + ".start");
                int end = Clock(Need(sec, "end", at + ".end"), at + ".end");
                if (end <= start)
                {
                    throw Error(at + ".end 必须晚于 start（不支持跨午夜）");
                }
                windows[i] = new PolicyWindow(Days(Need(sec, "days", at + ".days"), at + ".days"), start, end);
            }
            return windows;
        }

        static string[] ReadDates(Dictionary<string, object> map)
        {
            string[] dates = Strings(map, "denyDates", "denyDates");
            for (int i = 0; i < dates.Length; i++)
            {
                DateTime day;
                if (!DateTime.TryParseExact(dates[i], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
                {
                    throw Error("denyDates 的每一项必须是 yyyy-MM-dd：'" + dates[i] + "'");
                }
            }
            return dates;
        }

        static PolicyLicense ReadLicense(Dictionary<string, object> map)
        {
            Dictionary<string, object> sec = Section(map, "license", "license");
            CheckKeys(sec, LicenseKeys, "license");
            int logins = Int(sec, "maxConcurrentLogins", "license.maxConcurrentLogins", 0, 0, 64);
            string[] states = Strings(sec, "holdWritesWhen", "license.holdWritesWhen");
            for (int i = 0; i < states.Length; i++)
            {
                if (Array.IndexOf(LicenseStates, states[i]) < 0)
                {
                    throw Error("license.holdWritesWhen 只能是 near、full、unknown：'" + states[i] + "'");
                }
            }
            return new PolicyLicense(logins, states);
        }

        // 缺少的键取 fallback 的值（账套 quotas 按键覆盖 defaults）。
        static WriteQuotaLimits ReadQuota(Dictionary<string, object> sec, string at, WriteQuotaLimits fallback)
        {
            CheckKeys(sec, QuotaKeys, at);
            return new WriteQuotaLimits(
                Int(sec, "writesPerMinute", at + ".writesPerMinute", fallback.PerMinute, 0, 100000),
                Int(sec, "writesPerDay", at + ".writesPerDay", fallback.PerDay, 0, 10000000),
                Int(sec, "maxLines", at + ".maxLines", fallback.MaxLines, 0, 100000),
                Amount(sec, "maxAmount", at + ".maxAmount", fallback.MaxAmount));
        }

        static bool ReadUnlisted(Dictionary<string, object> map)
        {
            string text = Text(map, "unlisted", "unlisted");
            if (text == null || text == "deny")
            {
                return false;
            }
            if (text == "allow")
            {
                return true;
            }
            throw Error("unlisted 只能是 \"deny\" 或 \"allow\"");
        }

        static Dictionary<string, PolicyAccount> ReadAccounts(Dictionary<string, object> map, WriteQuotaLimits defaults)
        {
            Dictionary<string, PolicyAccount> result = new Dictionary<string, PolicyAccount>(StringComparer.Ordinal);
            Dictionary<string, object> sec = Section(map, "accounts", "accounts");
            foreach (KeyValuePair<string, object> pair in sec)
            {
                if (pair.Key == "comment")
                {
                    continue;
                }
                string at = "accounts." + pair.Key;
                if (!AccPattern.IsMatch(pair.Key))
                {
                    throw Error("accounts 的键必须是 3 位数字的账套号：'" + pair.Key + "'");
                }
                Dictionary<string, object> acc = AsObject(pair.Value, at);
                CheckKeys(acc, AccountKeys, at);
                Dictionary<string, object> ops = Section(acc, "operators", at + ".operators");
                CheckKeys(ops, OperatorKeys, at + ".operators");
                PolicyOperators operators = new PolicyOperators(
                    Strings(ops, "allow", at + ".operators.allow"), Strings(ops, "deny", at + ".operators.deny"));
                WriteQuotaLimits quota = ReadQuota(Section(acc, "quotas", at + ".quotas"), at + ".quotas", defaults);
                result[pair.Key] = new PolicyAccount(pair.Key, operators, quota, ReadRules(acc, at + ".allow"));
            }
            return result;
        }

        static PolicyRule[] ReadRules(Dictionary<string, object> acc, string at)
        {
            if (!acc.ContainsKey("allow"))
            {
                return new PolicyRule[0];
            }
            IList list = AsList(acc, "allow", at);
            PolicyRule[] rules = new PolicyRule[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string here = at + "[" + i.ToString(CultureInfo.InvariantCulture) + "]";
                Dictionary<string, object> sec = AsObject(list[i], here);
                CheckKeys(sec, RuleKeys, here);
                string type = Need(sec, "type", here + ".type");
                if (!TypePattern.IsMatch(type))
                {
                    throw Error(here + ".type 必须是 \"*\" 或小写的类型名：'" + type + "'");
                }
                rules[i] = new PolicyRule(type, ReadOps(sec, here + ".ops"));
            }
            return rules;
        }

        static string[] ReadOps(Dictionary<string, object> sec, string at)
        {
            if (!sec.ContainsKey("ops"))
            {
                throw Error("缺少 " + at);
            }
            string[] ops = Strings(sec, "ops", at);
            if (ops.Length == 0)
            {
                throw Error(at + " 不能是空数组");
            }
            for (int i = 0; i < ops.Length; i++)
            {
                if (ops[i] != "*" && Array.IndexOf(OpNames, ops[i]) < 0)
                {
                    throw Error(at + " 含未知操作 '" + ops[i] + "'；可用：* 或 " + string.Join("、", OpNames));
                }
            }
            return ops;
        }

        static int Clock(string text, string at)
        {
            Match m = ClockPattern.Match(text);
            if (!m.Success)
            {
                throw Error(at + " 必须是 HH:MM：'" + text + "'");
            }
            return int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture) * 3600
                + int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture) * 60;
        }

        static bool[] Days(string text, string at)
        {
            if (!DaysPattern.IsMatch(text))
            {
                throw Error(at + " 不合法：'" + text + "'（写成 1-5 或 1,3,5，1 是星期一）");
            }
            bool[] found = new bool[8];
            string[] parts = text.Split(',');
            for (int i = 0; i < parts.Length; i++)
            {
                int first = parts[i][0] - '0';
                int last = parts[i].Length > 1 ? parts[i][2] - '0' : first;
                if (last < first)
                {
                    throw Error(at + " 的范围 '" + parts[i] + "' 反了");
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
            return new InvalidOperationException("写入策略 " + message);
        }
    }
}
