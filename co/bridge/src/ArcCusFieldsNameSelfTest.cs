using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户字段修改：名称 name（cCusName）、税号 tax_no（cCusRegCode）的解析与 SET 子句，
    // 409 state_mismatch 的 detail.current（customer_fields 与 customer_person），简称重复 409 abbrname_taken。
    // 由 ArcCusFieldsSelfTest 调用。不连库。
    internal static class ArcCusFieldsNameSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckRejects();
            CheckSet();
            CheckCurrent();
            CheckPersonCurrent();
            CheckAbbrTaken();
        }

        static void CheckParse()
        {
            ArcCusFieldsAsk ask = Ask(Fields("name", "  示例客户有限公司 ", "tax_no", " EXAMPLE-TAX-NO-001 "), null);
            Expect("name trimmed", ask.Fields["name"] == "示例客户有限公司" && ask.Fields["tax_no"] == "EXAMPLE-TAX-NO-001");
            ask = Ask(Fields("tax_no", ""), Fields("tax_no", "EXAMPLE-TAX-NO-001"));
            Expect("tax clear", ask.Fields["tax_no"] == "" && ask.Expect["tax_no"] == "EXAMPLE-TAX-NO-001");
            Expect("tax blank clears", Ask(Fields("tax_no", "   "), null).Fields["tax_no"] == "");
            Expect("name expect empty", Ask(Fields("name", "示例客户"), Fields("name", "")).Expect["name"] == "");
            Expect("name max", Ask(Fields("name", new string('名', 98)), null).Fields["name"].Length == 98);
            Expect("tax max", Ask(Fields("tax_no", new string('9', 50)), null).Fields["tax_no"].Length == 50);
            Expect("name keeps case", Ask(Fields("name", "Example Ltd"), null).Fields["name"] == "Example Ltd");
        }

        static void CheckRejects()
        {
            ArcCusFieldsSelfTest.Rejected("name empty", Body(Fields("name", ""), null), "fields.name");
            ArcCusFieldsSelfTest.Rejected("name blank", Body(Fields("name", "  "), null), "fields.name");
            ArcCusFieldsSelfTest.Rejected("name long", Body(Fields("name", new string('名', 99)), null), "fields.name");
            ArcCusFieldsSelfTest.Rejected("name control", Body(Fields("name", "示例\n客户"), null), "fields.name");
            ArcCusFieldsSelfTest.Rejected("tax long", Body(Fields("tax_no", new string('9', 51)), null), "fields.tax_no");
            ArcCusFieldsSelfTest.Rejected("tax control", Body(Fields("tax_no", "91\t00"), null), "fields.tax_no");
            ArcCusFieldsSelfTest.Rejected("expect name long",
                Body(Fields("name", "示例"), Fields("name", new string('名', 99))), "expect.name");
            ArcCusFieldsSelfTest.Rejected("expect tax not in fields",
                Body(Fields("name", "示例"), Fields("tax_no", "")), "expect.tax_no");
            ArcCusFieldsSelfTest.Rejected("name key case", Body(Fields("Name", "示例"), null), "fields.Name");
        }

        static void CheckSet()
        {
            List<object> args = new List<object>();
            Expect("set name", ArcCusFieldsSql.SetOf("name", "示例客户", args) == "cCusName=?"
                && args.Count == 1 && (string)args[0] == "示例客户");
            args.Clear();
            Expect("set tax", ArcCusFieldsSql.SetOf("tax_no", "EXAMPLE-TAX-NO-001", args) == "cCusRegCode=?" && args.Count == 1);
            args.Clear();
            Expect("set tax clear", ArcCusFieldsSql.SetOf("tax_no", "", args) == "cCusRegCode=NULL" && args.Count == 0);
            Expect("lock name", ArcCusFieldsSql.LockSql.IndexOf("LTRIM(RTRIM(ISNULL(cCusName,''))) AS name,", StringComparison.Ordinal) > 0
                && ArcCusFieldsSql.LockSql.IndexOf("LTRIM(RTRIM(ISNULL(cCusRegCode,''))) AS tax_no,", StringComparison.Ordinal) > 0);
        }

        // detail.current 只含请求 fields 的键（按 Keys 顺序），值是当前值，空为空串；消息照旧列出不符的键。
        static void CheckCurrent()
        {
            Dictionary<string, string> cur = ArcCusFieldsSelfTest.Cur("示例简称", "", "", "");
            BridgeException ex = GateError(Ask(Fields("name", "新名称", "tax_no", "91"), Fields("name", "旧名称")), cur);
            Expect("current 409", ex != null && ex.Status == 409 && ex.Code == "state_mismatch"
                && ex.Message.IndexOf("name 当前是示例客户有限公司", StringComparison.Ordinal) >= 0);
            Dictionary<string, object> current = CurrentOf(ex);
            Expect("current keys", current != null && current.Count == 2 && (string)current["name"] == "示例客户有限公司"
                && (string)current["tax_no"] == "");
            Expect("expect trimmed", GateError(Ask(Fields("name", "x"), Fields("name", "示例客户有限公司 ")), cur) == null
                && GateError(Ask(Fields("tax_no", "x"), Fields("tax_no", "")), cur) == null);
            cur["tax_no"] = "ab12";
            Expect("tax case sensitive", GateError(Ask(Fields("tax_no", "x"), Fields("tax_no", "AB12")), cur) != null);
            ex = GateError(Ask(Fields("district", "33"), Fields("district", "31")), cur);
            current = CurrentOf(ex);
            Expect("current district only", current != null && current.Count == 1 && (string)current["district"] == "");
            Expect("concurrent has no current", ArcCustomerRow.Concurrent("C900001").Detail == null);
        }

        static void CheckPersonCurrent()
        {
            ArcPersonAsk ask = new ArcPersonAsk();
            ask.Code = "C900001";
            ask.Person = "op002";
            ask.HasExpect = true;
            ask.Expect = "op001";
            ArcPersonSnap snap = new ArcPersonSnap();
            snap.Person = "";
            BridgeException ex = PersonError(ask, snap);
            Dictionary<string, object> current = CurrentOf(ex);
            Expect("person 409", ex != null && ex.Status == 409 && ex.Code == "state_mismatch" && ex.Message == "当前专管业务员是空");
            Expect("person current empty", current != null && current.Count == 1 && (string)current["person"] == "");
            snap.Person = "op003";
            current = CurrentOf(PersonError(ask, snap));
            Expect("person current", current != null && (string)current["person"] == "op003");
            ask.Expect = "OP003";
            Expect("person same", PersonError(ask, snap) == null);
        }

        static void CheckAbbrTaken()
        {
            BridgeException ex = ArcCusFieldsSql.AbbrTaken(Ask(Fields("abbrname", "示例简称"), null));
            Expect("abbr taken", ex.Status == 409 && ex.Code == "abbrname_taken" && ex.Field == "fields.abbrname"
                && ex.Message.IndexOf("示例简称", StringComparison.Ordinal) > 0);
            Expect("abbr taken sql", ArcCusFieldsSql.AbbrTakenSql.IndexOf("WHERE cCusAbbName=? AND cCusCode<>?", StringComparison.Ordinal) > 0);
            Exception dup = new InvalidOperationException("Cannot insert duplicate key row in object");
            Expect("duplicate key", ArcGl.DuplicateKey(dup) && !ArcGl.DuplicateKey(ex));
            Expect("dup with abbr", ArcCusFieldsSql.AbbrCollision(Ask(Fields("abbrname", "示例简称"), null), dup));
            Expect("dup without abbr", !ArcCusFieldsSql.AbbrCollision(Ask(Fields("name", "示例客户"), null), dup));
            Expect("abbr no dup", !ArcCusFieldsSql.AbbrCollision(Ask(Fields("abbrname", "示例简称"), null),
                new InvalidOperationException("超时")));
        }

        static BridgeException GateError(ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            try
            {
                ArcCusFieldsSql.Gate(ask, cur);
            }
            catch (BridgeException ex)
            {
                return ex;
            }
            return null;
        }

        static BridgeException PersonError(ArcPersonAsk ask, ArcPersonSnap snap)
        {
            try
            {
                ArcPersonSql.Gate(ask, snap);
            }
            catch (BridgeException ex)
            {
                return ex;
            }
            return null;
        }

        static Dictionary<string, object> CurrentOf(BridgeException ex)
        {
            if (ex == null || ex.Detail == null || !ex.Detail.ContainsKey("current"))
            {
                return null;
            }
            return ex.Detail["current"] as Dictionary<string, object>;
        }

        static ArcCusFieldsAsk Ask(Dictionary<string, object> fields, Dictionary<string, object> expect)
        {
            return ArcCusFieldsSelfTest.Ask(fields, expect);
        }

        static Dictionary<string, object> Body(Dictionary<string, object> fields, Dictionary<string, object> expect)
        {
            return ArcCusFieldsSelfTest.Body("C900001", fields, expect);
        }

        static Dictionary<string, object> Fields(params string[] pairs)
        {
            return ArcCusFieldsSelfTest.Fields(pairs);
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
