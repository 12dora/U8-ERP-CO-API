using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户字段修改部分（archives/customer_fields）：请求解析与校验、expect 核对、SET 子句、
    // 无变化 / 预演 / 完成响应、共用 SQL 与客户专管业务员原语句一致、权限与写入分类、锁键、预演模式。不连库。
    internal static class ArcCusFieldsSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckParseRejects();
            CheckProtocolParse();
            CheckGate();
            CheckSet();
            CheckStopped();
            CheckBodies();
            CheckSql();
            CheckRoute();
            CheckLocks();
        }

        static void CheckParse()
        {
            ArcCusFieldsAsk ask = ArcCusFields.Parse(Body("C900001", Fields("abbrname", "  示例简称 ", "district", "33", "end_date", "2026-10-20"), null));
            Expect("parse fields", ask.Code == "C900001" && ask.Fields.Count == 3 && ask.Expect == null
                && ask.Fields["abbrname"] == "示例简称" && ask.Fields["district"] == "33"
                && ask.Fields["end_date"] == "2026-10-20");
            ask = ArcCusFields.Parse(Body("C900001", Fields("district", "", "end_date", ""),
                Fields("district", "33", "end_date", "")));
            Expect("parse clear", ask.Fields["district"] == "" && ask.Fields["end_date"] == ""
                && ask.Expect["district"] == "33" && ask.Expect["end_date"] == "" && ask.Expect.Count == 2);
            ask = ArcCusFields.Parse(Body("C900001", Fields("abbrname", "示例简称"), Fields("abbrname", " 旧简称 ")));
            Expect("parse expect trim", ask.Expect["abbrname"] == "旧简称");
            Dictionary<string, object> nil = Body("C900001", Fields("district", "33"), null);
            nil["expect"] = null;
            Expect("parse expect null", ArcCusFields.Parse(nil).Expect == null);
            Expect("parse expect empty abbr",
                ArcCusFields.Parse(Body("C900001", Fields("abbrname", "示例简称"), Fields("abbrname", ""))).Expect["abbrname"] == "");
            Expect("parse max abbr", ArcCusFields.Parse(
                Body("C900001", Fields("abbrname", new string('简', 60)), null)).Fields["abbrname"].Length == 60);
            Expect("parse max district", ArcCusFields.Parse(
                Body("C900001", Fields("district", new string('3', 12)), null)).Fields["district"].Length == 12);
        }

        static void CheckParseRejects()
        {
            Rejected("code missing", Body(null, Fields("district", "33"), null), "code");
            Rejected("fields missing", Body("C900001", null, null), "fields");
            Rejected("fields empty", Body("C900001", Fields(), null), "fields");
            Rejected("fields not object", WithRaw("fields", "x"), "fields");
            Rejected("unknown key", Body("C900001", Fields("name", "x"), null), "fields.name");
            Rejected("key case", Body("C900001", Fields("Abbrname", "x"), null), "fields.Abbrname");
            Rejected("abbr empty", Body("C900001", Fields("abbrname", "  "), null), "fields.abbrname");
            Rejected("abbr long", Body("C900001", Fields("abbrname", new string('简', 61)), null), "fields.abbrname");
            Rejected("abbr control", Body("C900001", Fields("abbrname", "a\tb"), null), "fields.abbrname");
            Rejected("district long", Body("C900001", Fields("district", new string('3', 13)), null), "fields.district");
            Rejected("district padded", Body("C900001", Fields("district", " 33"), null), "fields.district");
            Rejected("date slash", Body("C900001", Fields("end_date", "2026/10/20"), null), "fields.end_date");
            Rejected("date compact", Body("C900001", Fields("end_date", "20261020"), null), "fields.end_date");
            Rejected("date month", Body("C900001", Fields("end_date", "2026-13-01"), null), "fields.end_date");
            Rejected("date old", Body("C900001", Fields("end_date", "1899-12-31"), null), "fields.end_date");
            Rejected("date padded", Body("C900001", Fields("end_date", " 2026-10-20"), null), "fields.end_date");
            Dictionary<string, object> number = Fields();
            number["district"] = 33;
            Rejected("value not string", Body("C900001", number, null), "fields.district");
            Rejected("expect not object", WithRaw("expect", "x"), "expect");
            Rejected("expect extra key", Body("C900001", Fields("district", "33"), Fields("end_date", "")), "expect.end_date");
            Rejected("expect unknown key", Body("C900001", Fields("district", "33"), Fields("name", "")), "expect.name");
            Rejected("expect bad date", Body("C900001", Fields("end_date", ""), Fields("end_date", "x")), "expect.end_date");
            Dictionary<string, object> nullValue = Fields();
            nullValue["district"] = null;
            Rejected("expect null value", Body("C900001", Fields("district", "33"), nullValue), "expect.district");
        }

        // 收付款协议（sa_protocol）：自己的 1–20 上限（不走地区分类的 12），空串清空，前后空格 / 控制字符 / 非字符串 400。
        static void CheckProtocolParse()
        {
            ArcCusFieldsAsk ask = ArcCusFields.Parse(Body("C900001", Fields("sa_protocol", "A001"), Fields("sa_protocol", "")));
            Expect("parse protocol", ask.Fields.Count == 1 && ask.Fields["sa_protocol"] == "A001" && ask.Expect["sa_protocol"] == "");
            Expect("parse protocol clear", ArcCusFields.Parse(
                Body("C900001", Fields("sa_protocol", ""), Fields("sa_protocol", "A001"))).Fields["sa_protocol"] == "");
            Expect("parse max protocol", ArcCusFields.Parse(
                Body("C900001", Fields("sa_protocol", new string('A', 20)), null)).Fields["sa_protocol"].Length == 20);
            Expect("parse protocol over district max", ArcCusFields.Parse(
                Body("C900001", Fields("sa_protocol", new string('A', 13)), null)).Fields["sa_protocol"].Length == 13);
            Rejected("protocol long", Body("C900001", Fields("sa_protocol", new string('A', 21)), null), "fields.sa_protocol");
            Rejected("protocol lead space", Body("C900001", Fields("sa_protocol", " A001"), null), "fields.sa_protocol");
            Rejected("protocol trail space", Body("C900001", Fields("sa_protocol", "A001 "), null), "fields.sa_protocol");
            Rejected("protocol blank", Body("C900001", Fields("sa_protocol", "  "), null), "fields.sa_protocol");
            Rejected("protocol control", Body("C900001", Fields("sa_protocol", "A0\t01"), null), "fields.sa_protocol");
            Rejected("expect protocol long", Body("C900001", Fields("sa_protocol", "A001"),
                Fields("sa_protocol", new string('A', 21))), "expect.sa_protocol");
            Dictionary<string, object> number = Fields();
            number["sa_protocol"] = 1;
            Rejected("protocol not string", Body("C900001", number, null), "fields.sa_protocol");
            Rejected("protocol key case", Body("C900001", Fields("SA_PROTOCOL", "A001"), null), "fields.SA_PROTOCOL");
        }

        static void CheckGate()
        {
            Dictionary<string, string> cur = Cur("示例客户有限公司", "", "");
            ArcCusFieldsSql.Gate(Ask(Fields("abbrname", "示例简称", "district", "33"), null), cur);
            ArcCusFieldsSql.Gate(Ask(Fields("abbrname", "示例简称", "district", "33"),
                Fields("abbrname", "示例客户有限公司", "district", "")), cur);
            string message = Mismatch(Ask(Fields("abbrname", "示例简称", "district", "33"),
                Fields("abbrname", "别的名称", "district", "")), cur);
            Expect("gate field named", message != null && message.IndexOf("abbrname 当前是示例客户有限公司", StringComparison.Ordinal) >= 0
                && message.IndexOf("district", StringComparison.Ordinal) < 0);
            message = Mismatch(Ask(Fields("district", "33", "end_date", "2026-10-20"),
                Fields("district", "31", "end_date", "2026-10-19")), Cur("x", "", "2026-10-20"));
            Expect("gate lists all", message != null && message.IndexOf("district 当前是空", StringComparison.Ordinal) >= 0
                && message.IndexOf("end_date 当前是2026-10-20", StringComparison.Ordinal) >= 0);
            ArcCusFieldsSql.Gate(Ask(Fields("district", "33"), Fields("district", "a1")), Cur("x", "A1", ""));
            Expect("gate district ignores case", Mismatch(Ask(Fields("district", "33"), Fields("district", "b1")), Cur("x", "A1", "")) != null);
            Expect("gate case sensitive", Mismatch(Ask(Fields("abbrname", "y"), Fields("abbrname", "X")), Cur("x", "", "")) != null);
            ArcCusFieldsSql.Gate(Ask(Fields("sa_protocol", "A002"), Fields("sa_protocol", "a001")), Cur("x", "", "", "A001"));
            message = Mismatch(Ask(Fields("sa_protocol", "A002"), Fields("sa_protocol", "")), Cur("x", "", "", "A001"));
            Expect("gate protocol named", message != null && message.IndexOf("sa_protocol 当前是A001", StringComparison.Ordinal) >= 0);
        }

        static void CheckSet()
        {
            List<object> args = new List<object>();
            Expect("set abbr", ArcCusFieldsSql.SetOf("abbrname", "示例简称", args) == "cCusAbbName=?" && args.Count == 1);
            args.Clear();
            Expect("set district", ArcCusFieldsSql.SetOf("district", "33", args) == "cDCCode=?" && args.Count == 1);
            args.Clear();
            Expect("set district clear", ArcCusFieldsSql.SetOf("district", "", args) == "cDCCode=NULL" && args.Count == 0);
            Expect("set date", ArcCusFieldsSql.SetOf("end_date", "2026-10-20", args)
                == "dEndDate=CONVERT(datetime, CONVERT(date, ?, 23))" && args.Count == 1 && (string)args[0] == "2026-10-20");
            args.Clear();
            Expect("set date clear", ArcCusFieldsSql.SetOf("end_date", "", args) == "dEndDate=NULL" && args.Count == 0);
            CheckSetProtocol();
        }

        static void CheckSetProtocol()
        {
            List<object> args = new List<object>();
            // 闸门：sa_protocol 必须映射到 cCusSAProtocol，绝不能落到 dEndDate。
            Expect("set protocol", ArcCusFieldsSql.SetOf("sa_protocol", "A001", args) == "cCusSAProtocol=?"
                && args.Count == 1 && (string)args[0] == "A001");
            args.Clear();
            Expect("set protocol clear", ArcCusFieldsSql.SetOf("sa_protocol", "", args) == "cCusSAProtocol=NULL" && args.Count == 0);
            for (int i = 0; i < ArcCusFields.Keys.Length; i++)
            {
                string key = ArcCusFields.Keys[i];
                string set = ArcCusFieldsSql.SetOf(key, "", args);
                Expect("set mapped " + key, set.EndsWith("=NULL", StringComparison.Ordinal)
                    && (key == "end_date") == set.StartsWith("dEndDate", StringComparison.Ordinal));
            }
            Expect("set unknown", SetRejects("name") && SetRejects("Sa_Protocol") && args.Count == 0);
        }

        static bool SetRejects(string key)
        {
            try
            {
                ArcCusFieldsSql.SetOf(key, "x", new List<object>());
            }
            catch (BridgeException ex)
            {
                return ex.Status == 500;
            }
            return false;
        }

        // 协议停用：dEDate 非空且不晚于登录日期；登录日期未知或无停用日期不拦。
        static void CheckStopped()
        {
            Expect("stopped same day", ArcCusFieldsSql.Stopped("2026-10-20", "2026-10-20"));
            Expect("stopped before", ArcCusFieldsSql.Stopped("2026-10-19", "2026-10-20"));
            Expect("stopped later", !ArcCusFieldsSql.Stopped("2026-10-21", "2026-10-20"));
            Expect("stopped none", !ArcCusFieldsSql.Stopped("", "2026-10-20"));
            Expect("stopped no date", !ArcCusFieldsSql.Stopped("2026-10-19", "") && !ArcCusFieldsSql.Stopped("2026-10-19", null));
            Expect("protocol sql", ArcCusFieldsSql.ProtocolSql.IndexOf("FROM AA_Agreement WHERE cCode=?", StringComparison.Ordinal) > 0
                && ArcCusFieldsSql.ProtocolSql.IndexOf("dEDate", StringComparison.Ordinal) > 0);
        }

        static void CheckBodies()
        {
            ArcCusFieldsAsk ask = ArcCusFields.Parse(Body("C900001", Fields("district", "33"), null));
            Dictionary<string, string> cur = Cur("示例简称", "", "");
            Expect("differs", ArcCusFields.Differs(cur, ask.Fields));
            Expect("no differ", !ArcCusFields.Differs(Cur("示例简称", "33", ""), ask.Fields));
            CheckOk(ask, cur);
            CheckDry(ask, cur);
        }

        static void CheckOk(ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            Dictionary<string, object> body = ArcCusFields.OkBody(ask, cur, true);
            Dictionary<string, object> after = View(body, "after");
            Dictionary<string, object> before = View(body, "before");
            Expect("ok changed", (bool)body["changed"] && (string)body["code"] == "C900001");
            Expect("ok after", (string)after["district"] == "33" && (string)after["abbrname"] == "示例简称" && after.Count == 4
                && (string)after["sa_protocol"] == "");
            Expect("ok before", (string)before["district"] == "" && before.Count == 4 && before.ContainsKey("sa_protocol"));
            CheckOkProtocol();
            body = ArcCusFields.OkBody(ask, Cur("示例简称", "33", ""), false);
            Expect("ok unchanged", !(bool)body["changed"] && (string)View(body, "after")["district"] == "33"
                && (string)View(body, "after")["end_date"] == "");
        }

        static void CheckOkProtocol()
        {
            Dictionary<string, object> body = ArcCusFields.OkBody(ArcCusFields.Parse(Body("C900001", Fields("sa_protocol", ""), null)),
                Cur("示例简称", "", "", "A001"), true);
            Expect("ok protocol cleared", (string)View(body, "after")["sa_protocol"] == ""
                && (string)View(body, "before")["sa_protocol"] == "A001" && View(body, "after").Count == 4);
        }

        static void CheckDry(ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            Dictionary<string, object> body = ArcCusFields.DryBody(ask, cur);
            Expect("dry body", (bool)body["dry_run"] && (string)body["mode"] == DryRunModes.Validate && (bool)body["changed"]);
            Expect("dry view", (string)View(body, "after")["district"] == "33" && (string)View(body, "before")["district"] == "");
            Expect("dry body unchanged", !(bool)ArcCusFields.DryBody(ask, Cur("示例简称", "33", ""))["changed"]);
        }

        static Dictionary<string, object> View(Dictionary<string, object> body, string key)
        {
            return (Dictionary<string, object>)body[key];
        }

        // 共用 SQL 与客户专管业务员原来的语句逐字一致（外部行为不变）。
        static void CheckSql()
        {
            Expect("lock sql", ArcCustomerRow.LockSql("a AS x")
                == "SELECT a AS x, CONVERT(varchar(20), CONVERT(bigint, pubufts)) AS ts"
                + " FROM Customer WITH (UPDLOCK, ROWLOCK) WHERE cCusCode=?");
            Expect("update sql person", ArcCustomerRow.UpdateSql("cCusPPerson=?")
                == "UPDATE Customer SET cCusPPerson=?, cModifyPerson=?,"
                + " dModifyDate=CONVERT(datetime, CONVERT(date, ?, 23))"
                + " WHERE cCusCode=? AND pubufts=CONVERT(binary(8), CONVERT(bigint, ?))");
            Expect("update sql both", ArcCustomerRow.UpdateSql("cCusPPerson=?, cCusDepart=?")
                == "UPDATE Customer SET cCusPPerson=?, cCusDepart=?, cModifyPerson=?,"
                + " dModifyDate=CONVERT(datetime, CONVERT(date, ?, 23))"
                + " WHERE cCusCode=? AND pubufts=CONVERT(binary(8), CONVERT(bigint, ?))");
            for (int i = 0; i < ArcCusFields.Keys.Length; i++)
            {
                Expect("lock alias " + ArcCusFields.Keys[i],
                    ArcCusFieldsSql.LockSql.IndexOf(" AS " + ArcCusFields.Keys[i] + ",", StringComparison.Ordinal) > 0);
            }
            Expect("lock alias sa_protocol column", ArcCusFieldsSql.LockSql.IndexOf(
                "LTRIM(RTRIM(ISNULL(cCusSAProtocol,''))) AS sa_protocol,", StringComparison.Ordinal) > 0
                && ArcCusFields.Keys.Length == 4);
            Expect("same", ArcCustomerRow.Same(" A ", "a") && !ArcCustomerRow.Same("a", "b"));
            Expect("concurrent", ArcCustomerRow.Concurrent("C900001").Status == 409);
            Expect("unknown", ArcCustomerRow.Unknown("C900001", "字段").Status == 504);
        }

        static void CheckRoute()
        {
            Expect("rule", PermRegistry.ForKey(ArcCusFields.RuleKey) != null);
            Expect("rule key", ArcCusFields.RuleKey == ArcGuard.RuleKey("customer", ArcCusFields.Op));
            Expect("write gate", WriteGate.IsWrite(ArcCusFields.Path));
            WriteRequestInfo info = WriteClass.Of(ArcCusFields.Path, Body("C900001", Fields("district", "33"), null));
            Expect("write class", info != null && info.Type == "archives" && info.Op == "customer_fields");
            Expect("dry mode", DryRunModes.Lookup("archives/customer_fields", "", "archive_customer_fields", "")
                == DryRunModes.Validate);
            Expect("op name", WriteClass.IsOp("customer_fields"));
            Expect("customer op", ArcCustomerOps.Is("customer_fields") && ArcCustomerOps.IsPath(ArcCusFields.Path)
                && !ArcCustomerOps.Is("update") && !ArcCustomerOps.IsPath(Requests.ArcRoot + "update"));
            Expect("check route", CheckRejects(Body("C900001", Fields(), null)));
        }

        static bool CheckRejects(Dictionary<string, object> body)
        {
            try
            {
                ArcRoutes.Check(ArcCusFields.Op, body);
            }
            catch (BridgeException ex)
            {
                return ex.Status == 400;
            }
            return false;
        }

        static void CheckLocks()
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Config.SerializeWrites = false;
            item.Path = ArcCusFields.Path;
            item.Body = Body("c900001", Fields("district", "33"), null);
            string[] keys = DocLocks.KeysOf(item);
            Expect("lock customer", Array.IndexOf(keys, "arc:customer:C900001") >= 0);
            Expect("lock write", Array.IndexOf(keys, "arc:write") >= 0);
        }

        static Dictionary<string, string> Cur(string abbr, string district, string end)
        {
            return Cur(abbr, district, end, "");
        }

        static Dictionary<string, string> Cur(string abbr, string district, string end, string protocol)
        {
            Dictionary<string, string> cur = new Dictionary<string, string>(StringComparer.Ordinal);
            cur["abbrname"] = abbr;
            cur["district"] = district;
            cur["end_date"] = end;
            cur["sa_protocol"] = protocol;
            return cur;
        }

        static ArcCusFieldsAsk Ask(Dictionary<string, object> fields, Dictionary<string, object> expect)
        {
            return ArcCusFields.Parse(Body("C900001", fields, expect));
        }

        static string Mismatch(ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            try
            {
                ArcCusFieldsSql.Gate(ask, cur);
            }
            catch (BridgeException ex)
            {
                return ex.Status == 409 && ex.Code == "state_mismatch" ? ex.Message : null;
            }
            return null;
        }

        // 键值依次成对：Fields("a", "1", "b", "2")。
        static Dictionary<string, object> Fields(params string[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static Dictionary<string, object> Body(string code, Dictionary<string, object> fields, Dictionary<string, object> expect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (code != null)
            {
                body["code"] = code;
            }
            if (fields != null)
            {
                body["fields"] = fields;
            }
            if (expect != null)
            {
                body["expect"] = expect;
            }
            return body;
        }

        static Dictionary<string, object> WithRaw(string key, object value)
        {
            Dictionary<string, object> body = Body("C900001", Fields("district", "33"), null);
            body[key] = value;
            return body;
        }

        static void Rejected(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                ArcCusFields.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
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
