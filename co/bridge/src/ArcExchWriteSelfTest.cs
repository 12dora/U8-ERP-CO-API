using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的汇率写入部分（ArcExchWrite、ArcExchSql、EaiDistribute）：只测纯逻辑（编码与字段校验、EAI 报文、
    // u8key 解析、浮动汇率日的写法、汇率比较、登录子系统、预演模式、权限登记），不连库、不建 COM。由 ArcGlSelfTest.Run 调用。
    internal static class ArcExchWriteSelfTest
    {
        const string Usd = "美元";

        public static void Run()
        {
            CheckKind();
            CheckEnvelope();
            CheckRejects();
            CheckHelpers();
        }

        static void CheckKind()
        {
            ArcKind k = ArcKind.Find(ArcExch.Name);
            Expect("exch writable", k != null && !k.ReadOnly && k.RoRead && k.NameTag == null
                && (!Paths.U8HomePresent || ArcMap.Of(k).Canon("adjust_rate") != null));
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = ArcExch.Name;
            Expect("exch sub create", ArcGlKinds.SubOf("create", body) == ArcGlKinds.WriteSub);
            Expect("exch sub update", ArcGlKinds.SubOf("update", body) == null);
            Expect("exch dry create", DryRunModes.Lookup("archives/create", ArcExch.Name, "archive_create", "") == "validate");
            Expect("exch dry update", DryRunModes.Lookup("archives/update", ArcExch.Name, "archive_update", "") == "rollback");
            Expect("exch dry delete", DryRunModes.Lookup("archives/delete", ArcExch.Name, "archive_delete", "") == "rollback");
            Expect("vencontact dry", DryRunModes.Lookup("archives/delete", ArcPartner.VendorContact, "archive_delete", "") == "rollback"
                && DryRunModes.Lookup("archives/create", ArcPartner.VendorContact, "archive_create", "") == "validate");
            string[] ops = new string[] { "create", "update", "delete" };
            for (int i = 0; i < ops.Length; i++)
            {
                Expect("exch write rule " + ops[i], PermRegistry.ForKey(ArcGuard.RuleKey(ArcExch.Name, ops[i])) != null);
            }
        }

        // 记账汇率 itype 2、date 是期间号；调整汇率 itype 3；浮动汇率 itype 1、date 是自然日期。
        static void CheckEnvelope()
        {
            ArcReq req = Req("create", Usd + ":2026:10", Fields("rate", 7.1));
            ExchKey key = ExchKey.Parse(req.Code, "code");
            List<ExchPlan> plans = ArcExchWrite.Plans(req, key);
            string xml = ArcExchWrite.Envelope(key, plans[0]);
            Expect("exch envelope", plans.Count == 1 && xml.IndexOf("roottag='currencyrate'", StringComparison.Ordinal) > 0
                && xml.IndexOf("proc='add'", StringComparison.Ordinal) > 0
                && xml.IndexOf("<currencyrate><name>美元</name><period>10</period><type>2</type><date>10</date><rate>7.1</rate></currencyrate>",
                    StringComparison.Ordinal) > 0);
            req = Req("create", Usd + ":2026:3", Fields("adjust_rate", "6.85"));
            key = ExchKey.Parse(req.Code, "code");
            Expect("exch adjust", ArcExchWrite.Plans(req, key)[0].Type == 3 && ArcExchWrite.Plans(req, key)[0].Date == "3");
            req = Req("create", Usd + ":2026:10:2026-10-05", Fields("rate", "7.12"));
            key = ExchKey.Parse(req.Code, "code");
            ExchPlan floating = ArcExchWrite.Plans(req, key)[0];
            Expect("exch floating", floating.Type == 1 && floating.Date == "2026-10-05" && floating.Value == "7.12");
            req = Req("update", Usd + ":2026:10", Fields("rate", 7.2, "adjust_rate", 7.3));
            Expect("exch update both", ArcExchWrite.Plans(req, ExchKey.Parse(req.Code, "code")).Count == 2);
            Req("delete", Usd + ":2026:10:5", null);
        }

        static void CheckRejects()
        {
            Rejected("exch create both", "create", Usd + ":2026:10", Fields("rate", 7.1, "adjust_rate", 7.0));
            Rejected("exch create none", "create", Usd + ":2026:10", Fields());
            Rejected("exch floating adjust", "update", Usd + ":2026:10:2026-10-05", Fields("adjust_rate", 7.0));
            Rejected("exch date code", "update", Usd + ":2026-10-05", Fields("rate", 7.0));
            Rejected("exch date code delete", "delete", Usd + ":2026-10-05", null);
            Rejected("exch day outside", "create", Usd + ":2026:10:2026-11-05", Fields("rate", 7.0));
            Rejected("exch day number", "create", Usd + ":2026:10:5", Fields("rate", 7.0));
            Rejected("exch zero", "create", Usd + ":2026:10", Fields("rate", 0));
            Rejected("exch text", "update", Usd + ":2026:10", Fields("rate", "abc"));
            Rejected("exch unknown", "update", Usd + ":2026:10", Fields("nflat", 7.0));
        }

        static void CheckHelpers()
        {
            string[] days = ArcExchSql.Days("2026-10-05");
            Expect("exch days", days[0] == "2026-10-05" && days[1] == "5" && days[2] == "05" && ArcExchSql.Days("5")[2] == "5");
            Expect("exch near", ArcExchSql.Near("7.0229999999999997", "7.023") && !ArcExchSql.Near("7.1", "7.2") && !ArcExchSql.Near(null, "1"));
            Expect("eai key", EaiDistribute.Key("<ufinterface><item key=\"x\" succeed=\"0\" dsc=\"OK\" u8key=\"S000100000001\"/></ufinterface>")
                == "S000100000001" && EaiDistribute.Key("not xml") == "" && EaiDistribute.Key(null) == "");
        }

        static void Rejected(string name, string op, string code, Dictionary<string, object> fields)
        {
            try
            {
                Req(op, code, fields);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static ArcReq Req(string op, string code, Dictionary<string, object> fields)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = ArcExch.Name;
            body["code"] = code;
            if (fields != null)
            {
                body["fields"] = fields;
            }
            return ArcReq.Parse(op, body);
        }

        static Dictionary<string, object> Fields(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
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
