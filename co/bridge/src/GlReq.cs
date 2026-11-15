using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 总账凭证键：年度取登录年度，期间、类别、凭证号来自请求。
    internal sealed class GlKey
    {
        public int Year;
        public int Period;
        public string Sign;
        public int No;

        public string Text()
        {
            return Year.ToString(CultureInfo.InvariantCulture) + "年" + Period.ToString(CultureInfo.InvariantCulture)
                + "期 " + Sign + "-" + No.ToString(CultureInfo.InvariantCulture);
        }
    }

    internal sealed class GlFlow
    {
        public string Item;
        public decimal Debit;
        public decimal Credit;
    }

    internal sealed class GlLine
    {
        public string Account;
        public string Digest;
        public decimal Debit;
        public decimal Credit;
        public string Dept;
        public string Person;
        public string Customer;
        public string Supplier;
        public string ItemClass;
        public string Item;
        public string Settle;
        public string DocNo;
        public string DocDate;
        public string Currency;
        public decimal Rate;
        public decimal Qty;
        // 原币金额：0 表示按本币 / 汇率折算；红字冲销（GlReverse）取原凭证的原币取负，不重算。
        public decimal Fc;
        public List<GlFlow> Flows = new List<GlFlow>();
    }

    internal sealed class GlDraft
    {
        public string Sign;
        public string Date;
        public int Attachments;
        public List<GlLine> Lines = new List<GlLine>();
    }

    // 登录前的字段校验。只看形状和数值，科目、档案、期间在登录后查库。
    internal static class GlReq
    {
        const decimal AmountMax = 1000000000000m;
        static readonly string[] HeadNames = new string[] { "sign", "date", "attachments" };
        static readonly string[] LineNames = new string[]
        {
            "account", "digest", "debit", "credit", "dept", "person", "customer", "supplier", "item_class", "item",
            "settle", "doc_no", "doc_date", "currency", "rate", "qty", "cash_flow"
        };
        static readonly string[] FlowNames = new string[] { "item", "debit", "credit" };

        // 给 meta 路由用：表头、分录、现金流量项目的字段名副本。
        internal static string[][] MetaNames()
        {
            return new string[][] { (string[])HeadNames.Clone(), (string[])LineNames.Clone(), (string[])FlowNames.Clone() };
        }

        public static GlKey Key(Dictionary<string, object> body, int year)
        {
            GlKey key = new GlKey();
            key.Year = year;
            key.Period = IntIn(Field(body, "period"), "period", 1, 12);
            key.Sign = SignText(Field(body, "sign"), "sign");
            key.No = IntIn(Field(body, "no"), "no", 1, 32767);
            return key;
        }

        public static GlDraft Draft(Dictionary<string, object> body)
        {
            Dictionary<string, object> head = Field(body, "head") as Dictionary<string, object>;
            if (head == null)
            {
                throw Bad("head 必须是对象", "head");
            }
            Allow(head, HeadNames, "", "head");
            GlDraft draft = new GlDraft();
            draft.Sign = SignText(Field(head, "sign"), "head.sign");
            draft.Date = OptDate(Field(head, "date"), "head.date");
            object att = Field(head, "attachments");
            draft.Attachments = att == null ? 0 : IntIn(att, "head.attachments", 0, 32767);
            object[] lines = Field(body, "lines") as object[];
            if (lines == null || lines.Length < 2 || lines.Length > 200)
            {
                throw Bad("lines 必须是 2 到 200 行的数组", "lines");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                draft.Lines.Add(Line(lines[i], i + 1));
            }
            CheckBalance(draft);
            return draft;
        }

        // 行内校验给的 field 是相对行的（如 debit），这里补上 lines.<下标>。
        static GlLine Line(object raw, int index)
        {
            try
            {
                return LineBody(raw, index);
            }
            catch (BridgeException ex)
            {
                throw Under(ex, FieldPath.Item("lines", index - 1));
            }
        }

        static GlLine LineBody(object raw, int index)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            string at = "第 " + index.ToString(CultureInfo.InvariantCulture) + " 行";
            if (row == null)
            {
                throw Bad(at + "必须是对象");
            }
            Allow(row, LineNames, at, "");
            GlLine line = new GlLine();
            line.Account = Need(row, "account", 40, at);
            line.Digest = Need(row, "digest", 120, at);
            Side(row, at, out line.Debit, out line.Credit);
            line.Dept = Opt(row, "dept", 12, at);
            line.Person = Opt(row, "person", 20, at);
            line.Customer = Opt(row, "customer", 20, at);
            line.Supplier = Opt(row, "supplier", 20, at);
            line.ItemClass = Opt(row, "item_class", 2, at);
            line.Item = Opt(row, "item", 60, at);
            line.Settle = Opt(row, "settle", 3, at);
            line.DocNo = Opt(row, "doc_no", 30, at);
            line.DocDate = OptDate(Field(row, "doc_date"), at + " doc_date");
            Money(row, line, at);
            line.Flows = Flows(Field(row, "cash_flow"), at);
            return line;
        }

        static void Money(Dictionary<string, object> row, GlLine line, string at)
        {
            line.Currency = Opt(row, "currency", 8, at);
            object rate = Field(row, "rate");
            object qty = Field(row, "qty");
            line.Rate = rate == null ? 0 : Positive(rate, at + " rate", 6);
            line.Qty = qty == null ? 0 : Positive(qty, at + " qty", 6);
            if (line.Currency.Length > 0 && line.Rate <= 0)
            {
                throw Bad(at + "填了币种必须同时填写汇率 rate", "rate");
            }
            if (line.Currency.Length == 0 && line.Rate > 0)
            {
                throw Bad(at + "填了汇率必须同时填写币种 currency", "currency");
            }
        }

        static List<GlFlow> Flows(object raw, string at)
        {
            List<GlFlow> list = new List<GlFlow>();
            if (raw == null)
            {
                return list;
            }
            object[] items = raw as object[];
            if (items == null || items.Length > 50)
            {
                throw Bad(at + " cash_flow 必须是不超过 50 项的数组", "cash_flow");
            }
            for (int i = 0; i < items.Length; i++)
            {
                try
                {
                    list.Add(Flow(items[i], at));
                }
                catch (BridgeException ex)
                {
                    throw Under(ex, FieldPath.Item("cash_flow", i));
                }
            }
            return list;
        }

        static GlFlow Flow(object raw, string at)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw Bad(at + " cash_flow 的元素必须是对象");
            }
            Allow(map, FlowNames, at + " cash_flow", "");
            GlFlow flow = new GlFlow();
            flow.Item = Need(map, "item", 20, at + " cash_flow");
            Side(map, at + " cash_flow", out flow.Debit, out flow.Credit);
            return flow;
        }

        // 借贷只能填一边，且大于 0。
        static void Side(Dictionary<string, object> row, string at, out decimal debit, out decimal credit)
        {
            object d = Field(row, "debit");
            object c = Field(row, "credit");
            debit = d == null ? 0 : Amount(d, at + " debit");
            credit = c == null ? 0 : Amount(c, at + " credit");
            if ((debit > 0) == (credit > 0))
            {
                throw Bad(at + "借方 debit 和贷方 credit 必须且只能填一个大于 0 的金额", "debit");
            }
        }

        static void CheckBalance(GlDraft draft)
        {
            decimal debit = 0;
            decimal credit = 0;
            foreach (GlLine line in draft.Lines)
            {
                debit += line.Debit;
                credit += line.Credit;
            }
            if (debit <= 0 || credit <= 0 || debit != credit)
            {
                throw Bad("借贷不平：借方合计 " + debit.ToString("0.00", CultureInfo.InvariantCulture) + "，贷方合计 "
                    + credit.ToString("0.00", CultureInfo.InvariantCulture), "lines");
            }
        }

        static decimal Amount(object value, string label)
        {
            decimal amount;
            if (!TryNumber(value, out amount) || amount < 0 || amount > AmountMax)
            {
                throw Bad(label + " 必须是 0 到 1000000000000 之间的数", Tail(label));
            }
            if (decimal.Round(amount, 2) != amount)
            {
                throw Bad(label + " 最多两位小数", Tail(label));
            }
            return amount;
        }

        static decimal Positive(object value, string label, int digits)
        {
            decimal number;
            if (!TryNumber(value, out number) || number <= 0 || number > AmountMax)
            {
                throw Bad(label + " 必须是大于 0 且不超过 1000000000000 的数", Tail(label));
            }
            if (decimal.Round(number, digits) != number)
            {
                throw Bad(label + " 最多 " + digits.ToString(CultureInfo.InvariantCulture) + " 位小数", Tail(label));
            }
            return number;
        }

        internal static bool TryNumber(object value, out decimal number)
        {
            number = 0;
            try
            {
                if (value is int || value is long || value is decimal)
                {
                    number = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                    return true;
                }
                if (value is double)
                {
                    double d = (double)value;
                    if (double.IsNaN(d) || double.IsInfinity(d))
                    {
                        return false;
                    }
                    number = (decimal)d;
                    return true;
                }
            }
            catch (OverflowException)
            {
                return false;
            }
            return false;
        }

        internal static int IntIn(object value, string label, int min, int max)
        {
            long number;
            if (value is int)
            {
                number = (int)value;
            }
            else if (value is long)
            {
                number = (long)value;
            }
            else
            {
                throw Bad(label + " 必须是整数", Tail(label));
            }
            if (number < min || number > max)
            {
                throw Bad(label + " 必须在 " + min.ToString(CultureInfo.InvariantCulture) + " 到 "
                    + max.ToString(CultureInfo.InvariantCulture) + " 之间", Tail(label));
            }
            return (int)number;
        }

        internal static string SignText(object value, string label)
        {
            string text = value as string;
            text = text == null ? "" : text.Trim();
            if (text.Length < 1 || text.Length > 2 || !Clean(text))
            {
                throw Bad(label + " 必须是 1 到 2 个字的凭证类别", Tail(label));
            }
            return text;
        }

        internal static string OptDate(object value, string label)
        {
            if (value == null)
            {
                return "";
            }
            string text = value as string;
            DateTime parsed;
            if (text == null || !DateTime.TryParseExact(text.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out parsed))
            {
                throw Bad(label + " 必须是 yyyy-MM-dd", Tail(label));
            }
            return text.Trim();
        }

        internal static string OptText(Dictionary<string, object> map, string name, int max)
        {
            return Opt(map, name, max, "");
        }

        static string Need(Dictionary<string, object> map, string name, int max, string at)
        {
            string text = Opt(map, name, max, at);
            if (text.Length == 0)
            {
                throw Bad(at + " " + name + " 不能为空", name);
            }
            return text;
        }

        static string Opt(Dictionary<string, object> map, string name, int max, string at)
        {
            object raw = Field(map, name);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            if (text == null)
            {
                throw Bad(at + " " + name + " 必须是字符串", name);
            }
            text = text.Trim();
            if (text.Length > max || !Clean(text))
            {
                throw Bad(at + " " + name + " 过长或含控制字符（最多 " + max.ToString(CultureInfo.InvariantCulture) + " 字）", name);
            }
            return text;
        }

        static bool Clean(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return false;
                }
            }
            return true;
        }

        // path：field 前缀；行内传空串，由 Line / Flows 统一补下标。
        static void Allow(Dictionary<string, object> map, string[] names, string at, string path)
        {
            foreach (string key in map.Keys)
            {
                if (Array.IndexOf(names, key) < 0)
                {
                    throw Bad((at.Length > 0 ? at + " " : "") + "不能设置字段 " + key, FieldPath.Join(path, key));
                }
            }
        }

        internal static object Field(Dictionary<string, object> map, string name)
        {
            object value;
            if (map == null || !map.TryGetValue(name, out value))
            {
                return null;
            }
            return value;
        }

        internal static BridgeException Bad(string message)
        {
            return new BridgeException(400, "bad_request", message);
        }

        internal static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }

        // 给 400 的 field 加前缀（没有 field 时就用前缀本身），用于数组元素里的校验。
        internal static BridgeException Under(BridgeException ex, string at)
        {
            return FieldPath.Under(ex, at);
        }

        // 标签末段是 ASCII 字段路径时（如「第 2 行 debit」「head.sign」）作 field，否则不给。
        static string Tail(string label)
        {
            string text = label ?? "";
            int cut = text.LastIndexOf(' ');
            return FieldPath.Clean(cut < 0 ? text : text.Substring(cut + 1));
        }
    }
}
