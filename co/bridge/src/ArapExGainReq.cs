using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // arap/exchange_gain（汇兑损益）与 arap/exchange_gain/cancel（取消汇兑损益）的请求。
    // Date 是登录日期，也是汇兑损益的登记日期（U8 界面的「计算日期」）。Rate 为 0 表示取该期的调整汇率。
    // 取消时 CancelNos 为空表示按登记日期 Date 取消全部未制单的汇兑损益。
    internal sealed class ExGainAsk
    {
        public bool Cancel;
        public string Flag;
        public string Date;
        public string Currency;
        public decimal Rate;
        public List<string> Partners = new List<string>();
        public bool SettleCleared = true;
        public List<string> CancelNos = new List<string>();
    }

    // 汇兑损益（U8 处理方式 9M）在登录前的校验（400）。第二级写入：应收、应付都只对配置为测试账套的账套开放
    // （TestGate，登录前、入队后各查一次，403 test_account_only）。
    // 新增字段：flag（AR / AP，也是登录子系统）、currency（外币名称，必填）、rate（可选，大于 0；缺省取该期 exch 的调整汇率）、
    // partners（可选，1 到 200 个客户 / 供应商编码；缺省全部）、settle_cleared（可选布尔，缺省 true：原币已结清只剩本币尾差的单据一并结清）。
    // 取消字段：flag、cancel_nos（可选，1 到 200 个处理号 SYRAR… / SYPAP…；缺省取消登记日期 date 当天全部未制单的汇兑损益）。
    // 处理在 ArapExGain / ArapExGainCancel。
    internal static class ArapExGainReq
    {
        public const string Path = "/u8co/v1/arap/exchange_gain";
        public const string CancelPath = "/u8co/v1/arap/exchange_gain/cancel";
        public const string Action = "exchange_gain";
        public const string CancelAction = "exchange_gain_cancel";
        public const string TestOnly = "汇兑损益只对配置为测试账套的账套开放";
        public const int MaxList = 200;
        const int CodeMax = 20;
        const int CurrencyMax = 20;
        const decimal RateMax = 1000000m;
        static readonly Regex CancelNo = new Regex("^SY(RAR|PAP)[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        // 登录前的字段名单（RequestsP4 的 P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] Spec = new string[] { Path, "flag", "currency", "rate", "partners", "settle_cleared" };
        internal static readonly string[] CancelSpec = new string[] { CancelPath, "flag", "cancel_nos" };

        // 汇兑损益（新增、取消，应收、应付都是）查测试账套名单。
        public static void TestGate(WorkItem item)
        {
            TestAccountGate.Require(item, TestOnly);
        }

        public static bool IsPath(string path)
        {
            return path == Path || path == CancelPath;
        }

        public static string ActionOf(string path)
        {
            return path == CancelPath ? CancelAction : Action;
        }

        public static ExGainAsk Parse(string path, Dictionary<string, object> body)
        {
            ExGainAsk ask = new ExGainAsk();
            ask.Cancel = path == CancelPath;
            ask.Flag = Requests.Field(body, "flag") as string;
            if (ask.Flag != "AR" && ask.Flag != "AP")
            {
                throw Bad("flag 只能是 AR 或 AP", "flag");
            }
            ask.Date = Requests.Field(body, "date") as string;
            if (ask.Cancel)
            {
                ask.CancelNos = Nos(Requests.Field(body, "cancel_nos"), ask.Flag);
                return ask;
            }
            ask.Currency = Currency(Requests.Field(body, "currency"));
            ask.Rate = Rate(Requests.Field(body, "rate"));
            ask.Partners = Codes(Requests.Field(body, "partners"));
            ask.SettleCleared = Flag(Requests.Field(body, "settle_cleared"), "settle_cleared", true);
            return ask;
        }

        static string Currency(object raw)
        {
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0)
            {
                throw Bad("缺少 currency（外币名称，如 美元）", "currency");
            }
            if (text.Length > CurrencyMax || HasControl(text))
            {
                throw Bad("currency 无效", "currency");
            }
            return text;
        }

        // 缺省或 null 为 0（取调整汇率）；给了就大于 0、不超过 1000000、最多 10 位小数。
        internal static decimal Rate(object raw)
        {
            if (raw == null)
            {
                return 0m;
            }
            decimal value;
            if (!ArapWriteoffReq.TryNum(raw, out value) || value <= 0 || value > RateMax || decimal.Round(value, 10) != value)
            {
                throw Bad("rate 必须大于 0、不超过 1000000、最多 10 位小数", "rate");
            }
            return value;
        }

        static bool Flag(object raw, string name, bool fallback)
        {
            if (raw == null)
            {
                return fallback;
            }
            if (!(raw is bool))
            {
                throw Bad(name + " 必须是布尔值", name);
            }
            return (bool)raw;
        }

        // 往来单位编码清单：缺省为空（全部）；给了就 1 到 200 个不重复的非空编码。
        internal static List<string> Codes(object raw)
        {
            List<string> list = new List<string>();
            if (raw == null)
            {
                return list;
            }
            IList items = ListOf(raw, "partners");
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < items.Count; i++)
            {
                string code = items[i] as string;
                code = code == null ? "" : code.Trim();
                if (code.Length == 0 || code.Length > CodeMax || HasControl(code))
                {
                    throw Bad("partners 里的编码无效", FieldPath.Item("partners", i));
                }
                if (!seen.Add(code))
                {
                    throw Bad("partners 里有重复的编码", FieldPath.Item("partners", i));
                }
                list.Add(code);
            }
            return list;
        }

        // 处理号清单：缺省为空（按登记日期）；给了就 1 到 200 个不重复的处理号，前缀与 flag 一致（SYRAR 应收、SYPAP 应付）。
        internal static List<string> Nos(object raw, string flag)
        {
            List<string> list = new List<string>();
            if (raw == null)
            {
                return list;
            }
            IList items = ListOf(raw, "cancel_nos");
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < items.Count; i++)
            {
                string no = items[i] as string;
                string at = FieldPath.Item("cancel_nos", i);
                if (no == null || !CancelNo.IsMatch(no))
                {
                    throw Bad("cancel_nos 里必须是 SYRAR 或 SYPAP 后接数字的处理号", at);
                }
                if (no.Substring(4, 1) != flag.Substring(1, 1))
                {
                    throw Bad("处理号与 flag 不一致（SYRAR 是应收、SYPAP 是应付）", at);
                }
                if (!seen.Add(no))
                {
                    throw Bad("cancel_nos 里有重复的处理号", at);
                }
                list.Add(no);
            }
            return list;
        }

        static IList ListOf(object raw, string name)
        {
            IList items = raw as IList;
            if (items == null || items.Count == 0 || items.Count > MaxList)
            {
                throw Bad(name + " 必须是 1 到 " + MaxList.ToString(CultureInfo.InvariantCulture) + " 项的数组", name);
            }
            return items;
        }

        static bool HasControl(string text)
        {
            foreach (char c in text)
            {
                if (char.IsControl(c))
                {
                    return true;
                }
            }
            return false;
        }

        // 中文说明（审计备注、日志）。
        public static string Title(ExGainAsk ask)
        {
            string side = ask.Flag == "AP" ? "应付" : "应收";
            if (!ask.Cancel)
            {
                return side + "汇兑损益 " + ask.Currency + " " + (ask.Date ?? "");
            }
            if (ask.CancelNos.Count == 0)
            {
                return side + "取消汇兑损益 " + (ask.Date ?? "");
            }
            return side + "取消汇兑损益 " + ask.CancelNos[0] + (ask.CancelNos.Count > 1 ? " 等 "
                + ask.CancelNos.Count.ToString(CultureInfo.InvariantCulture) + " 个" : "");
        }

        // 锁键："arap:writeoff:<AR|AP>"（与同一侧的核销、取消核销、自动核销串行：都改同一批单据的余额）和 "arap:exgain:<AR|AP>"。
        // 请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(string path, Dictionary<string, object> body)
        {
            try
            {
                string flag = Parse(path, body).Flag;
                return new string[] { "arap:writeoff:" + flag, "arap:exgain:" + flag };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
