using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // arap/process/voucher 的请求：同一类处理（应收冲应付 9I、应付冲应收 9J、并账 BZ、汇兑损益 9M、红票对冲 9N）的 1 到 50 个批次号合成一张凭证。
    internal sealed class ProcVoucherAsk
    {
        public string Flag;
        // 处理类型（往来明细 cProcStyle）：9I / 9J / BZ / 9M / 9N，由批次号前缀定。
        public string Style;
        public List<string> CancelNos = new List<string>();
        // 凭证类别；空串表示缺省（SignOf：票据结算、贴现「收」，其余「转」）。
        public string Sign;
        // 制单日期；空串表示各批次处理日期中最晚的。
        public string Date;
        // 摘要；空串表示按往来明细（处理时写的摘要，没有就用处理类型名）。
        public string Digest;
        // 汇兑损益科目（只用于 9M，必填；U8 在制单界面选，桥不猜）。
        public string PlCode;
        // 贴现费用科目（只用于票据贴现 9D，可选；空串取基本科目设置 pjyfrzkm）。
        public string ExpenseCode = "";
        // 调用方指定的现金流量项目（科目编码 → 项目编码，ArapCashItems）；没给是空表。
        public CashItemMap CashItems = new CashItemMap();

        public bool ExchangeGain
        {
            get { return Style == "9M"; }
        }

        // 坏账处理（HZAR：计提 9F、发生 9G、收回 9H）。解析时 Style 是占位的 HZ，Prepare 按库识别后改成实际的处理方式
        // （ArapProcVoucherBad.Resolve）。第二级写入，只对测试账套开放。
        public bool Bad
        {
            get { return ArapProcVoucherReq.IsBadStyle(Style); }
        }

        // 应付票据的结算、退回（PJJAP / CLAP）：按应收票据推断、未经实测，第二级写入，只对测试账套开放。
        public bool ApNotes
        {
            get { return Notes && Flag == "AP"; }
        }

        // 票据处理（9A / 9D / 9E / 9C），凭证来源 PJ，分录另取 AP_Note_Sub（ArapProcVoucherNotes）。
        public bool Notes
        {
            get { return ArapProcVoucherReq.IsNoteStyle(Style); }
        }
    }

    // arap/process/voucher（处理制单）在登录前的校验（400）。字段 flag（AR / AP，也是登录子系统、凭证的制单系统）、
    // cancel_nos（批次号数组）、可选 sign、voucher_date、digest、cash_items（ArapCashItems），9M 另须 pl_code。请求里的 date 是登录日期（公共字段）。
    // 批次号前缀与处理类型、flag 的对应（Ap_Proc_CancelNo 的 cType + cFlag，同 U8）：
    // YCFAP 应收冲应付 9I（AR）、FCYAR 应付冲应收 9J（AP）、BZAR / BZAP 并账 BZ、SYRAR / SYPAP 汇兑损益 9M、HRAR / HPAP 红票对冲 9N；
    // 票据处理：PJJ 结算 9A、PJT 贴现 9D、PJB 背书 9E、CL 退回 9C，后接 AR；应付票据只开放 PJJAP、CLAP（第二级写入），
    // PJTAP、PJBAP 400。9D 可另给 expense_code。
    // 坏账处理：HZAR（计提 9F、发生 9G、收回 9H 共用编号，按库识别），coutsign JT。
    // 汇兑损益、坏账处理、应付票据是第二级写入（TestAccountGate），登录前查测试账套名单。处理在 ArapProcVoucher。
    internal static class ArapProcVoucherReq
    {
        public const string Path = "/u8co/v1/arap/process/voucher";
        public const string Action = "arap_process_voucher";
        public const int Max = 50;
        public const int DigestMax = 120;
        public const string TestOnly = "汇兑损益制单只对配置为测试账套的账套开放";
        public const string BadTestOnly = "坏账处理制单只对配置为测试账套的账套开放";
        public const string ApNotesTestOnly = "应付票据处理制单只对配置为测试账套的账套开放（应付票据写入只在测试账套上验证过）";
        internal static readonly string[] Spec = new string[] { Path, "flag", "cancel_nos", "sign", "voucher_date", "digest", "pl_code",
            "expense_code", "cash_items" };
        public static readonly string[] Styles = new string[] { "9I", "9J", "BZ", "9M", "9N", "9A", "9D", "9E", "9C", "9F", "9G", "9H" };
        static readonly string[] NoteStyles = new string[] { "9A", "9D", "9E", "9C" };
        // 坏账处理：HZ 是解析时的占位（批次号前缀 HZAR 分不出三种）。
        static readonly string[] BadStyles = new string[] { "HZ", "9F", "9G", "9H" };

        // 前缀、处理类型、flag。
        static readonly string[][] Prefixes = new string[][]
        {
            new string[] { "YCFAP", "9I", "AR" }, new string[] { "FCYAR", "9J", "AP" }, new string[] { "BZAR", "BZ", "AR" },
            new string[] { "BZAP", "BZ", "AP" }, new string[] { "SYRAR", "9M", "AR" }, new string[] { "SYPAP", "9M", "AP" },
            new string[] { "HRAR", "9N", "AR" }, new string[] { "HPAP", "9N", "AP" },
            new string[] { "PJJAR", "9A", "AR" }, new string[] { "PJTAR", "9D", "AR" }, new string[] { "PJBAR", "9E", "AR" },
            new string[] { "CLAR", "9C", "AR" }, new string[] { "PJJAP", "9A", "AP" }, new string[] { "PJTAP", "9D", "AP" },
            new string[] { "PJBAP", "9E", "AP" }, new string[] { "CLAP", "9C", "AP" },
            new string[] { "HZAR", "HZ", "AR" }
        };
        static readonly Regex NoShape = new Regex("^([A-Z]{4,5})[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        public static ProcVoucherAsk Parse(Dictionary<string, object> body)
        {
            ProcVoucherAsk ask = new ProcVoucherAsk();
            string flag = Requests.Field(body, "flag") as string;
            if (flag != "AR" && flag != "AP")
            {
                throw Bad("flag 只能是 AR 或 AP", "flag");
            }
            ask.Flag = flag;
            ask.CancelNos = Nos(Requests.Field(body, "cancel_nos"));
            ask.Style = StyleOf(ask.CancelNos[0]);
            for (int i = 0; i < ask.CancelNos.Count; i++)
            {
                string at = FieldPath.Item("cancel_nos", i);
                if (StyleOf(ask.CancelNos[i]) != ask.Style)
                {
                    throw Bad("一次只能给同一类处理的批次号（" + Title(ask.Style) + "）", at);
                }
                if (FlagOf(ask.CancelNos[i]) != flag)
                {
                    throw Bad("批次号与 flag 不一致（YCFAP、BZAR、SYRAR、HRAR、HZAR、PJJAR 等用 AR；FCYAR、BZAP、SYPAP、HPAP、PJJAP 等用 AP）", at);
                }
            }
            object sign = Requests.Field(body, "sign");
            ask.Sign = sign == null ? "" : GlReq.SignText(sign, "sign");
            ask.Date = GlReq.OptDate(Requests.Field(body, "voucher_date"), "voucher_date");
            ask.Digest = Digest(Requests.Field(body, "digest"));
            ask.PlCode = PlCode(Requests.Field(body, "pl_code"), ask.ExchangeGain);
            ask.ExpenseCode = ExpenseCode(Requests.Field(body, "expense_code"), ask.Style == "9D");
            ask.CashItems = ArapCashItems.Parse(Requests.Field(body, "cash_items"));
            return ask;
        }

        // 锁键："arap:proc:<批次号>"（与处理、取消处理共用）、"new:gl:<类别>"（与总账新增共用）、"arap:voucher:<AR|AP>"（与制单、取消制单共用）。
        // 请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            try
            {
                ProcVoucherAsk ask = Parse(body);
                List<string> keys = new List<string>();
                foreach (string no in ask.CancelNos)
                {
                    keys.Add("arap:proc:" + no);
                }
                keys.Add("new:gl:" + SignOf(ask));
                keys.Add("arap:voucher:" + ask.Flag);
                // 坏账处理制单回写坏账准备参数行（计提 9F），与 arap/bad_debt 共用 "arap:bad:AR"。
                if (ask.Bad)
                {
                    keys.Add("arap:bad:AR");
                }
                return keys.ToArray();
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        // 缺省凭证类别：应收票据结算（9A）、贴现（9D）借银行，用「收」；应付票据结算贷银行，用「付」（按同一规则推断）；
        // 其余（背书、退回、往来处理）用「转」（同 U8）。
        // 坏账处理一律「转」（三种共用 HZAR 前缀，登录前分不出；未经实测）；坏账收回借银行时可用 sign 指定「收」。
        // 类别不存在时 ArapProcVoucherPlan 409，请调用方用 sign 指定。
        public static string SignOf(ProcVoucherAsk ask)
        {
            if (ask.Sign.Length > 0)
            {
                return ask.Sign;
            }
            if (ask.Style == "9A" && ask.Flag == "AP")
            {
                return ArapVoucherRule.Pay;
            }
            return ask.Style == "9A" || ask.Style == "9D" ? ArapVoucherRule.Receive : ArapVoucherRule.Transfer;
        }

        // 凭证来源列 coutsign：应收冲应付 / 应付冲应收 ZZ，并账 BZ，汇兑损益 SY，票据处理 PJ（同 U8）；
        // 坏账处理 JT（coutsign，未经实测）。
        public static string OutSign(string style)
        {
            if (IsNoteStyle(style))
            {
                return "PJ";
            }
            if (IsBadStyle(style))
            {
                return ArapProcVoucherBad.OutSign;
            }
            switch (style)
            {
                case "BZ":
                    return "BZ";
                case "9M":
                    return "SY";
                default:
                    return "ZZ";
            }
        }

        // 红票对冲（9N）的 coutsign 是制单系统本身（U8 应收 9N 凭证 coutsign = AR；应付侧按同一规则推断为 AP，未经实测）。
        public static string OutSign(string style, string flag)
        {
            return style == ArapRedRule.Style ? flag : OutSign(style);
        }

        // 处理类型的中文名；不认识的取「汇兑损益」（9M）。
        static readonly Dictionary<string, string> Titles = BuildTitles();

        static Dictionary<string, string> BuildTitles()
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            map["9N"] = ArapRedRule.Title;
            map["9A"] = "票据结算";
            map["9D"] = "票据贴现";
            map["9E"] = "票据背书";
            map["9C"] = "票据退回";
            map["9I"] = "应收冲应付";
            map["9J"] = "应付冲应收";
            map["BZ"] = "并账";
            map["HZ"] = "坏账处理";
            map["9F"] = "计提坏账";
            map["9G"] = "坏账发生";
            map["9H"] = "坏账收回";
            return map;
        }

        public static string Title(string style)
        {
            string title;
            return style != null && Titles.TryGetValue(style, out title) ? title : "汇兑损益";
        }

        // 批次号 → 处理类型；不认识的前缀返回 null。
        public static string StyleOf(string no)
        {
            string[] hit = Prefix(no);
            return hit == null ? null : hit[1];
        }

        public static string FlagOf(string no)
        {
            string[] hit = Prefix(no);
            return hit == null ? null : hit[2];
        }

        static string[] Prefix(string no)
        {
            Match m = NoShape.Match(no ?? "");
            if (!m.Success)
            {
                return null;
            }
            foreach (string[] one in Prefixes)
            {
                if (one[0] == m.Groups[1].Value)
                {
                    return one;
                }
            }
            return null;
        }

        static List<string> Nos(object value)
        {
            IList list = value as IList;
            if (list == null || list.Count < 1 || list.Count > Max)
            {
                throw Bad("cancel_nos 必须是 1 到 " + Max.ToString(CultureInfo.InvariantCulture) + " 个批次号的数组", "cancel_nos");
            }
            List<string> nos = new List<string>();
            for (int i = 0; i < list.Count; i++)
            {
                string at = FieldPath.Item("cancel_nos", i);
                string no = list[i] as string;
                if (no == null || StyleOf(no) == null)
                {
                    throw Bad("批次号必须是 YCFAP、FCYAR、BZAR、BZAP、SYRAR、SYPAP、HRAR、HPAP、HZAR，或 PJJ / PJT / PJB / CL 加 AR / AP，"
                        + "后接数字（如 YCFAP000000000001、PJTAR000000000001）", at);
                }
                if (ApNoteRefused(no))
                {
                    throw Bad(NotesProcReq.ApNotesOps, at);
                }
                if (nos.Contains(no))
                {
                    throw Bad("cancel_nos 有重复的批次号", at);
                }
                nos.Add(no);
            }
            return nos;
        }

        // 应付票据只开放结算（PJJAP）、退回（CLAP）；贴现、背书（PJTAP、PJBAP）不开放。
        static bool ApNoteRefused(string no)
        {
            string style = StyleOf(no);
            return IsNoteStyle(style) && FlagOf(no) == "AP" && style != "9A" && style != "9C";
        }

        static string PlCode(object value, bool needed)
        {
            if (!needed)
            {
                if (value != null)
                {
                    throw Bad("pl_code 只用于汇兑损益（SYRAR / SYPAP）制单", "pl_code");
                }
                return "";
            }
            string text = value as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0)
            {
                throw Bad("汇兑损益制单必须给 pl_code（汇兑损益科目编码，如 660399）", "pl_code");
            }
            if (text.Length > 40 || !Clean(text, false))
            {
                throw Bad("pl_code 必须是不超过 40 位、不含空白的科目编码", "pl_code");
            }
            return text;
        }

        // 贴现费用科目：只用于票据贴现（9D），可省略（取基本科目设置）；给了必须是不超过 40 位、不含空白的科目编码。
        static string ExpenseCode(object value, bool allowed)
        {
            if (value == null)
            {
                return "";
            }
            if (!allowed)
            {
                throw Bad("expense_code 只用于票据贴现（PJTAR / PJTAP）制单", "expense_code");
            }
            string text = value as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 || text.Length > 40 || !Clean(text, false))
            {
                throw Bad("expense_code 必须是不超过 40 位、不含空白的科目编码", "expense_code");
            }
            return text;
        }

        static string Digest(object value)
        {
            if (value == null)
            {
                return "";
            }
            string text = value as string;
            if (text == null)
            {
                throw Bad("digest 必须是字符串", "digest");
            }
            text = text.Trim();
            if (text.Length > DigestMax)
            {
                throw Bad("digest 最多 " + DigestMax.ToString(CultureInfo.InvariantCulture) + " 字", "digest");
            }
            if (!Clean(text, true))
            {
                throw Bad("digest 不能含控制字符", "digest");
            }
            return text;
        }

        // spaces：是否允许普通空格（摘要允许，科目编码不允许）。
        static bool Clean(string text, bool spaces)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]) || (!spaces && char.IsWhiteSpace(text[i])))
                {
                    return false;
                }
            }
            return true;
        }

        internal static bool IsStyle(string style)
        {
            return Array.IndexOf(Styles, style) >= 0;
        }

        internal static bool IsNoteStyle(string style)
        {
            return Array.IndexOf(NoteStyles, style) >= 0;
        }

        internal static bool IsBadStyle(string style)
        {
            return Array.IndexOf(BadStyles, style) >= 0;
        }

        // 第二级写入（汇兑损益、坏账处理、应付票据）登录前、入队后各查一次测试账套名单；其余处理什么也不做。
        public static void TestGate(WorkItem item, ProcVoucherAsk ask)
        {
            if (ask.ExchangeGain)
            {
                TestAccountGate.Require(item, TestOnly);
            }
            if (ask.Bad)
            {
                TestAccountGate.Require(item, BadTestOnly);
            }
            if (ask.ApNotes)
            {
                TestAccountGate.Require(item, ApNotesTestOnly);
            }
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
