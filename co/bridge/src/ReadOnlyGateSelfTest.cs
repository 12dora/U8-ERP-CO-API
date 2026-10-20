using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的只读账套部分（ReadOnlyGate）：走 Requests.ForPath 的真实解析。名单里的账套对 WriteGate 登记的
    // 每条写路由（枚举登记表，新增写路由自动覆盖）都 403 account_read_only，含预演，写入策略放行、测试账套名单也不放行；
    // 拒绝发生在解密之前（口令故意写成解不开的值，放行时是 400 口令无法解密）。名单外账套不受影响，读路由照常通过。
    // readOnlyExceptions 只放行规则指定的 archives/<op>（审计 policy 记 read_only_exception）；
    // archives/update、新增、删除、其它操作员、其它账套、vouchers/update 伪造成 archives 仍拒绝。
    // 三条客户专用路由与 op=create / delete + archives 的规则见 ReadOnlyExceptionSelfTest（由 CheckExceptionGate 调用）。
    // 写入策略在放行之后仍判定。配置校验错误在加载时抛出。不连库、不建 COM、不登录。
    // 由 SelfTest.RunWritePolicy 调用。
    internal static class ReadOnlyGateSelfTest
    {
        internal const string Listed = "801";
        const string Other = "802";
        internal const string ExtraAcc = "803";
        const string ArcUpdate = Requests.ArcRoot + "update";
        const string VoucherUpdate = "/u8co/v1/vouchers/update";

        public static void Run()
        {
            CheckConfig();
            CheckExceptionConfig();
            CheckHealth();
            CheckExceptionGate();
            WritePolicy.ResetForTest();
            try
            {
                CheckWrites();
                WritePolicy.Apply("{\"version\":1,\"unlisted\":\"allow\"}", DateTime.UtcNow);
                Expect("ro policy loaded", WritePolicy.State == "ok");
                CheckWrites();
            }
            finally
            {
                WritePolicy.ResetForTest();
            }
            CheckReads();
            CheckPost();
        }

        static void CheckConfig()
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["readOnlyAccounts"] = new object[] { Listed };
            BridgeConfig.CheckKeys(map);
            ConfigRules.CheckReadOnlyAccounts(new string[] { Listed });
            bool bad = false;
            try
            {
                ConfigRules.CheckReadOnlyAccounts(new string[] { "98" });
            }
            catch (InvalidOperationException)
            {
                bad = true;
            }
            Expect("ro bad code", bad);
            Expect("ro empty default", new BridgeConfig().ReadOnlyAccounts.Length == 0);
            Expect("roex empty default", new BridgeConfig().ReadOnlyExceptions.Length == 0);
            Expect("ro listed", BridgeConfig.IsReadOnlyAccount(Cfg(), Listed));
            Expect("ro other", !BridgeConfig.IsReadOnlyAccount(Cfg(), Other));
            Expect("ro null cfg", !BridgeConfig.IsReadOnlyAccount(null, Listed));
            Dictionary<string, object> keys = new Dictionary<string, object>();
            keys["readOnlyExceptions"] = new object[] { RuleDict() };
            BridgeConfig.CheckKeys(keys);
        }

        static void CheckHealth()
        {
            try
            {
                ReadOnlyGate.Configure(Cfg());
                Expect("ro health", ReadOnlyGate.HealthFragment()
                    == ",\"read_only_accounts\":[\"" + Listed + "\"],\"read_only_exceptions\":[]");
                Expect("ro meta", ReadOnlyGate.Accounts().Length == 1 && ReadOnlyGate.Accounts()[0] == Listed);
                Expect("roex meta empty", ReadOnlyGate.ExceptionSummaries().Length == 0);
            }
            finally
            {
                ReadOnlyGate.Configure(null);
            }
            Expect("ro health empty", ReadOnlyGate.HealthFragment()
                == ",\"read_only_accounts\":[],\"read_only_exceptions\":[]");
        }

        static void CheckWrites()
        {
            List<string> writes = DispatchRoutes.WritePaths();
            Expect("ro writes listed", writes.Count > 0);
            for (int i = 0; i < writes.Count; i++)
            {
                string path = writes[i];
                bool legacy = path == "/u8co/v1/sale-orders/verify" || path == "/u8co/v1/dispatches/verify";
                ExpectRefused(path, false);
                if (!legacy)
                {
                    ExpectRefused(path, true);
                }
                Expect("ro other passes " + path, Code(path, Other, false, null) == "bad_request");
            }
        }

        static void ExpectRefused(string path, bool dry)
        {
            AuditDraft draft = new AuditDraft();
            string code = Code(path, Listed, dry, draft);
            Expect("ro refused " + (dry ? "dry " : "") + path, code == ReadOnlyGate.Code && draft.Policy == ReadOnlyGate.Code);
        }

        static void CheckReads()
        {
            List<string> routes = DispatchRoutes.Paths();
            for (int i = 0; i < routes.Count; i++)
            {
                if (WriteGate.IsWrite(routes[i]))
                {
                    continue;
                }
                Expect("ro read passes " + routes[i], Code(routes[i], Listed, false, null) != ReadOnlyGate.Code);
            }
            Expect("ro load reaches decrypt", Code("/u8co/v1/vouchers/load", Listed, false, null) == "bad_request");
        }

        static void CheckPost()
        {
            WorkItem item = Item(Listed, "/u8co/v1/vouchers/create");
            string code = null;
            try
            {
                WriteClassGate.Post(item);
            }
            catch (BridgeException ex)
            {
                code = ex.Code;
            }
            Expect("ro post refused", code == ReadOnlyGate.Code && item.Policy == ReadOnlyGate.Code);
            WriteClassGate.Post(Item(Other, "/u8co/v1/vouchers/create"));
            WriteClassGate.Post(Item(Listed, "/u8co/v1/vouchers/load"));
            CheckExceptionPost();
        }

        static string Code(string path, string acc, bool dry, AuditDraft draft)
        {
            AuditDraft d = draft ?? new AuditDraft();
            d.Path = path;
            try
            {
                Requests.ForPath(Cfg(), path, Body(acc, dry), d);
                return null;
            }
            catch (BridgeException ex)
            {
                return ex.Code;
            }
            catch (Exception)
            {
                return "other";
            }
        }

        static byte[] Body(string acc, bool dry)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["acc"] = acc;
            body["year"] = "2026";
            body["operator"] = "op001";
            body["date"] = "2026-01-15";
            body["password_enc"] = "not-a-ciphertext";
            if (dry)
            {
                body[DryRunReq.Field] = true;
            }
            return Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(body));
        }

        static BridgeConfig Cfg()
        {
            BridgeConfig cfg = new BridgeConfig();
            cfg.EncKey = Crypto.EncKey(new byte[32]);
            cfg.AllowedAccounts = new string[] { Listed, Other };
            cfg.EnableReplicatedWrites = true;
            cfg.TestAccounts = new string[] { Listed, Other };
            cfg.ReadOnlyAccounts = new string[] { Listed };
            return cfg;
        }

        static WorkItem Item(string acc, string path)
        {
            WorkItem item = new WorkItem();
            item.Config = Cfg();
            item.Path = path;
            item.Type = Kinds.Find("sale_order");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "sale_order";
            item.Body = body;
            item.Acc = acc;
            item.Operator = "op001";
            return item;
        }

        static void CheckExceptionConfig()
        {
            string[] ro = new string[] { Listed };
            ReadOnlyExceptionRule[] rules = ConfigRulesReadOnly.ReadOnlyExceptions(ExMap(new object[] { RuleDict() }), ro);
            Expect("roex parse", rules.Length == 1 && rules[0].Op == ArcPerson.Op
                && rules[0].Operators[0] == "svc01" && rules[0].Type == "archives");
            ExpectConfigError("roex unknown key", WithKey(RuleDict(), "extra", "1"), ro, "未知字段");
            ExpectConfigError("roex archive key", WithKey(RuleDict(), "archive", "customer"), ro, "未知字段");
            ExpectConfigError("roex fields key", WithKey(RuleDict(), "fields", new object[] { "spec_operator" }), ro,
                "未知字段");
            ExpectConfigError("roex acc not ro", WithList(RuleDict(), "accounts", new object[] { Other }), ro,
                "readOnlyAccounts");
            ExpectConfigError("roex type", WithKey(RuleDict(), "type", "gl"), ro, "archives");
            ExpectConfigError("roex op", WithKey(RuleDict(), "op", "update"), ro, ArcPerson.Op);
            ExpectConfigError("roex operators", WithList(RuleDict(), "operators", new object[0]), ro, "不能为空");
            ExpectConfigError("roex op dup", WithList(RuleDict(), "operators", new object[] { "svc01", "SVC01" }), ro,
                "重复");
            ExpectConfigError("roex quote op", WithList(RuleDict(), "operators", new object[] { "svc\"01" }), ro, "双引号");
            Dictionary<string, object> missing = RuleDict();
            missing.Remove("op");
            ExpectConfigError("roex missing op", ExMap(new object[] { missing }), ro, "必须是非空字符串");
            ExpectConfigError("roex not array", ExMap("x"), ro, "对象数组");
        }

        static void CheckExceptionGate()
        {
            WritePolicy.ResetForTest();
            ExpectAdmit("roex admit", false);
            ExpectAdmit("roex admit dry", true);
            ExpectAdmitUser("roex operator case", "SVC01", false);
            ExpectRefusedArc("roex update", Listed, "svc01", ArcUpdate, true);
            ExpectRefusedArc("roex create", Listed, "svc01", Requests.ArcRoot + "create", true);
            ExpectRefusedArc("roex delete", Listed, "svc01", Requests.ArcRoot + "delete", false);
            ExpectRefusedShape("roex other operator", Listed, "op001", ArcPerson.Path);
            ExpectRefusedShape("roex other account", ExtraAcc, "svc01", ArcPerson.Path);
            ExpectIdemMismatch();
            ExpectVoucherSpoof();
            ExpectPolicyDeny();
            CheckExceptionHealth();
            ReadOnlyExceptionSelfTest.Run();
        }

        static void CheckExceptionHealth()
        {
            try
            {
                ReadOnlyGate.Configure(CfgEx());
                string json = ReadOnlyGate.HealthFragment();
                Expect("roex health", json.IndexOf("\"read_only_exceptions\":[{\"accounts\"") >= 0
                    && json.IndexOf("\"" + ArcPerson.Op + "\"") >= 0 && json.IndexOf("\"svc01\"") >= 0
                    && json.IndexOf("\"archive\"") < 0 && json.IndexOf("\"fields\"") < 0);
                object parsed = new JavaScriptSerializer().DeserializeObject("{" + json.Substring(1) + "}");
                Expect("roex health json", parsed != null);
                Expect("roex summaries", ReadOnlyGate.ExceptionSummaries().Length == 1);
                Expect("roex describe", ReadOnlyGate.Describe(CfgEx()).IndexOf("readOnlyExceptions") >= 0);
            }
            finally
            {
                ReadOnlyGate.Configure(null);
            }
        }

        static void CheckExceptionPost()
        {
            WorkItem ok = PersonItem(Listed, ArcPerson.Path, "svc01");
            WriteClassGate.Post(ok);
            Expect("roex post admitted", ok.Policy == ReadOnlyGate.ExceptionCode);
            string code = null;
            WorkItem bad = PersonItem(Listed, Requests.ArcRoot + "create", "svc01");
            try
            {
                WriteClassGate.Post(bad);
            }
            catch (BridgeException ex)
            {
                code = ex.Code;
            }
            Expect("roex post create", code == ReadOnlyGate.Code && bad.Policy == ReadOnlyGate.Code);
            code = null;
            WorkItem upd = PersonItem(Listed, ArcUpdate, "svc01");
            try
            {
                WriteClassGate.Post(upd);
            }
            catch (BridgeException ex)
            {
                code = ex.Code;
            }
            Expect("roex post update", code == ReadOnlyGate.Code && upd.Policy == ReadOnlyGate.Code);
            code = null;
            WorkItem spoof = PersonItem(Listed, VoucherUpdate, "svc01");
            spoof.Body["type"] = "archives";
            try
            {
                WriteClassGate.Post(spoof);
            }
            catch (BridgeException ex)
            {
                code = ex.Code;
            }
            Expect("roex post spoof", code == ReadOnlyGate.Code && spoof.Policy == ReadOnlyGate.Code);
        }

        static void ExpectAdmit(string name, bool dry)
        {
            ExpectAdmitUser(name, "svc01", dry);
        }

        static void ExpectAdmitUser(string name, string user, bool dry)
        {
            AuditDraft draft = new AuditDraft();
            byte[] body = PersonBody(Listed, user, dry, null);
            Expect(name, CodeOf(ArcPerson.Path, body, draft) == "bad_request"
                && draft.Policy == ReadOnlyGate.ExceptionCode);
        }

        static void ExpectIdemMismatch()
        {
            AuditDraft draft = new AuditDraft();
            byte[] body = ArcWriteBody(Listed, "svc01", true, "k-mismatch-1");
            Expect("roex idem mismatch", CodeOf(ArcUpdate, body, draft) == ReadOnlyGate.Code
                && draft.Policy == ReadOnlyGate.Code);
        }

        static void ExpectVoucherSpoof()
        {
            AuditDraft draft = new AuditDraft();
            draft.Path = VoucherUpdate;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "archives";
            body["code"] = "C900001";
            body["person"] = "EX040001";
            string code = PreCode(draft, body);
            Expect("roex voucher spoof", code == ReadOnlyGate.Code && draft.Policy == ReadOnlyGate.Code);
        }

        static void ExpectPolicyDeny()
        {
            WritePolicy.ResetForTest();
            try
            {
                WritePolicy.Apply("{\"version\":1,\"unlisted\":\"deny\"}", DateTime.UtcNow);
                Expect("roex policy loaded", WritePolicy.State == "ok");
                AuditDraft draft = new AuditDraft();
                byte[] body = PersonBody(Listed, "svc01", false, null);
                Expect("roex policy deny", CodeOf(ArcPerson.Path, body, draft) == "write_not_allowed"
                    && draft.Policy == "write_not_allowed");
            }
            finally
            {
                WritePolicy.ResetForTest();
            }
        }

        static string PreCode(AuditDraft draft, Dictionary<string, object> body)
        {
            try
            {
                WriteClassGate.Pre(CfgEx(), body, draft, Listed, "svc01");
                return null;
            }
            catch (BridgeException ex)
            {
                return ex.Code;
            }
        }

        static void ExpectRefusedShape(string name, string acc, string user, string path)
        {
            AuditDraft draft = new AuditDraft();
            byte[] body = PersonBody(acc, user, false, null);
            Expect(name, CodeOf(path, body, draft) == ReadOnlyGate.Code && draft.Policy == ReadOnlyGate.Code);
        }

        static void ExpectRefusedArc(string name, string acc, string user, string path, bool fields)
        {
            AuditDraft draft = new AuditDraft();
            byte[] body = ArcWriteBody(acc, user, fields, null);
            Expect(name, CodeOf(path, body, draft) == ReadOnlyGate.Code && draft.Policy == ReadOnlyGate.Code);
        }

        internal static void ExpectConfigError(string name, Dictionary<string, object> map, string[] ro, string text)
        {
            string msg = null;
            try
            {
                ConfigRulesReadOnly.ReadOnlyExceptions(map, ro);
            }
            catch (InvalidOperationException ex)
            {
                msg = ex.Message;
            }
            Expect(name, msg != null && msg.IndexOf(text, StringComparison.Ordinal) >= 0);
        }

        static string CodeOf(string path, byte[] body, AuditDraft draft)
        {
            return CodeWith(CfgEx(), path, body, draft);
        }

        internal static string CodeWith(BridgeConfig cfg, string path, byte[] body, AuditDraft draft)
        {
            draft.Path = path;
            try
            {
                Requests.ForPath(cfg, path, body, draft);
                return null;
            }
            catch (BridgeException ex)
            {
                return ex.Code;
            }
        }

        static byte[] ArcWriteBody(string acc, string user, bool fields, string idem)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["acc"] = acc;
            body["year"] = "2026";
            body["operator"] = user;
            body["date"] = "2026-01-15";
            body["password_enc"] = "not-a-ciphertext";
            body["archive"] = "customer";
            body["code"] = "C900001";
            if (fields)
            {
                Dictionary<string, object> map = new Dictionary<string, object>();
                map["spec_operator"] = "EX040001";
                body["fields"] = map;
            }
            if (idem != null)
            {
                body["idempotency_key"] = idem;
            }
            return Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(body));
        }

        static byte[] PersonBody(string acc, string user, bool dry, string idem)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["acc"] = acc;
            body["year"] = "2026";
            body["operator"] = user;
            body["date"] = "2026-01-15";
            body["password_enc"] = "not-a-ciphertext";
            body["code"] = "C900001";
            body["person"] = "EX040001";
            if (dry)
            {
                body[DryRunReq.Field] = true;
            }
            if (idem != null)
            {
                body["idempotency_key"] = idem;
            }
            return Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(body));
        }

        internal static BridgeConfig CfgEx()
        {
            BridgeConfig cfg = Cfg();
            cfg.AllowedAccounts = new string[] { Listed, Other, ExtraAcc };
            cfg.ReadOnlyAccounts = new string[] { Listed, ExtraAcc };
            cfg.ReadOnlyExceptions = new ReadOnlyExceptionRule[] { SampleRule() };
            return cfg;
        }

        static ReadOnlyExceptionRule SampleRule()
        {
            ReadOnlyExceptionRule rule = new ReadOnlyExceptionRule();
            rule.Accounts = new string[] { Listed };
            rule.Operators = new string[] { "svc01" };
            rule.Type = "archives";
            rule.Op = ArcPerson.Op;
            return rule;
        }

        internal static Dictionary<string, object> RuleDict()
        {
            Dictionary<string, object> rule = new Dictionary<string, object>();
            rule["accounts"] = new object[] { Listed };
            rule["operators"] = new object[] { "svc01" };
            rule["type"] = "archives";
            rule["op"] = ArcPerson.Op;
            return rule;
        }

        internal static Dictionary<string, object> ExMap(object rules)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["readOnlyExceptions"] = rules;
            return map;
        }

        internal static Dictionary<string, object> WithKey(Dictionary<string, object> rule, string key, object value)
        {
            rule[key] = value;
            return ExMap(new object[] { rule });
        }

        internal static Dictionary<string, object> WithList(Dictionary<string, object> rule, string key, object[] value)
        {
            rule[key] = value;
            return ExMap(new object[] { rule });
        }

        static WorkItem PersonItem(string acc, string path, string user)
        {
            WorkItem item = new WorkItem();
            item.Config = CfgEx();
            item.Path = path;
            item.Acc = acc;
            item.Operator = user;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["code"] = "C900001";
            body["person"] = "EX040001";
            item.Body = body;
            return item;
        }

        internal static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
