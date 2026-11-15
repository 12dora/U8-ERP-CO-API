using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的总账基础档案写入部分（ArcGl）：只测纯逻辑（报文、登录子系统、引用 SQL 的参数个数，科目仍只读），不连库、不建 COM。
    internal static class ArcGlSelfTest
    {
        public static void Run()
        {
            CheckEnvelope();
            CheckSub();
            Expect("arc gl refs args", ArcGlRefs.ArgsMatch());
            // 汇率写入（ArcExchWrite）。
            ArcExchWriteSelfTest.Run();
        }

        // 币种按 <name> 发编码，fields 的 code 是币种符号；凭证类别按 <type> 发；值里的 & < > 转义。
        static void CheckEnvelope()
        {
            ArcReq req = new ArcReq();
            req.Kind = ArcKind.Find(ArcGlKinds.Currency);
            req.Code = "测试币";
            ArcBag bag = new ArcBag();
            bag.Put("code", "T&1");
            bag.Put("name", "不该再发");
            string xml = ArcGl.Envelope(req, "add", bag);
            Expect("arc gl currency root", xml.IndexOf("roottag='currency'", StringComparison.Ordinal) > 0
                && xml.IndexOf("proc='add'", StringComparison.Ordinal) > 0);
            Expect("arc gl currency key", xml.IndexOf("<currency><name>测试币</name><code>T&amp;1</code></currency>", StringComparison.Ordinal) > 0);
            req.Kind = ArcKind.Find(ArcGlKinds.Sign);
            req.Code = "CO";
            bag = new ArcBag();
            bag.Put("type_name", "a<b");
            xml = ArcGl.Envelope(req, "delete", bag);
            Expect("arc gl sign key", xml.IndexOf("<dsign><type>CO</type><type_name>a&lt;b</type_name></dsign>", StringComparison.Ordinal) > 0);
            Expect("arc gl kinds writable", ArcKind.Find("account").ReadOnly && !ArcKind.Find(ArcGlKinds.Sign).ReadOnly
                && ArcKind.Find(ArcGlKinds.Sign).RoRead && ArcKind.Find(ArcGlKinds.Currency).Blocked("name") && !ArcKind.Find(ArcGlKinds.Currency).Blocked("code")
                && ArcKind.Find(ArcGlKinds.Sign).Blocked("type"));
        }

        static void CheckSub()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = "voucher_sign";
            Expect("arc gl sub write", ArcGlKinds.SubOf("create", body) == ArcGlKinds.WriteSub);
            Expect("arc gl sub read", ArcGlKinds.SubOf("get", body) == null);
            Expect("arc gl sub sql", ArcGlKinds.SubOf("update", body) == null && ArcGlKinds.SubOf("delete", body) == null);
            body["archive"] = "account";
            Expect("arc gl sub other", ArcGlKinds.SubOf("delete", body) == null);
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
