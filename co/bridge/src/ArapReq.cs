using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 收付款单 48/49 在 Ap_CloseBill（AR48 收款单、AP49 付款单、AP48 供应商退款、AR49 客户退款），应收/应付单 R0/P0 在 Ap_Vouch。
    // cFlag 同时是 UFAPBO 的子系统。
    internal sealed class ArapSpec
    {
        public string Flag;
        public string VouchType;
        public bool Close;
        public string Detail;
        public string MendFlag;
    }

    internal sealed class ArapLine
    {
        public Dictionary<string, string> Fields;
        public decimal Amt;
        public decimal AmtF;
        public decimal TaxRate;
        public int Type;
        // 外币应收/应付单的行币种、行汇率（ArapLineFx；null 取表头）。修改时载入的行记 U8 行上的币种。
        // Orig：该行的表头币种原币，记本位币的行 AmtF 是本币，表头原币合计用它。
        public string Currency;
        public decimal Rate;
        public decimal Orig;
        // 本币由桥按 round(原币 × 汇率, 2) 算出（调用方没给本币），ArapLineFx 的尾差只并到这样的行。
        public bool Derived;
    }

    // 键一律小写。日期、币种、汇率、金额、税率、iType 已从字段表里取出，由桥写入。
    internal sealed class ArapInput
    {
        public Dictionary<string, string> Head;
        public List<ArapLine> Lines;
        public string Date;
        public string Currency;
        public decimal Rate;
        public decimal Sum;
        public decimal SumF;
        // 账套本位币（WorkContext.HomeCurrency）。null 表示登录前、还不知道，与本位币有关的校验留到登录后。
        public string Home;
    }

    internal static class ArapReq
    {
        public const string Rmb = "人民币";
        const decimal AmountMax = 1000000000000m;
        const decimal RateMax = 1000000m;
        internal const string CloseHead = "cdwcode,dvouchdate,cdeptcode,cperson,csscode,ccode,cexch_name,iexchrate,cdigest,"
            + "citem_class,citemcode,corderno,cbank,cbankaccount";
        internal const string CloseLine = "itype,ckm,iamt,iamt_f,cdepcode,cpersoncode,cxmclass,cxm,cmemo";
        internal const string VouchHead = "cdwcode,dvouchdate,cdeptcode,cperson,ccode,cexch_name,iexchrate,cdigest,"
            + "citem_class,citemcode,cpaycode,corderno";
        internal const string VouchLine = "ccode,iamount,iamount_f,iamt,iamt_f,itaxrate,cdeptcode,cperson,citem_class,"
            + "citemcode,cdigest";

        public static ArapSpec Spec(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            if (name == "ar_receipt")
            {
                return Make("AR", "48", true);
            }
            if (name == "ap_payment")
            {
                return Make("AP", "49", true);
            }
            // 供应商退款（应付的收款单 AP48）、客户退款（应收的付款单 AR49）：同一套收付款单组件，金额照样为正，
            // 往来明细的负数由 U8 审核时自己写。
            if (name == "ap_refund")
            {
                return Make("AP", "48", true);
            }
            if (name == "ar_refund")
            {
                return Make("AR", "49", true);
            }
            if (name == "ar_bill")
            {
                return Make("AR", "R0", false);
            }
            if (name == "ap_bill")
            {
                return Make("AP", "P0", false);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持");
        }

        static ArapSpec Make(string flag, string vouchType, bool close)
        {
            ArapSpec spec = new ArapSpec();
            spec.Flag = flag;
            spec.VouchType = vouchType;
            spec.Close = close;
            spec.Detail = flag == "AR" ? "Ar_Detail" : "Ap_Detail";
            spec.MendFlag = flag == "AR" ? "bflag_AR" : "bflag_AP";
            return spec;
        }

        // 登录前的校验（home 为 null）：只查与本位币无关的部分。登录后 ArapCo.Create 带上本位币再查一遍全部。
        // 登录日期缺省（Date 为空）由 ArapCo.Create 补。
        public static ArapInput CheckCreate(VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            return CheckCreate(kind, head, lines, null);
        }

        public static ArapInput CheckCreate(VoucherKind kind, Dictionary<string, object> head, object[] lines, string home)
        {
            ArapSpec spec = Spec(kind);
            if (head == null)
            {
                throw Bad("head 必须是 JSON 对象", "head");
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw Bad("lines 必须是 1 到 200 行", "lines");
            }
            ArapInput input = new ArapInput();
            input.Home = home;
            try
            {
                input.Head = Fields(head, spec.Close ? CloseHead : VouchHead, 1, 16);
                HeadBasics(spec, input);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            input.Lines = new List<ArapLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                ArapLine line = LineAt(spec, input, lines[i], i);
                input.Sum += line.Amt;
                input.SumF += line.AmtF;
                input.Lines.Add(line);
            }
            // 表头合计取各行之和；逐行取整会有累计差，不再对表头做汇率校验。
            return input;
        }

        static void HeadBasics(ArapSpec spec, ArapInput input)
        {
            Need(input.Head, "cdwcode", "cDwCode");
            Need(input.Head, "ccode", "cCode");
            if (spec.Close)
            {
                Need(input.Head, "csscode", "cSSCode");
            }
            input.Date = Take(input.Head, "dvouchdate");
            if (input.Date.Length > 0)
            {
                CheckDate(input.Date);
            }
            Currency(input);
        }

        // 币种不给取本位币。登录前不知道本位币，本位币 / 外币的判断都留到登录后。
        static void Currency(ArapInput input)
        {
            string name = Take(input.Head, "cexch_name");
            input.Currency = name.Length == 0 ? (input.Home ?? "") : name;
            string rate = Take(input.Head, "iexchrate");
            if (rate.Length == 0 && IsForeign(input))
            {
                throw Bad("外币必须给汇率", "iexchrate");
            }
            input.Rate = rate.Length == 0 ? 1m : Num(rate, "iExchRate");
            if (input.Rate <= 0 || input.Rate > RateMax)
            {
                throw Bad("iExchRate 必须大于 0 且不超过 1000000", "iexchrate");
            }
            if (IsHome(input) && input.Rate != 1m)
            {
                throw Bad(input.Currency + "单据的汇率必须是 1", "iexchrate");
            }
        }

        // 本位币单据、外币单据：只在知道本位币（登录后）才能判断，登录前两者都是 false。
        internal static bool IsHome(ArapInput input)
        {
            return input.Home != null && input.Currency == input.Home;
        }

        internal static bool IsForeign(ArapInput input)
        {
            return input.Home != null && input.Currency != input.Home;
        }

        // 行内校验的 field 相对该行（如 iamount），这里补上 lines.<下标>。
        static ArapLine LineAt(ArapSpec spec, ArapInput input, object raw, int i)
        {
            try
            {
                return LineFor(spec, input, raw);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, FieldPath.Item("lines", i));
            }
        }

        // 修改时新增的行（ArapEdit）：与新增同一套行校验。
        internal static ArapLine LineFor(ArapSpec spec, ArapInput input, object raw)
        {
            return spec.Close ? CloseRow(input, raw) : VouchRow(input, raw);
        }

        static ArapLine CloseRow(ArapInput input, object raw)
        {
            ArapLine line = new ArapLine();
            line.Fields = Fields(Row(raw), CloseLine, 22, 37);
            line.Type = LineType(Take(line.Fields, "itype"));
            Need(line.Fields, "ckm", "cKm");
            Amounts(input, line, Take(line.Fields, "iamt"), Take(line.Fields, "iamt_f"), "iAmt");
            return line;
        }

        // 应收/应付单表体金额列是 iAmount / iAmount_f；也收 iAmt / iAmt_f 的写法，两者只能填一个。
        static ArapLine VouchRow(ArapInput input, object raw)
        {
            ArapLine line = new ArapLine();
            line.Fields = Fields(Row(raw), VouchLine, 22, 37);
            Need(line.Fields, "ccode", "cCode");
            string local = OneOf(line.Fields, "iamount", "iamt");
            Amounts(input, line, local, OneOf(line.Fields, "iamount_f", "iamt_f"), "iAmount");
            line.TaxRate = TaxRate(Take(line.Fields, "itaxrate"));
            return line;
        }

        static Dictionary<string, object> Row(object raw)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", "lines 的每一行必须是对象");
            }
            return map;
        }

        internal static Dictionary<string, string> Fields(Dictionary<string, object> raw, string csv, int defineFrom, int defineTo)
        {
            Dictionary<string, string> clean = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, object> pair in raw)
            {
                string key = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                if (!Listed(key, csv) && !Span(key, "cdefine", defineFrom, defineTo))
                {
                    throw Bad("不能设置字段 " + pair.Key, pair.Key).WithHint(FieldPath.WritableHint);
                }
                if (clean.ContainsKey(key))
                {
                    throw Bad("字段重复 " + pair.Key, pair.Key);
                }
                string text = Cell(pair.Value);
                if (text != null && text.Trim().Length > 0)
                {
                    clean[key] = text.Trim();
                }
            }
            return clean;
        }

        static bool Listed(string key, string csv)
        {
            return key.Length > 0 && ("," + csv + ",").IndexOf("," + key + ",", StringComparison.Ordinal) >= 0;
        }

        // 序号不收前导零：cdefine01 不算 cdefine1。
        internal static bool Span(string key, string prefix, int from, int to)
        {
            if (key.Length <= prefix.Length || !key.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            string digits = key.Substring(prefix.Length);
            int n;
            if (digits[0] == '0' || !int.TryParse(digits, NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            return n >= from && n <= to;
        }

        static string Cell(object value)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            string text = value as string;
            if (text != null)
            {
                return text;
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            IFormattable fmt = value as IFormattable;
            if (fmt == null)
            {
                throw new BridgeException(400, "bad_request", "字段值类型不正确");
            }
            return fmt.ToString(null, CultureInfo.InvariantCulture);
        }

        static void Need(Dictionary<string, string> map, string key, string label)
        {
            if (!map.ContainsKey(key))
            {
                throw Bad("缺少字段 " + label, key);
            }
        }

        internal static string Take(Dictionary<string, string> map, string key)
        {
            string value;
            if (!map.TryGetValue(key, out value))
            {
                return "";
            }
            map.Remove(key);
            return value;
        }

        internal static string OneOf(Dictionary<string, string> map, string key, string alias)
        {
            string a = Take(map, key);
            string b = Take(map, alias);
            if (a.Length > 0 && b.Length > 0)
            {
                throw Bad(key + " 与 " + alias + " 只能填一个", key);
            }
            return a.Length > 0 ? a : b;
        }

        internal static void CheckDate(string text)
        {
            DateTime parsed;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw Bad("dVouchDate 必须是 yyyy-MM-dd", "dvouchdate");
            }
        }

        internal static int LineType(string text)
        {
            if (text.Length == 0 || text == "0")
            {
                return 0;
            }
            if (text == "1")
            {
                return 1;
            }
            throw Bad("iType 只能是 0（应收付款）或 1（预收付款）", "itype");
        }

        internal static decimal TaxRate(string text)
        {
            if (text.Length == 0)
            {
                return 0m;
            }
            decimal rate = Num(text, "iTaxRate");
            if (rate < 0 || rate > 100)
            {
                throw Bad("iTaxRate 必须在 0 到 100 之间", "itaxrate");
            }
            return rate;
        }

        static decimal Amount(string text, string label)
        {
            if (text.Length == 0)
            {
                throw Bad("每行必须填写 " + label, Low(label));
            }
            decimal value = Num(text, label);
            if (value <= 0 || value > AmountMax)
            {
                throw Bad(label + " 必须大于 0 且不超过 1000000000000", Low(label));
            }
            if (decimal.Round(value, 2) != value)
            {
                throw Bad(label + " 最多两位小数", Low(label));
            }
            return value;
        }

        // 本位币：本币必填，原币若给必须相等。外币：原币必填；本币不给就按 round(原币 × 汇率, 2) 算，
        // 给了就要求与之相差不超过 0.01。登录前（还不知道本位币）只查给出的金额本身。
        internal static void Amounts(ArapInput input, ArapLine line, string local, string foreign, string label)
        {
            if (input.Home == null)
            {
                PreLogin(line, local, foreign, label);
                return;
            }
            if (IsHome(input))
            {
                HomeAmounts(input, line, local, foreign, label);
                return;
            }
            if (foreign.Length == 0)
            {
                throw Bad("外币单据每行必须填写原币金额 " + label + "_f", Low(label + "_f"));
            }
            line.AmtF = Amount(foreign, label + "_f");
            line.Derived = local.Length == 0;
            if (local.Length == 0)
            {
                line.Amt = Local(line.AmtF, input.Rate);
                if (line.Amt <= 0 || line.Amt > AmountMax)
                {
                    throw Bad(label + " 折算后必须大于 0 且不超过 1000000000000", Low(label + "_f"));
                }
                return;
            }
            line.Amt = Amount(local, label);
            Near(line.Amt, line.AmtF, input.Rate, label);
        }

        // 本币、原币都没给：照旧 400「每行必须填写 x」（与 Amount 对空本币的用语、field 相同）。
        static void PreLogin(ArapLine line, string local, string foreign, string label)
        {
            if (local.Length == 0 && foreign.Length == 0)
            {
                throw Bad("每行必须填写 " + label, Low(label));
            }
            line.Amt = local.Length > 0 ? Amount(local, label) : 0m;
            line.AmtF = foreign.Length > 0 ? Amount(foreign, label + "_f") : 0m;
        }

        static void HomeAmounts(ArapInput input, ArapLine line, string local, string foreign, string label)
        {
            line.Amt = Amount(local, label);
            if (foreign.Length > 0 && Num(foreign, label + "_f") != line.Amt)
            {
                throw Bad(input.Currency + "单据的原币金额必须等于本币金额", Low(label + "_f"));
            }
            line.AmtF = line.Amt;
        }

        static decimal Local(decimal foreign, decimal rate)
        {
            return decimal.Round(foreign * rate, 2, MidpointRounding.AwayFromZero);
        }

        static void Near(decimal local, decimal foreign, decimal rate, string label)
        {
            if (Math.Abs(local - Local(foreign, rate)) > 0.01m)
            {
                throw Bad(label + " 与原币金额×汇率相差超过 0.01", Low(label));
            }
        }

        static decimal Num(string text, string label)
        {
            decimal value;
            if (!decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                throw Bad(label + " 必须是数字", Low(label));
            }
            return value;
        }

        static string Low(string label)
        {
            return FieldPath.Clean(label.ToLowerInvariant());
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }

        public static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }

        public static string Plain(decimal value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
