using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户字段声明表（ArcCusCols）：表的完整性、每种取值种类的合法 / 非法值、必填、区间、字符集、
    // 自定义项的列类型与账套设置、联动列、日期次序、SET 子句与读取表达式、响应只含请求的键。由 ArcCusFieldsSelfTest 调用。不连库。
    internal static class ArcCusColsSelfTest
    {
        public static void Run()
        {
            CheckTable();
            CheckText();
            CheckCode();
            CheckDateBitNumber();
            CheckDefines();
            CheckDefineSettings();
            CheckPair();
            CheckDates();
            CheckSql();
            CheckViews();
            CheckRefSql();
        }

        // 键全小写不重复、列不重复、必填的只有四个文本 / 编码键与四个 0/1 键；每个键都能被解析。
        static void CheckTable()
        {
            HashSet<string> keys = new HashSet<string>(StringComparer.Ordinal);
            HashSet<string> columns = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < ArcCusCols.All.Length; i++)
            {
                ArcCusCol col = ArcCusCols.All[i];
                Expect("table key lower " + col.Key, col.Key == col.Key.ToLowerInvariant());
                Expect("table key unique " + col.Key, keys.Add(col.Key));
                Expect("table column unique " + col.Column, columns.Add(col.Column));
                Expect("table of " + col.Key, ArcCusCols.Of(col.Key) == col);
            }
            Expect("table size", ArcCusCols.All.Length == 58 && ArcCusCols.Keys.Length == 58);
            Expect("table unknown", ArcCusCols.Of("cCusPerson") == null && ArcCusCols.Of(null) == null
                && ArcCusCols.Of("Contact") == null);
            // 不进本路由的列：信用、统计、派生、开票单位、部门 / 专管业务员、可修订、GSP。
            string[] kept = new string[] { "ccuscreditcompany", "icuscreline", "iarmoney", "ccusmnemcode", "cinvoicecompany",
                "ccusdepart", "ccusperson", "bshop", "bongpinstore", "pubufts", "ccuscode" };
            for (int i = 0; i < kept.Length; i++)
            {
                Expect("table keeps out " + kept[i], ArcCusCols.Of(kept[i]) == null);
            }
            Expect("loaded needs", ListOf(ArcCusCols.Loaded(new string[] { "end_date" })).Equals("end_date,seed_date")
                && ListOf(ArcCusCols.Loaded(new string[] { "contact" })).Equals("contact"));
        }

        static void CheckText()
        {
            Expect("text trim", Parse("contact", "  联系人甲 ") == "联系人甲");
            Expect("text clear", Parse("contact", "") == "" && Parse("bank_open", "   ") == "");
            Expect("text max", Parse("contact", new string('甲', 50)).Length == 50 && Parse("memo", new string('a', 1000)).Length == 1000);
            Bad("text long", "contact", new string('甲', 51));
            Bad("memo long", "memo", new string('a', 1001));
            Bad("text control", "phone", "1\t2");
            Bad("text del", "phone", "1\u007f" + "2");
            Bad("required empty", "name", "");
            Expect("required expect empty", ArcCusValue.Parse(ArcCusCols.Of("name"), "", "expect.name", true) == "");
            Expect("expect legacy", ArcCusValue.Parse(ArcCusCols.Of("postcode"), "AB 12", "expect.postcode", true) == "AB 12"
                && ArcCusValue.Parse(ArcCusCols.Of("discount_rate"), "120.123456789", "expect.discount_rate", true) == "120.12345679"
                && ArcCusValue.Parse(ArcCusCols.Of("seed_date"), "1800-01-01", "expect.seed_date", true) == "1800-01-01");
            Expect("postcode ok", Parse("postcode", "200000") == "200000" && Parse("postcode", "ab-12") == "ab-12");
            Bad("postcode upper", "postcode", "AB12");
            Bad("postcode space", "postcode", "20 00");
            Bad("postcode long", "postcode", new string('1', 21));
            Expect("postcode clear", Parse("postcode", "") == "");
        }

        static void CheckCode()
        {
            Expect("code plain", Parse("pay_condition", "01") == "01" && Parse("ccusbankcode", new string('A', 50)).Length == 50);
            Expect("code clear", Parse("pay_condition", "") == "" && Parse("head_corp_code", "") == "");
            Bad("code padded", "pay_condition", " 01");
            Bad("code padded end", "ccussscode", "01 ");
            Bad("code long", "deliver_mode", new string('A', 11));
            Bad("code long wh", "deli_warehouse", new string('A', 11));
            Bad("code long trade", "ccustradeccode", new string('A', 13));
            Bad("code control", "crelvendor", "S9\n01");
            Bad("code required empty", "sort_code", "");
            Bad("currency required empty", "ccusexch_name", "");
            Expect("code required value", Parse("ccusexch_name", "人民币") == "人民币");
            string[] protocols = new string[] { "ccusexprotocol", "ccusotherprotocol", "ccuscmprotocol", "ccusimagentprotocol" };
            for (int i = 0; i < protocols.Length; i++)
            {
                Expect("protocol max " + protocols[i], Parse(protocols[i], new string('A', 20)).Length == 20);
                Bad("protocol long " + protocols[i], protocols[i], new string('A', 21));
                Expect("protocol filter " + protocols[i], ArcCusCols.Of(protocols[i]).RefWhere != null
                    && ArcCusCols.Of(protocols[i]).RefStop == "dEDate");
            }
            Expect("sa filter", ArcCusCols.Of("sa_protocol").RefWhere == "iLZYJ=1" && ArcCusCols.Of("sa_protocol").Special == ArcCusCol.SpSa);
            Expect("end level", ArcCusCols.Of("sort_code").EndLevel && ArcCusCols.Of("ccustradeccode").EndLevel
                && ArcCusCols.Of("district").EndLevel && !ArcCusCols.Of("pay_condition").EndLevel);
        }

        static void CheckDateBitNumber()
        {
            Expect("date ok", Parse("seed_date", "2026-10-20") == "2026-10-20" && Parse("seed_date", "") == "");
            Bad("date slash", "seed_date", "2026/10/20");
            Bad("date old", "seed_date", "1899-12-31");
            Bad("date padded", "seed_date", " 2026-10-20");
            Expect("bit ok", Parse("bcusoverseas", "0") == "0" && Parse("brequestsign", "1") == "1");
            Bad("bit empty", "bcusdomestic", "");
            Bad("bit two", "bserviceattribute", "2");
            Bad("bit word", "bcusoverseas", "true");
            Expect("num canon", Parse("discount_rate", "5.50") == "5.5" && Parse("discount_rate", "0.0") == "0"
                && Parse("discount_rate", "-0") == "0" && Parse("discount_rate", "100") == "100");
            Expect("num clear", Parse("fcusdiscountrate", "") == "" && Parse("fadvancepaymentratio", " ") == "");
            Bad("num low", "discount_rate", "-0.1");
            Bad("num high", "fcusdiscountrate", "100.1");
            Bad("num exp", "discount_rate", "1e1");
            Bad("num digits", "discount_rate", "1.123456789");
            Bad("num word", "fadvancepaymentratio", "abc");
            Expect("tax open top", Parse("icustaxrate", "99.99") == "99.99");
            Bad("tax at top", "icustaxrate", "100");
            Expect("num from db", ArcCusValue.FromDb(ArcCusCols.Of("discount_rate"), "5.50000000") == "5.5"
                && ArcCusValue.FromDb(ArcCusCols.Of("self_define11"), "12") == "12"
                && ArcCusValue.FromDb(ArcCusCols.Of("contact"), "x") == "x");
        }

        // 自定义项 1–3 文本 20、4–6 文本 60、7–10 文本 120、11–12 整数、13–14 小数、15–16 日期（Customer 的列类型）。
        static void CheckDefines()
        {
            Expect("define max 1", Parse("self_define1", new string('a', 20)).Length == 20);
            Bad("define long 1", "self_define1", new string('a', 21));
            Expect("define max 4", Parse("self_define4", new string('a', 60)).Length == 60);
            Bad("define long 6", "self_define6", new string('a', 61));
            Expect("define max 7", Parse("self_define7", new string('a', 120)).Length == 120
                && Parse("self_define10", new string('a', 120)).Length == 120);
            Bad("define long 7", "self_define7", new string('a', 121));
            Bad("define long 10", "self_define10", new string('a', 121));
            Expect("define int", Parse("self_define11", "12") == "12" && Parse("self_define12", "-3") == "-3"
                && Parse("self_define12", "") == "");
            Bad("define int frac", "self_define11", "1.5");
            Bad("define int big", "self_define12", "2147483648");
            Expect("define num", Parse("self_define13", "1.25") == "1.25" && Parse("self_define14", "0.50") == "0.5");
            Bad("define num digits", "self_define13", "1.123456789");
            Expect("define date", Parse("self_define15", "2026-01-31") == "2026-01-31" && Parse("self_define16", "") == "");
            Bad("define date bad", "self_define15", "2026-02-30");
            for (int i = 1; i <= 16; i++)
            {
                ArcCusCol col = ArcCusCols.Of("self_define" + i.ToString(System.Globalization.CultureInfo.InvariantCulture));
                Expect("define column " + i, col != null && col.Column == "cCusDefine" + i.ToString(System.Globalization.CultureInfo.InvariantCulture)
                    && col.Special == ArcCusCol.SpDefine);
            }
        }

        // 账套的自定义项设置：文本取使用长度、设置的最大长度、列宽中最小的正数，定长必须等长，小数不超过设置的位数。
        static void CheckDefineSettings()
        {
            ArcCusCol text = ArcCusCols.Of("self_define4");
            SizeOk("size free", text, Rule(0, 0, false, -1), new string('a', 60));
            SizeOk("size len", text, Rule(10, 0, false, -1), new string('a', 10));
            SizeBad("size len over", text, Rule(10, 0, false, -1), new string('a', 11));
            SizeBad("size max over", text, Rule(0, 8, false, -1), new string('a', 9));
            SizeOk("size min of three", text, Rule(20, 8, false, -1), new string('a', 8));
            SizeOk("size fixed ok", text, Rule(6, 0, true, -1), "abcdef");
            SizeBad("size fixed short", text, Rule(6, 0, true, -1), "abc");
            ArcCusCol number = ArcCusCols.Of("self_define13");
            SizeOk("digits ok", number, Rule(0, 0, false, 2), "1.25");
            SizeBad("digits over", number, Rule(0, 0, false, 2), "1.255");
            SizeOk("digits unset", number, Rule(0, 0, false, -1), "1.12345678");
            SizeOk("digits int", ArcCusCols.Of("self_define11"), Rule(3, 0, true, 0), "12");
            Expect("cap", ArcCusDefine.Cap(60, 0) == 60 && ArcCusDefine.Cap(60, 10) == 10 && ArcCusDefine.Cap(0, 10) == 10
                && ArcCusDefine.Cap(5, 10) == 5);
            Expect("setting sql", ArcCusDefine.SettingSql.IndexOf("FROM UserDef_Base WHERE cClass=N'客户' AND cDicDbName=?", StringComparison.Ordinal) > 0
                && ArcCusDefine.ArchiveSql.IndexOf("FROM UserDefine WHERE cID=? AND cValue=?", StringComparison.Ordinal) > 0);
            string[] used = new string[] { "bCheckVal", "iDataSource", "cRelArchive", "bInput", "bFixLength" };
            for (int i = 0; i < used.Length; i++)
            {
                Expect("setting sql " + used[i], ArcCusDefine.SettingSql.IndexOf(used[i], StringComparison.Ordinal) > 0);
            }
            Expect("setting sql no auto archive", ArcCusDefine.SettingSql.IndexOf("bArchive", StringComparison.Ordinal) < 0);
        }

        // 所属行业带出行业名称：写入时同时写、清空时一起清；当前值里带联动值。
        static void CheckPair()
        {
            ArcCusCol col = ArcCusCols.Of("ccustradeccode");
            Expect("pair cols", col.PairColumn == "cTrade" && col.PairFrom == "cTradeCName");
            List<object> args = new List<object>();
            Dictionary<string, string> pairs = new Dictionary<string, string>(StringComparer.Ordinal);
            pairs["ccustradeccode"] = "行业甲";
            Expect("pair set", ArcCusFieldsSql.PairSet(col, pairs, args) == "cTrade=?" && args.Count == 1 && (string)args[0] == "行业甲");
            args.Clear();
            pairs["ccustradeccode"] = "";
            Expect("pair clear", ArcCusFieldsSql.PairSet(col, pairs, args) == "cTrade=NULL" && args.Count == 0);
            Expect("pair absent clears", ArcCusFieldsSql.PairSet(col, new Dictionary<string, string>(), args) == "cTrade=NULL");
            string sql = ArcCusFieldsSql.LockSqlOf(ArcCusCols.Loaded(new string[] { "ccustradeccode" }));
            Expect("pair lock", sql.IndexOf(" AS ccustradeccode_pair", StringComparison.Ordinal) > 0
                && sql.IndexOf("cTrade", StringComparison.Ordinal) > 0);
            Expect("ref sql pair", ArcCusRefs.RefSql(col, "bTradeCEnd=1").IndexOf("cTradeCName", StringComparison.Ordinal) > 0);
        }

        // 停用日期不早于发展日期（发展日期为空时不早于建档日期）；只在请求改了其中之一、结果与当前值不同时查。
        static void CheckDates()
        {
            ArcCusFieldsAsk ask = Ask("end_date", "2026-05-01", "seed_date", "2026-06-01");
            DateBad("dates end before seed", ask, Cur("2020-01-01", "2020-01-01"), "2020-01-01");
            ask = Ask("end_date", "2026-06-01", "seed_date", "2026-06-01");
            ArcCusCheck.CheckDates(ask, Cur("", ""), "2020-01-01");
            ask = Ask("end_date", "2026-05-01");
            ArcCusCheck.CheckDates(ask, Cur("", "2026-04-01"), "2020-01-01");
            DateBad("dates end before stored seed", ask, Cur("", "2026-06-01"), "2020-01-01");
            DateBad("dates end before created", ask, Cur("", ""), "2026-06-01");
            ArcCusCheck.CheckDates(Ask("end_date", ""), Cur("2026-05-01", "2026-06-01"), "2020-01-01");
            ArcCusCheck.CheckDates(Ask("end_date", "2026-05-01"), Cur("2026-05-01", "2026-06-01"), "2020-01-01");
            DateBad("dates seed after stored end", Ask("seed_date", "2026-07-01"), Cur("2026-05-01", ""), "2020-01-01");
            ArcCusCheck.CheckDates(Ask("seed_date", "2026-04-01"), Cur("2026-05-01", ""), "2020-01-01");
            ArcCusCheck.CheckDates(Ask("contact", "x"), new Dictionary<string, string>(), "2020-01-01");
        }

        static void CheckSql()
        {
            Expect("expr date", ArcCusFieldsSql.Expr(ArcCusCol.KDate, "dEndDate")
                == "ISNULL(CONVERT(varchar(10), dEndDate, 23),'')");
            Expect("expr bit", ArcCusFieldsSql.Expr(ArcCusCol.KBit, "bCusOverseas") == "ISNULL(CONVERT(varchar(1), bCusOverseas),'')");
            Expect("expr num", ArcCusFieldsSql.Expr(ArcCusCol.KNum, "iCusDisRate").IndexOf("decimal(38,8)", StringComparison.Ordinal) > 0);
            Expect("expr int", ArcCusFieldsSql.Expr(ArcCusCol.KInt, "cCusDefine11") == "ISNULL(CONVERT(varchar(20), cCusDefine11),'')");
            Expect("expr text", ArcCusFieldsSql.Expr(ArcCusCol.KText, "cMemo") == "LTRIM(RTRIM(ISNULL(cMemo,'')))");
            List<object> args = new List<object>();
            Expect("set text", ArcCusFieldsSql.SetOf("contact", "联系人甲", args) == "cCusPerson=?" && args.Count == 1);
            args.Clear();
            Expect("set bit", ArcCusFieldsSql.SetOf("bcusoverseas", "1", args) == "bCusOverseas=CONVERT(bit, ?)" && args.Count == 1);
            args.Clear();
            Expect("set num", ArcCusFieldsSql.SetOf("discount_rate", "5.5", args) == "iCusDisRate=CONVERT(float, ?)");
            args.Clear();
            Expect("set int", ArcCusFieldsSql.SetOf("self_define11", "7", args) == "cCusDefine11=CONVERT(int, ?)");
            args.Clear();
            Expect("set date", ArcCusFieldsSql.SetOf("seed_date", "2026-10-20", args)
                == "dCusDevDate=CONVERT(datetime, CONVERT(date, ?, 23))");
            args.Clear();
            Expect("set clear", ArcCusFieldsSql.SetOf("self_define15", "", args) == "cCusDefine15=NULL" && args.Count == 0);
            Expect("set clear bank", ArcCusFieldsSql.SetOf("bank_open", "", args) == "cCusBank=NULL");
        }

        // 响应的 before / after 与 detail.current 只含请求的键，按声明表顺序；空值是空串。
        static void CheckViews()
        {
            ArcCusFieldsAsk ask = Ask("contact", "联系人乙", "bank_open", "示例银行", "bcusoverseas", "1");
            Dictionary<string, string> cur = new Dictionary<string, string>(StringComparer.Ordinal);
            cur["contact"] = "联系人甲";
            cur["bank_open"] = "";
            cur["bcusoverseas"] = "0";
            Dictionary<string, object> body = ArcCusFields.OkBody(ask, cur, true);
            Dictionary<string, object> before = (Dictionary<string, object>)body["before"];
            Dictionary<string, object> after = (Dictionary<string, object>)body["after"];
            Expect("view keys", before.Count == 3 && after.Count == 3 && !before.ContainsKey("name"));
            Expect("view values", (string)before["bank_open"] == "" && (string)after["bank_open"] == "示例银行"
                && (string)after["bcusoverseas"] == "1" && (string)before["contact"] == "联系人甲");
            string order = string.Join(",", new List<string>(after.Keys).ToArray());
            Expect("view order", order == "contact,bank_open,bcusoverseas");
            Dictionary<string, object> dry = ArcCusFields.DryBody(ask, cur);
            Expect("dry changed", (bool)dry["changed"] && ((Dictionary<string, object>)dry["after"]).Count == 3);
            Expect("current keys", ArcCusFields.CurrentOf(cur, ask.Fields).Count == 3);
            ArcCusFieldsAsk same = Ask("contact", "联系人甲");
            Expect("no change", !ArcCusFields.Differs(cur, same.Fields));
            CheckGateNewKey(cur);
        }

        static void CheckGateNewKey(Dictionary<string, string> cur)
        {
            string gate = null;
            try
            {
                ArcCusFieldsSql.Gate(Ask2(Ask("bank_open", "x"), "bank_open", "别家银行"), cur);
            }
            catch (BridgeException ex)
            {
                gate = ex.Status == 409 && ex.Code == "state_mismatch" ? ex.Message : null;
            }
            Expect("gate new key", gate != null && gate.IndexOf("bank_open 当前是空", StringComparison.Ordinal) >= 0);
        }

        static void CheckRefSql()
        {
            string sql = ArcCusRefs.RefSql(ArcCusCols.Of("sort_code"), "bCCEnd=1");
            Expect("ref sql", sql == "SELECT LTRIM(RTRIM(cCCCode)) AS code FROM CustomerClass WHERE cCCCode=? AND bCCEnd=1");
            Expect("ref sql plain", ArcCusRefs.RefSql(ArcCusCols.Of("pay_condition"), null)
                == "SELECT LTRIM(RTRIM(cPayCode)) AS code FROM PayCondition WHERE cPayCode=?");
            Expect("ref sql stop", ArcCusRefs.RefSql(ArcCusCols.Of("ccusexprotocol"), "(iLZYJ=2 OR iLZYJ=3)")
                .IndexOf("AS ended", StringComparison.Ordinal) > 0);
            BridgeException ex = ArcCusRefs.Invalid(ArcCusCols.Of("sort_code"), "x");
            Expect("invalid code", ex.Status == 409 && ex.Code == "sort_code_invalid" && ex.Field == "fields.sort_code");
            Expect("missing end", ArcCusRefs.Missing(ArcCusCols.Of("sort_code")).IndexOf("末级", StringComparison.Ordinal) > 0
                && ArcCusRefs.Missing(ArcCusCols.Of("pay_condition")).IndexOf("末级", StringComparison.Ordinal) < 0);
            Expect("name taken", ArcCusCheck.NameTaken("示例").Code == "name_taken" && ArcCusCheck.NameTaken("示例").Status == 409
                && ArcCusCheck.NameFlagSql.IndexOf("RptCusName", StringComparison.Ordinal) > 0);
        }

        static string Parse(string key, string value)
        {
            return ArcCusValue.Parse(ArcCusCols.Of(key), value, "fields." + key, false);
        }

        static void Bad(string name, string key, string value)
        {
            try
            {
                Parse(key, value);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == "fields." + key);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static ArcCusDefineRule Rule(int len, int maxLen, bool fix, int digits)
        {
            return new ArcCusDefineRule(len, maxLen, fix, digits);
        }

        static void SizeOk(string name, ArcCusCol col, ArcCusDefineRule rule, string value)
        {
            try
            {
                ArcCusDefine.CheckSize(col, rule, value);
            }
            catch (BridgeException)
            {
                throw new InvalidOperationException(name);
            }
        }

        static void SizeBad(string name, ArcCusCol col, ArcCusDefineRule rule, string value)
        {
            try
            {
                ArcCusDefine.CheckSize(col, rule, value);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == "fields." + col.Key);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void DateBad(string name, ArcCusFieldsAsk ask, Dictionary<string, string> cur, string created)
        {
            try
            {
                ArcCusCheck.CheckDates(ask, cur, created);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field != null && ex.Field.StartsWith("fields.", StringComparison.Ordinal));
                return;
            }
            throw new InvalidOperationException(name);
        }

        // 当前值：end_date、seed_date（校验日期次序时一并读出）。
        static Dictionary<string, string> Cur(string end, string seed)
        {
            Dictionary<string, string> cur = new Dictionary<string, string>(StringComparer.Ordinal);
            cur["end_date"] = end;
            cur["seed_date"] = seed;
            return cur;
        }

        static ArcCusFieldsAsk Ask(params string[] pairs)
        {
            ArcCusFieldsAsk ask = new ArcCusFieldsAsk();
            ask.Code = "C900001";
            ask.Fields = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                ask.Fields[pairs[i]] = pairs[i + 1];
            }
            return ask;
        }

        // 给请求加上 expect（一个键）。
        static ArcCusFieldsAsk Ask2(ArcCusFieldsAsk ask, string key, string expect)
        {
            ask.Expect = new Dictionary<string, string>(StringComparer.Ordinal);
            ask.Expect[key] = expect;
            return ask;
        }

        static string ListOf(List<string> keys)
        {
            return string.Join(",", keys.ToArray());
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
