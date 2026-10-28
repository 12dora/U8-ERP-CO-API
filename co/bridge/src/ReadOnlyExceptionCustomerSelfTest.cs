using System;

namespace U8Co
{
    // --selftest 的只读例外：客户新增（ReadOnlyExceptionSelfTest 调用）。op=create 的 archives 可列 customer
    // （ConfigRulesReadOnly.EligibleArchives），放行 archives/create 且请求体 archive=customer；
    // op=delete 不能列 customer（EligibleDelete 只有 district_class），archives/update 不能开放，
    // 直接构造的 delete + customer 规则同样不放行。不连库、不建 COM、不登录。
    internal static class ReadOnlyExceptionCustomerSelfTest
    {
        const string Listed = ReadOnlyGateSelfTest.Listed;
        const string Customer = ConfigRulesReadOnly.CustomerArchive;
        const string CreatePath = Requests.ArcRoot + "create";
        const string DeletePath = Requests.ArcRoot + "delete";
        const string UpdatePath = Requests.ArcRoot + "update";

        public static void Run()
        {
            CheckConfig();
            CheckGate();
            CheckPost();
        }

        static void CheckConfig()
        {
            string[] ro = new string[] { Listed };
            ReadOnlyExceptionRule[] rules = ReadOnlyExceptionSelfTest.Parse(
                ReadOnlyExceptionSelfTest.RuleOf("create", new object[] { Customer }), ro);
            ReadOnlyGateSelfTest.Expect("roex4 parse create customer", rules.Length == 1 && rules[0].Op == "create"
                && rules[0].Archives.Length == 1 && rules[0].Archives[0] == Customer);
            rules = ReadOnlyExceptionSelfTest.Parse(
                ReadOnlyExceptionSelfTest.RuleOf("create", new object[] { ArcKindRwMore.District, Customer }), ro);
            ReadOnlyGateSelfTest.Expect("roex4 parse create both", rules[0].Archives.Length == 2);
            ReadOnlyExceptionSelfTest.ExpectError("roex4 delete customer",
                ReadOnlyExceptionSelfTest.RuleOf("delete", new object[] { Customer }), ro, "op=delete");
            ReadOnlyExceptionSelfTest.ExpectError("roex4 delete both",
                ReadOnlyExceptionSelfTest.RuleOf("delete", new object[] { ArcKindRwMore.District, Customer }), ro, Customer);
            // update 不是可开放的 op：带不带 archives 都启动即报错。
            ReadOnlyExceptionSelfTest.ExpectError("roex4 update customer",
                ReadOnlyExceptionSelfTest.RuleOf("update", new object[] { Customer }), ro, "create");
            ReadOnlyExceptionSelfTest.ExpectError("roex4 update plain",
                ReadOnlyExceptionSelfTest.RuleOf("update", null), ro, "create");
            ReadOnlyGateSelfTest.Expect("roex4 eligible", Array.IndexOf(ConfigRulesReadOnly.Eligible("create"), Customer) >= 0
                && Array.IndexOf(ConfigRulesReadOnly.Eligible("delete"), Customer) < 0
                && ConfigRulesReadOnly.Eligible("update").Length == 0);
        }

        static void CheckGate()
        {
            BridgeConfig cfg = Cfg("create", Customer);
            ReadOnlyExceptionSelfTest.ExpectAdmit("roex4 admit create customer", cfg, CreatePath, "svc01", Customer);
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 create district not listed", cfg, CreatePath, "svc01", ArcKindRwMore.District);
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 create vendor", cfg, CreatePath, "svc01", "vendor");
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 create case", cfg, CreatePath, "svc01", "Customer");
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 update customer", cfg, UpdatePath, "svc01", Customer);
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 delete customer", cfg, DeletePath, "svc01", Customer);
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 other operator", cfg, CreatePath, "op001", Customer);
            // 规则绕过配置加载直接构造：delete + customer、update（带不带 archives）都不放行。
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 forged delete", Cfg("delete", Customer), DeletePath, "svc01", Customer);
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 forged update", Cfg("update", Customer), UpdatePath, "svc01", Customer);
            BridgeConfig bare = Cfg("update", Customer);
            bare.ReadOnlyExceptions[0].Archives = null;
            ReadOnlyExceptionSelfTest.ExpectRefused("roex4 forged update bare", bare, UpdatePath, "svc01", Customer);
        }

        static void CheckPost()
        {
            BridgeConfig cfg = Cfg("create", Customer);
            WorkItem ok = ReadOnlyExceptionSelfTest.Item(cfg, CreatePath, Customer);
            WriteClassGate.Post(ok);
            ReadOnlyGateSelfTest.Expect("roex4 post create customer", ok.Policy == ReadOnlyGate.ExceptionCode);
            string[] refused = new string[] { UpdatePath, DeletePath };
            for (int i = 0; i < refused.Length; i++)
            {
                WorkItem item = ReadOnlyExceptionSelfTest.Item(cfg, refused[i], Customer);
                string code = null;
                try
                {
                    WriteClassGate.Post(item);
                }
                catch (BridgeException ex)
                {
                    code = ex.Code;
                }
                ReadOnlyGateSelfTest.Expect("roex4 post refused " + refused[i],
                    code == ReadOnlyGate.Code && item.Policy == ReadOnlyGate.Code);
            }
        }

        // 一条 archives 规则：801 + svc01，op 与 archives 按参数（直接构造，不经配置加载）。
        static BridgeConfig Cfg(string op, string archive)
        {
            BridgeConfig cfg = ReadOnlyGateSelfTest.CfgEx();
            ReadOnlyExceptionRule rule = new ReadOnlyExceptionRule();
            rule.Accounts = new string[] { Listed };
            rule.Operators = new string[] { "svc01" };
            rule.Type = "archives";
            rule.Op = op;
            rule.Archives = new string[] { archive };
            cfg.ReadOnlyExceptions = new ReadOnlyExceptionRule[] { rule };
            return cfg;
        }
    }
}
