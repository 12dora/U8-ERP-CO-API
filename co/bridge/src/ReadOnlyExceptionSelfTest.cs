using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的只读例外泛化部分（ReadOnlyGate 的另一半，由 ReadOnlyGateSelfTest 调用）：
    // 三条客户专用路由 customer_person / customer_fields / customer_address 各自只放行自己的路径；
    // op=create / delete（ConfigRulesReadOnly.ArchiveOps）必须带 archives（每项须在 EligibleArchives 里，目前只有
    // district_class），只放行 archives/<op> 且请求体 archive 在规则里；archives 用在其它 op、未知档案类型启动即报错。
    // update、其它档案的 create / delete 在只读账套上仍 403。不连库、不建 COM、不登录。
    internal static class ReadOnlyExceptionSelfTest
    {
        const string Listed = ReadOnlyGateSelfTest.Listed;
        const string CreatePath = Requests.ArcRoot + "create";
        const string DeletePath = Requests.ArcRoot + "delete";

        public static void Run()
        {
            CheckConfig();
            CheckGate();
            CheckEmptyArchives();
            CheckPost();
            CheckHealth();
        }

        static void CheckConfig()
        {
            string[] ro = new string[] { Listed };
            for (int i = 0; i < ArcCustomerOps.Ops.Length; i++)
            {
                ReadOnlyExceptionRule[] rules = Parse(RuleOf(ArcCustomerOps.Ops[i], null), ro);
                ReadOnlyGateSelfTest.Expect("roex2 parse " + ArcCustomerOps.Ops[i],
                    rules.Length == 1 && rules[0].Op == ArcCustomerOps.Ops[i] && rules[0].Archives == null);
                ExpectError("roex2 archives on " + ArcCustomerOps.Ops[i],
                    RuleOf(ArcCustomerOps.Ops[i], new object[] { ArcKindRwMore.District }), ro, "只能用于");
            }
            for (int i = 0; i < ConfigRulesReadOnly.ArchiveOps.Length; i++)
            {
                CheckArchiveOp(ConfigRulesReadOnly.ArchiveOps[i], ro);
            }
            ExpectError("roex2 op update", RuleOf("update", null), ro, "customer_fields");
            ExpectError("roex2 op get", RuleOf("get", null), ro, "delete");
        }

        // create / delete 规则：archives 必填、非空、唯一、只收 district_class。
        static void CheckArchiveOp(string op, string[] ro)
        {
            ReadOnlyExceptionRule[] rules = Parse(RuleOf(op, new object[] { ArcKindRwMore.District }), ro);
            ReadOnlyGateSelfTest.Expect("roex2 parse " + op, rules[0].Op == op && rules[0].Archives.Length == 1
                && rules[0].Archives[0] == ArcKindRwMore.District);
            ExpectError("roex2 no archives " + op, RuleOf(op, null), ro, "缺少 archives");
            ExpectError("roex2 empty " + op, RuleOf(op, new object[0]), ro, "不能为空");
            ExpectError("roex2 other kind " + op, RuleOf(op, new object[] { "customer" }), ro, ArcKindRwMore.District);
            ExpectError("roex2 dup " + op,
                RuleOf(op, new object[] { ArcKindRwMore.District, ArcKindRwMore.District }), ro, "重复");
            ExpectError("roex2 not list " + op, RuleOf(op, "district_class"), ro, "字符串数组");
        }

        static void CheckGate()
        {
            BridgeConfig cfg = Cfg();
            for (int i = 0; i < ArcCustomerOps.Ops.Length; i++)
            {
                string path = Requests.ArcRoot + ArcCustomerOps.Ops[i];
                ExpectAdmit("roex2 admit " + ArcCustomerOps.Ops[i], cfg, path, "svc01", null);
                // 只有自己那条规则放行：只配这一条的配置里，另外两条路径仍拒绝。
                BridgeConfig only = Cfg(ArcCustomerOps.Ops[i]);
                for (int j = 0; j < ArcCustomerOps.Ops.Length; j++)
                {
                    string other = Requests.ArcRoot + ArcCustomerOps.Ops[j];
                    if (j == i)
                    {
                        ExpectAdmit("roex2 own " + other, only, other, "svc01", null);
                    }
                    else
                    {
                        ExpectRefused("roex2 foreign " + other, only, other, "svc01", null);
                    }
                }
            }
            ExpectAdmit("roex2 create district", cfg, CreatePath, "svc01", ArcKindRwMore.District);
            ExpectAdmit("roex2 delete district", cfg, DeletePath, "svc01", ArcKindRwMore.District);
            ExpectRefused("roex2 create customer", cfg, CreatePath, "svc01", "customer");
            ExpectRefused("roex2 delete customer", cfg, DeletePath, "svc01", "customer");
            ExpectRefused("roex2 delete no archive", cfg, DeletePath, "svc01", null);
            ExpectRefused("roex2 delete case", cfg, DeletePath, "svc01", "District_Class");
            // create 与 delete 各自独立：只配 create 的规则不放行 delete，反之亦然。
            ExpectRefused("roex2 delete needs rule", Cfg("create"), DeletePath, "svc01", ArcKindRwMore.District);
            ExpectRefused("roex2 create needs delete rule", Cfg("delete"), CreatePath, "svc01", ArcKindRwMore.District);
            ExpectRefused("roex2 create no archive", cfg, CreatePath, "svc01", null);
            ExpectRefused("roex2 create fa_card", cfg, CreatePath, "svc01", ArcFa.Name);
            ExpectRefused("roex2 create case", cfg, CreatePath, "svc01", "District_Class");
            ExpectRefused("roex2 update district", cfg, Requests.ArcRoot + "update", "svc01", ArcKindRwMore.District);
            ExpectRefused("roex2 other operator", cfg, CreatePath, "op001", ArcKindRwMore.District);
            ExpectRefused("roex2 other operator delete", cfg, DeletePath, "op001", ArcKindRwMore.District);
            ExpectRefused("roex2 other account", cfg, CreatePath, "svc01", ArcKindRwMore.District, ReadOnlyGateSelfTest.ExtraAcc);
            // 规则只写 customer_* 时 create / delete + district_class 仍拒绝（须有自己的规则）。
            ExpectRefused("roex2 create needs rule", Cfg("customer_fields"), CreatePath, "svc01", ArcKindRwMore.District);
        }

        // create / delete 规则的 archives 缺省或为空时不匹配任何请求（配置加载会拒绝，这里直接构造防御）。
        static void CheckEmptyArchives()
        {
            for (int i = 0; i < ConfigRulesReadOnly.ArchiveOps.Length; i++)
            {
                string op = ConfigRulesReadOnly.ArchiveOps[i];
                string path = Requests.ArcRoot + op;
                string[][] variants = new string[][] { null, new string[0] };
                for (int j = 0; j < variants.Length; j++)
                {
                    BridgeConfig cfg = Cfg();
                    ReadOnlyExceptionRule rule = new ReadOnlyExceptionRule();
                    rule.Accounts = new string[] { Listed };
                    rule.Operators = new string[] { "svc01" };
                    rule.Type = "archives";
                    rule.Op = op;
                    rule.Archives = variants[j];
                    cfg.ReadOnlyExceptions = new ReadOnlyExceptionRule[] { rule };
                    ExpectRefused("roex2 no archives " + op + j, cfg, path, "svc01", ArcKindRwMore.District);
                    ExpectRefused("roex2 no archives any " + op + j, cfg, path, "svc01", "customer");
                    ExpectRefused("roex2 no archives none " + op + j, cfg, path, "svc01", null);
                }
            }
        }

        static void CheckPost()
        {
            BridgeConfig cfg = Cfg();
            WorkItem ok = Item(cfg, CreatePath, ArcKindRwMore.District);
            WriteClassGate.Post(ok);
            ReadOnlyGateSelfTest.Expect("roex2 post create district", ok.Policy == ReadOnlyGate.ExceptionCode);
            WorkItem bad = Item(cfg, CreatePath, "customer");
            string code = null;
            try
            {
                WriteClassGate.Post(bad);
            }
            catch (BridgeException ex)
            {
                code = ex.Code;
            }
            ReadOnlyGateSelfTest.Expect("roex2 post create customer",
                code == ReadOnlyGate.Code && bad.Policy == ReadOnlyGate.Code);
            WorkItem del = Item(cfg, DeletePath, ArcKindRwMore.District);
            WriteClassGate.Post(del);
            ReadOnlyGateSelfTest.Expect("roex2 post delete district", del.Policy == ReadOnlyGate.ExceptionCode);
            WorkItem delBad = Item(cfg, DeletePath, "customer");
            code = null;
            try
            {
                WriteClassGate.Post(delBad);
            }
            catch (BridgeException ex)
            {
                code = ex.Code;
            }
            ReadOnlyGateSelfTest.Expect("roex2 post delete customer",
                code == ReadOnlyGate.Code && delBad.Policy == ReadOnlyGate.Code);
            for (int i = 0; i < ArcCustomerOps.Ops.Length; i++)
            {
                WorkItem item = Item(cfg, Requests.ArcRoot + ArcCustomerOps.Ops[i], null);
                WriteClassGate.Post(item);
                ReadOnlyGateSelfTest.Expect("roex2 post " + ArcCustomerOps.Ops[i],
                    item.Policy == ReadOnlyGate.ExceptionCode);
            }
        }

        static void CheckHealth()
        {
            try
            {
                ReadOnlyGate.Configure(Cfg());
                string json = ReadOnlyGate.HealthFragment();
                ReadOnlyGateSelfTest.Expect("roex2 health archives",
                    json.IndexOf("\"archives\":[\"" + ArcKindRwMore.District + "\"]", StringComparison.Ordinal) >= 0
                    && json.IndexOf("\"" + ArcCusFields.Op + "\"", StringComparison.Ordinal) >= 0
                    && json.IndexOf("\"" + ArcCusAddr.Op + "\"", StringComparison.Ordinal) >= 0);
                ReadOnlyGateSelfTest.Expect("roex2 health json",
                    new JavaScriptSerializer().DeserializeObject("{" + json.Substring(1) + "}") != null);
                object[] list = ReadOnlyGate.ExceptionSummaries();
                int withArchives = 0;
                for (int i = 0; i < list.Length; i++)
                {
                    if (((Dictionary<string, object>)list[i]).ContainsKey("archives"))
                    {
                        withArchives++;
                    }
                }
                ReadOnlyGateSelfTest.Expect("roex2 summaries", list.Length == 5 && withArchives == 2);
            }
            finally
            {
                ReadOnlyGate.Configure(null);
            }
        }

        // 配置：五条规则（三条客户专用路由 + create / delete 各限 district_class），都是 801 + svc01。
        static BridgeConfig Cfg()
        {
            List<string> ops = new List<string>(ArcCustomerOps.Ops);
            ops.AddRange(ConfigRulesReadOnly.ArchiveOps);
            return Cfg(ops.ToArray());
        }

        static BridgeConfig Cfg(params string[] ops)
        {
            BridgeConfig cfg = ReadOnlyGateSelfTest.CfgEx();
            cfg.ReadOnlyExceptions = new ReadOnlyExceptionRule[ops.Length];
            for (int i = 0; i < ops.Length; i++)
            {
                ReadOnlyExceptionRule rule = new ReadOnlyExceptionRule();
                rule.Accounts = new string[] { Listed };
                rule.Operators = new string[] { "svc01" };
                rule.Type = "archives";
                rule.Op = ops[i];
                rule.Archives = Array.IndexOf(ConfigRulesReadOnly.ArchiveOps, ops[i]) >= 0
                    ? new string[] { ArcKindRwMore.District } : null;
                cfg.ReadOnlyExceptions[i] = rule;
            }
            return cfg;
        }

        static Dictionary<string, object> RuleOf(string op, object archives)
        {
            Dictionary<string, object> rule = ReadOnlyGateSelfTest.RuleDict();
            rule["op"] = op;
            if (archives != null)
            {
                rule["archives"] = archives;
            }
            return rule;
        }

        static ReadOnlyExceptionRule[] Parse(Dictionary<string, object> rule, string[] ro)
        {
            return ConfigRulesReadOnly.ReadOnlyExceptions(ReadOnlyGateSelfTest.ExMap(new object[] { rule }), ro);
        }

        static void ExpectError(string name, Dictionary<string, object> rule, string[] ro, string text)
        {
            ReadOnlyGateSelfTest.ExpectConfigError(name, ReadOnlyGateSelfTest.ExMap(new object[] { rule }), ro, text);
        }

        static void ExpectAdmit(string name, BridgeConfig cfg, string path, string user, string archive)
        {
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, path, Body(Listed, user, archive), draft);
            ReadOnlyGateSelfTest.Expect(name, code == "bad_request" && draft.Policy == ReadOnlyGate.ExceptionCode);
        }

        static void ExpectRefused(string name, BridgeConfig cfg, string path, string user, string archive)
        {
            ExpectRefused(name, cfg, path, user, archive, Listed);
        }

        static void ExpectRefused(string name, BridgeConfig cfg, string path, string user, string archive, string acc)
        {
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, path, Body(acc, user, archive), draft);
            ReadOnlyGateSelfTest.Expect(name, code == ReadOnlyGate.Code && draft.Policy == ReadOnlyGate.Code);
        }

        // 口令故意写成解不开的值：放行时在解密处 400，拒绝发生在解密之前。
        static byte[] Body(string acc, string user, string archive)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["acc"] = acc;
            body["year"] = "2026";
            body["operator"] = user;
            body["date"] = "2026-01-15";
            body["password_enc"] = "not-a-ciphertext";
            body["code"] = "C900001";
            if (archive != null)
            {
                body["archive"] = archive;
            }
            return Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(body));
        }

        static WorkItem Item(BridgeConfig cfg, string path, string archive)
        {
            WorkItem item = new WorkItem();
            item.Config = cfg;
            item.Path = path;
            item.Acc = Listed;
            item.Operator = "svc01";
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["code"] = "C900001";
            if (archive != null)
            {
                body["archive"] = archive;
            }
            item.Body = body;
            return item;
        }
    }
}
