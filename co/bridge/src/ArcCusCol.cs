using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户字段修改（archives/customer_fields）的声明表里的一行：请求键（EAI 标签的小写写法）、Customer 列、取值种类、
    // 长度 / 范围、引用的档案、联动的列。解析、校验、SQL、响应都遍历 ArcCusCols.All，新增一列只加一行。
    internal sealed class ArcCusCol
    {
        // 取值种类：文本、引用档案编码、日期、0/1、小数、整数。
        internal const int KText = 0;
        internal const int KRef = 1;
        internal const int KDate = 2;
        internal const int KBit = 3;
        internal const int KNum = 4;
        internal const int KInt = 5;
        // 需要额外规则的键（其余都只按种类校验）。
        internal const string SpAbbr = "abbr";
        internal const string SpName = "name";
        internal const string SpHead = "head";
        internal const string SpVendor = "vendor";
        internal const string SpSa = "sa";
        internal const string SpDefine = "define";
        // 联动列在当前值表里的键后缀。
        internal const string PairSuffix = "_pair";

        public string Key;
        public string Column;
        public string Label;
        public int Kind;
        // 文本长度或引用编码的列宽。
        public int Max;
        // 写入时不能为空（包括只有空白）。
        public bool Required;
        // 引用编码在库里不分大小写：比较不分大小写，写入档案里存的写法。
        public bool IgnoreCase;
        // 引用的档案要求末级。
        public bool EndLevel;
        public bool HasRange;
        public bool HighOpen;
        public decimal Low;
        public decimal High;
        public string RefTable;
        public string RefCode;
        public string RefWhere;
        // 引用档案的停用日期列（登录日期不早于它即已停用）。
        public string RefStop;
        // 联动列（写本列时同时写）与它在引用档案里的取值列。
        public string PairColumn;
        public string PairFrom;
        public string Charset;
        public string CharsetNote;
        public string Special;
        // 校验时要一并读出当前值的其他键。
        public string[] Needs = new string[0];

        public static ArcCusCol Text(string key, string column, string label, int max)
        {
            ArcCusCol c = new ArcCusCol();
            c.Key = key;
            c.Column = column;
            c.Label = label;
            c.Kind = KText;
            c.Max = max;
            return c;
        }

        public static ArcCusCol Ref(string key, string column, string label, int max, string table, string code)
        {
            ArcCusCol c = Text(key, column, label, max);
            c.Kind = KRef;
            c.IgnoreCase = true;
            c.RefTable = table;
            c.RefCode = code;
            return c;
        }

        public static ArcCusCol Of(int kind, string key, string column, string label)
        {
            ArcCusCol c = Text(key, column, label, 0);
            c.Kind = kind;
            return c;
        }

        public ArcCusCol Req()
        {
            Required = true;
            return this;
        }

        public ArcCusCol Sp(string special)
        {
            Special = special;
            return this;
        }

        public ArcCusCol End(string flagColumn)
        {
            EndLevel = true;
            RefWhere = flagColumn + "=1";
            return this;
        }

        // 收付款协议：按 U8 的 iLZYJ 取值过滤，且不能已停用。
        public ArcCusCol Agree(string where)
        {
            RefWhere = where;
            RefStop = "dEDate";
            return this;
        }

        public ArcCusCol Pair(string column, string from)
        {
            PairColumn = column;
            PairFrom = from;
            return this;
        }

        public ArcCusCol Chars(string set, string note)
        {
            Charset = set;
            CharsetNote = note;
            return this;
        }

        public ArcCusCol Range(decimal low, decimal high, bool highOpen)
        {
            HasRange = true;
            Low = low;
            High = high;
            HighOpen = highOpen;
            return this;
        }

        public ArcCusCol Need(string key)
        {
            Needs = new string[] { key };
            return this;
        }
    }

    // 客户字段声明表（固定顺序：响应、SQL 都按它）。不在表里的键一律拒绝；不在表里的列（信用、统计、派生、开票单位、
    // 部门 / 专管业务员、可修订、GSP / 许可证）不进这条路由。
    internal static class ArcCusCols
    {
        internal static readonly ArcCusCol[] All = Build();
        internal static readonly string[] Keys = KeysOf(All);
        static readonly Dictionary<string, ArcCusCol> ByKey = Index(All);

        public static ArcCusCol Of(string key)
        {
            ArcCusCol col;
            return key != null && ByKey.TryGetValue(key, out col) ? col : null;
        }

        // 请求的键加上校验要一并读出的键，按声明表顺序。
        public static List<string> Loaded(ICollection<string> requested)
        {
            HashSet<string> want = new HashSet<string>(requested, StringComparer.Ordinal);
            foreach (string key in requested)
            {
                ArcCusCol col = Of(key);
                if (col != null)
                {
                    want.UnionWith(col.Needs);
                }
            }
            List<string> keys = new List<string>();
            for (int i = 0; i < All.Length; i++)
            {
                if (want.Contains(All[i].Key))
                {
                    keys.Add(All[i].Key);
                }
            }
            return keys;
        }

        static ArcCusCol[] Build()
        {
            List<ArcCusCol> t = new List<ArcCusCol>();
            Core(t);
            Contact(t);
            Bank(t);
            Archives(t);
            Protocols(t);
            Defines(t);
            Flags(t);
            return t.ToArray();
        }

        static string[] KeysOf(ArcCusCol[] cols)
        {
            string[] keys = new string[cols.Length];
            for (int i = 0; i < cols.Length; i++)
            {
                keys[i] = cols[i].Key;
            }
            return keys;
        }

        static Dictionary<string, ArcCusCol> Index(ArcCusCol[] cols)
        {
            Dictionary<string, ArcCusCol> map = new Dictionary<string, ArcCusCol>(StringComparer.Ordinal);
            for (int i = 0; i < cols.Length; i++)
            {
                map.Add(cols[i].Key, cols[i]);
            }
            return map;
        }

        static void Core(List<ArcCusCol> t)
        {
            t.Add(ArcCusCol.Text("abbrname", "cCusAbbName", "简称", 60).Req().Sp(ArcCusCol.SpAbbr));
            t.Add(ArcCusCol.Ref("district", "cDCCode", "地区分类", 12, "DistrictClass", "cDCCode").End("bDCEnd"));
            t.Add(ArcCusCol.Of(ArcCusCol.KDate, "end_date", "dEndDate", "停用日期").Need("seed_date"));
            t.Add(ArcCusCol.Ref("sa_protocol", "cCusSAProtocol", "销售默认收付款协议", 20, "AA_Agreement", "cCode")
                .Agree("iLZYJ=1").Sp(ArcCusCol.SpSa));
            t.Add(ArcCusCol.Text("name", "cCusName", "名称", 98).Req().Sp(ArcCusCol.SpName));
            t.Add(ArcCusCol.Text("tax_no", "cCusRegCode", "税号", 50));
            t.Add(ArcCusCol.Of(ArcCusCol.KDate, "seed_date", "dCusDevDate", "发展日期").Need("end_date"));
            t.Add(ArcCusCol.Ref("sort_code", "cCCCode", "客户分类", 12, "CustomerClass", "cCCCode").End("bCCEnd").Req());
            t.Add(ArcCusCol.Ref("ccustradeccode", "cCusTradeCCode", "所属行业", 12, "TradeClass", "cTradeCCode")
                .End("bTradeCEnd").Pair("cTrade", "cTradeCName"));
            t.Add(ArcCusCol.Ref("head_corp_code", "cCusHeadCode", "所属总公司", 20, "Customer", "cCusCode")
                .Sp(ArcCusCol.SpHead));
            t.Add(ArcCusCol.Ref("crelvendor", "cRelVendor", "对应供应商", 20, "Vendor", "cVenCode")
                .Sp(ArcCusCol.SpVendor));
        }

        static void Contact(List<ArcCusCol> t)
        {
            t.Add(ArcCusCol.Text("contact", "cCusPerson", "联系人", 50));
            t.Add(ArcCusCol.Text("phone", "cCusPhone", "电话", 100));
            t.Add(ArcCusCol.Text("mobile", "cCusHand", "手机", 100));
            t.Add(ArcCusCol.Text("fax", "cCusFax", "传真", 100));
            t.Add(ArcCusCol.Text("bp", "cCusBP", "呼机", 20));
            t.Add(ArcCusCol.Text("email", "cCusEmail", "邮箱", 100));
            t.Add(ArcCusCol.Text("address", "cCusAddress", "地址", 255));
            t.Add(ArcCusCol.Text("postcode", "cCusPostCode", "邮政编码", 20)
                .Chars("0123456789abcdefghijklmnopqrstuvwxyz-", "只能是数字、小写字母和减号"));
            t.Add(ArcCusCol.Text("legal_man", "cCusLPerson", "法人", 100));
            t.Add(ArcCusCol.Text("memo", "cMemo", "备注", 1000));
            t.Add(ArcCusCol.Text("devliver_site", "cCusOAddress", "发货地址", 255));
        }

        static void Bank(List<ArcCusCol> t)
        {
            t.Add(ArcCusCol.Text("bank_open", "cCusBank", "开户银行", 100));
            t.Add(ArcCusCol.Text("bank_acc_number", "cCusAccount", "银行账号", 50));
            t.Add(ArcCusCol.Ref("ccusbankcode", "cCusBankCode", "银行编码", 50, "AA_Bank", "cBankCode"));
        }

        static void Archives(List<ArcCusCol> t)
        {
            t.Add(ArcCusCol.Ref("pay_condition", "cCusPayCond", "付款条件", 20, "PayCondition", "cPayCode"));
            t.Add(ArcCusCol.Ref("ccussscode", "cCusSSCode", "结算方式", 20, "SettleStyle", "cSSCode"));
            t.Add(ArcCusCol.Ref("deliver_mode", "cCusOType", "发运方式", 10, "ShippingChoice", "cSCCode"));
            t.Add(ArcCusCol.Ref("deli_warehouse", "cCusWhCode", "发货仓库", 10, "Warehouse", "cWhCode"));
            t.Add(ArcCusCol.Ref("ccusexch_name", "cCusExch_name", "币种", 50, "foreigncurrency", "cexch_name").Req());
        }

        static void Protocols(List<ArcCusCol> t)
        {
            t.Add(ArcCusCol.Ref("ccusexprotocol", "cCusExProtocol", "协议", 20, "AA_Agreement", "cCode")
                .Agree("(iLZYJ=2 OR iLZYJ=3)"));
            t.Add(ArcCusCol.Ref("ccusotherprotocol", "cCusOtherProtocol", "协议", 20, "AA_Agreement", "cCode")
                .Agree("iLZYJ=5"));
            t.Add(ArcCusCol.Ref("ccuscmprotocol", "cCusCMProtocol", "协议", 20, "AA_Agreement", "cCode")
                .Agree("(iLZYJ=5 OR iLZYJ=6 OR iLZYJ=8 OR iLZYJ=9)"));
            t.Add(ArcCusCol.Ref("ccusimagentprotocol", "cCusImAgentProtocol", "协议", 20, "AA_Agreement", "cCode")
                .Agree("iLZYJ=12"));
        }

        // 自定义项 1–3 文本 20、4–6 文本 60、7–10 文本 120、11–12 整数、13–14 小数、15–16 日期（Customer 的列类型）；
        // 含义和档案约束取账套的客户自定义项设置（ArcCusDefine），这里不假设。
        static void Defines(List<ArcCusCol> t)
        {
            for (int i = 1; i <= 16; i++)
            {
                string n = i.ToString(System.Globalization.CultureInfo.InvariantCulture);
                string label = "自定义项" + n;
                ArcCusCol c;
                if (i <= 10)
                {
                    c = ArcCusCol.Text("self_define" + n, "cCusDefine" + n, label, i <= 3 ? 20 : (i <= 6 ? 60 : 120));
                }
                else
                {
                    int kind = i <= 12 ? ArcCusCol.KInt : (i <= 14 ? ArcCusCol.KNum : ArcCusCol.KDate);
                    c = ArcCusCol.Of(kind, "self_define" + n, "cCusDefine" + n, label);
                }
                t.Add(c.Sp(ArcCusCol.SpDefine));
            }
        }

        static void Flags(List<ArcCusCol> t)
        {
            t.Add(ArcCusCol.Of(ArcCusCol.KBit, "bcusdomestic", "bCusDomestic", "国内客户").Req());
            t.Add(ArcCusCol.Of(ArcCusCol.KBit, "bcusoverseas", "bCusOverseas", "国外客户").Req());
            t.Add(ArcCusCol.Of(ArcCusCol.KBit, "bserviceattribute", "bServiceAttribute", "标志位").Req());
            t.Add(ArcCusCol.Of(ArcCusCol.KBit, "brequestsign", "bRequestSign", "标志位").Req());
            t.Add(ArcCusCol.Of(ArcCusCol.KNum, "discount_rate", "iCusDisRate", "扣率").Range(0m, 100m, false));
            t.Add(ArcCusCol.Of(ArcCusCol.KNum, "fcusdiscountrate", "fCusDiscountRate", "折扣率").Range(0m, 100m, false));
            t.Add(ArcCusCol.Of(ArcCusCol.KNum, "fadvancepaymentratio", "fAdvancePaymentRatio", "预付款比例")
                .Range(0m, 100m, false));
            t.Add(ArcCusCol.Of(ArcCusCol.KNum, "icustaxrate", "iCusTaxRate", "税率").Range(0m, 100m, true));
        }
    }
}
