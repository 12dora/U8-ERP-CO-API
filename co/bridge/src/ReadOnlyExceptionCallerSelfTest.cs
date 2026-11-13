using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的只读例外 callers 规则（按调用系统放行，由 ReadOnlyGateSelfTest 调用）：
    // 配置：operators 与 callers 恰好一个（两个都写、都不写报错），callers 只用于 type=vouchers 的 CallerOps（create、update、
    // close、verify、generate），archives、head_fix、so_contract 写 callers 报错，callers 须是信任项 name（不能是 sha256）。
    // 闸门：请求体 caller_trust（API 已验签的信任项 name，IdemReq.TrustField）在 callers 里时任何操作员都放行该对，caller
    // （幂等与审计标识）换成 sha256 摘要也一样；信任项不对、大小写不同、不带 caller_trust（只有 caller「crm:…」也不算，
    // 旧版 API 即如此）、档案路由、别的单据对、应收审核 arap_verify 都 403；caller_trust 不合写法 400。审计 read_only_match
    // 记 caller，按操作员的规则记 operator。入队后同样判断。caller_trust 不进幂等摘要和存储键。健康检查列出 callers。
    // 与 API tests/test_readonly_exceptions_callers.py 同一组用例。不连库、不建 COM、不登录。
    internal static class ReadOnlyExceptionCallerSelfTest
    {
        const string Listed = ReadOnlyGateSelfTest.Listed;
        const string Root = ReadOnlyVoucherOps.Root;
        const string So = "sale_order";
        const string Dl = "dispatch";
        const string Inv = "sale_invoice";
        const string Crm = "crm";

        public static void Run()
        {
            CheckConfig();
            CheckAdmit();
            CheckRefused();
            CheckOperatorRows();
            CheckForged();
            CheckPost();
            CheckHealth();
        }

        static void CheckConfig()
        {
            CheckParse();
            CheckKeyErrors();
            CheckScopeErrors();
            CheckTrustNames();
            CheckIdem();
        }

        static void CheckParse()
        {
            string[] ro = new string[] { Listed };
            Expect("roc caller ops", ConfigRulesReadOnly.CallerOps.Length == 5 && HasCallerOps());
            ReadOnlyExceptionRule[] rules = ReadOnlyExceptionSelfTest.Parse(Rule("create", So), ro);
            Expect("roc parse", rules.Length == 1 && rules[0].Operators == null && rules[0].Callers.Length == 1
                && rules[0].Callers[0] == Crm && rules[0].Voucher == So);
        }

        static bool HasCallerOps()
        {
            string[] want = new string[] { "create", "update", "close", "verify", "generate" };
            for (int i = 0; i < want.Length; i++)
            {
                if (Array.IndexOf(ConfigRulesReadOnly.CallerOps, want[i]) < 0)
                {
                    return false;
                }
            }
            return true;
        }

        // operators / callers 的有无与内容。
        static void CheckKeyErrors()
        {
            Dictionary<string, object> both = Rule("create", So);
            both["operators"] = new object[] { "svc01" };
            ExpectError("roc both", both, "恰好写 operators 或 callers 之一");
            Dictionary<string, object> neither = Rule("create", So);
            neither.Remove("callers");
            ExpectError("roc neither", neither, "恰好写 operators 或 callers 之一");
            ExpectError("roc empty", WithCallers(Rule("create", So), new object[0]), "不能为空");
            ExpectError("roc dup", WithCallers(Rule("create", So), new object[] { Crm, Crm }), "重复");
            string[] bad = new string[] { "sha256", "1crm", "crm:x", "crm x", "", new string('c', 64), "客户" };
            for (int i = 0; i < bad.Length; i++)
            {
                ExpectError("roc name " + i.ToString(), WithCallers(Rule("create", So), new object[] { bad[i] }), "信任项的 name");
            }
            Dictionary<string, object> notList = Rule("create", So);
            notList["callers"] = Crm;
            ExpectError("roc not list", notList, "字符串数组");
        }

        static void CheckScopeErrors()
        {
            // 档案规则、单据类型固定的 op 只能写 operators。
            Dictionary<string, object> arc = ReadOnlyGateSelfTest.RuleDict();
            arc.Remove("operators");
            arc["callers"] = new object[] { Crm };
            ExpectError("roc archives", arc, "callers 只能用于 type=vouchers");
            Dictionary<string, object> arcCreate = ReadOnlyExceptionSelfTest.RuleOf("create", new object[] { "customer" });
            arcCreate.Remove("operators");
            arcCreate["callers"] = new object[] { Crm };
            ExpectError("roc archives create", arcCreate, "callers 只能用于 type=vouchers");
            ExpectError("roc head_fix", Rule(VoucherHeadFix.Op, null), "callers 只能用于 type=vouchers");
            ExpectError("roc so_contract", Rule(VoucherSoContract.Op, null), "callers 只能用于 type=vouchers");
            ExpectError("roc missing voucher", Rule("create", null), "必须给出");
            Dictionary<string, object> stray = Rule("create", So);
            stray["accounts"] = new object[] { "802" };
            ExpectError("roc read only", stray, "readOnlyAccounts");
        }

        // caller_trust 的写法（IdemReq.IsTrustName）；配置的 callers 另排除 sha256（ConfigRulesReadOnly.TrustName）。
        static void CheckTrustNames()
        {
            string[] good = new string[] { Crm, "app-a_2", "A", new string('c', 63) };
            string[] bad = new string[] { null, "", "crm:c", "1crm", "_crm", "crm x", new string('c', 64), "客户" };
            for (int i = 0; i < good.Length; i++)
            {
                Expect("roc trust name ok " + i.ToString(), IdemReq.IsTrustName(good[i]) && ConfigRulesReadOnly.TrustName(good[i]));
            }
            for (int i = 0; i < bad.Length; i++)
            {
                Expect("roc trust name bad " + i.ToString(), !IdemReq.IsTrustName(bad[i]) && !ConfigRulesReadOnly.TrustName(bad[i]));
            }
            Expect("roc trust name digest", IdemReq.IsTrustName("sha256") && !ConfigRulesReadOnly.TrustName("sha256"));
        }

        // caller_trust 和 caller 一起在写路由上取走；不进业务摘要、不改存储键，带不带它同一条幂等记录。
        static void CheckIdem()
        {
            string route = Root + "create";
            Dictionary<string, object> plain = Login(Listed, "op001", null);
            plain[IdemReq.CallerField] = "crm:client-a";
            plain[IdemReq.KeyField] = "k-1";
            Dictionary<string, object> with = new Dictionary<string, object>(plain);
            with[IdemReq.TrustField] = Crm;
            IdemAsk a = IdemReq.Take(plain, route);
            IdemAsk b = IdemReq.Take(with, route);
            Expect("roc idem same", a != null && b != null && a.Caller == b.Caller && a.BodySha == b.BodySha
                && a.StoreId(Listed, route) == b.StoreId(Listed, route) && !with.ContainsKey(IdemReq.TrustField));
            Dictionary<string, object> noKey = Login(Listed, "op001", Crm);
            Expect("roc take no key", IdemReq.Take(noKey, route) == null && !noKey.ContainsKey(IdemReq.TrustField)
                && !noKey.ContainsKey(IdemReq.CallerField));
            Dictionary<string, object> read = Login(Listed, "op001", Crm);
            Expect("roc read route keeps", IdemReq.Take(read, Root + "load") == null && read.ContainsKey(IdemReq.TrustField));
        }

        // 任何操作员都放行该对（含预演），审计 read_only_match 记 caller。
        static void CheckAdmit()
        {
            string create = Root + "create";
            BridgeConfig cfg = Cfg("create", So);
            ExpectAdmit("roc admit op001", cfg, create, Body(create, "op001", So, null, Crm, false));
            ExpectAdmit("roc admit op002", cfg, create, Body(create, "op002", So, null, Crm, false));
            ExpectAdmit("roc admit dry", cfg, create, Body(create, "op003", So, null, Crm, true));
            string verify = Root + "verify";
            BridgeConfig inv = Cfg("verify", Inv);
            ExpectAdmit("roc admit inv verify", inv, verify, Body(verify, "op001", Inv, "verify", Crm, false));
            ExpectAdmit("roc admit inv unverify", inv, verify, Body(verify, "op001", Inv, "unverify", Crm, false));
            string gen = Root + "generate";
            ExpectAdmit("roc admit gen", Cfg("generate", Dl), gen, Body(gen, "op001", Dl, null, Crm, false));
            // 客户端标识很长（如 azp 为 200 个字符）时 API 的 caller 换成 sha256:<摘要>；caller_trust 不变，照样命中（复审 P2）。
            ExpectAdmit("roc admit long client", cfg, create, Raw(create, "sha256:" + new string('a', 64), Crm));
            ExpectAdmit("roc admit trust no caller", cfg, create, Raw(create, null, Crm));
        }

        static void CheckRefused()
        {
            string create = Root + "create";
            BridgeConfig cfg = Cfg("create", So);
            ExpectRefused("roc other trust", cfg, create, Body(create, "op001", So, null, "tool", false));
            ExpectRefused("roc trust case", cfg, create, Body(create, "op001", So, null, "CRM", false));
            ExpectRefused("roc no caller", cfg, create, Body(create, "op001", So, null, null, false));
            // 只有 caller（旧版 API 的形状，或直连桥）：不从里面拆信任项，不命中。
            ExpectRefused("roc caller only", cfg, create, Raw(create, "crm:client-a", null));
            ExpectRefused("roc digest only", cfg, create, Raw(create, "sha256:" + new string('0', 64), null));
            // caller_trust 不合写法：400，在闸门之前（不会被当成「没有调用系统」）。
            string[] bad = new string[] { "", "crm:c", "1crm", new string('c', 64), "客户" };
            for (int i = 0; i < bad.Length; i++)
            {
                ExpectBadTrust("roc bad trust " + i.ToString(), cfg, create, Raw(create, "crm:c", bad[i]));
            }
            Dictionary<string, object> notText = Login(Listed, "op001", null);
            notText[IdemReq.TrustField] = 7;
            notText["type"] = So;
            ExpectBadTrust("roc bad trust number", cfg, create, Encode(notText));
            ExpectRefused("roc other type", cfg, create, Body(create, "op001", "purchase_order", null, Crm, false));
            ExpectRefused("roc dispatch", cfg, create, Body(create, "op001", Dl, null, Crm, false));
            Dictionary<string, object> otherAcc = BodyMap(create, "op001", So, null, Crm, false);
            otherAcc["acc"] = ReadOnlyGateSelfTest.ExtraAcc;
            ExpectRefused("roc other account", cfg, create, Encode(otherAcc));
            string[] others = new string[] { "update", "close", "verify", "generate", "delete", "lock" };
            for (int i = 0; i < others.Length; i++)
            {
                string path = Root + others[i];
                ExpectRefused("roc other route " + others[i], cfg, path,
                    Body(path, "op001", So, ActionOf(others[i]), Crm, false));
            }
            ExpectRefusedArchive("roc archives route", cfg);
            string verify = Root + "verify";
            BridgeConfig inv = Cfg("verify", Inv);
            ExpectRefused("roc arap_verify", inv, verify, Body(verify, "op001", Inv, "arap_verify", Crm, false));
            ExpectRefused("roc arap_unverify", inv, verify, Body(verify, "op001", Inv, "arap_unverify", Crm, false));
            ExpectRefused("roc inv not dl", inv, verify, Body(verify, "op001", Dl, "verify", Crm, false));
            ExpectRefused("roc legacy", Cfg("verify", So), "/u8co/v1/sale-orders/verify", Legacy(Crm));
        }

        // 按操作员的规则照旧（不看 caller），审计记 operator；同时有两种规则时 operator 优先。
        static void CheckOperatorRows()
        {
            string create = Root + "create";
            BridgeConfig ops = Cfg("create", So);
            ops.ReadOnlyExceptions[0].Callers = null;
            ops.ReadOnlyExceptions[0].Operators = new string[] { "svc01" };
            ExpectAdmit("roc operator row", ops, create, Body(create, "svc01", So, null, null, false), ReadOnlyGate.MatchOperator);
            ExpectRefused("roc operator row trust", ops, create, Body(create, "op001", So, null, Crm, false));
            BridgeConfig mixed = Cfg("create", So);
            mixed.ReadOnlyExceptions = new ReadOnlyExceptionRule[] { mixed.ReadOnlyExceptions[0], ops.ReadOnlyExceptions[0] };
            ExpectAdmit("roc mixed operator", mixed, create, Body(create, "svc01", So, null, Crm, false), ReadOnlyGate.MatchOperator);
            ExpectAdmit("roc mixed caller", mixed, create, Body(create, "op001", So, null, Crm, false), ReadOnlyGate.MatchCaller);
        }

        // 直接构造的规则：callers 同时带 operators、用于档案或单据类型固定的 op 一律不放行。
        static void CheckForged()
        {
            string create = Root + "create";
            BridgeConfig both = Cfg("create", So);
            both.ReadOnlyExceptions[0].Operators = new string[] { "op001" };
            ExpectRefused("roc forged both", both, create, Body(create, "op001", So, null, Crm, false));
            BridgeConfig fix = Cfg(VoucherHeadFix.Op, null);
            string fixPath = Root + VoucherHeadFix.Op;
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(fix, fixPath, HeadFixBody(Crm), draft);
            Expect("roc forged head_fix", code == ReadOnlyGate.Code);
            BridgeConfig arc = Cfg("create", So);
            arc.ReadOnlyExceptions[0].Type = "archives";
            arc.ReadOnlyExceptions[0].Op = ArcPerson.Op;
            arc.ReadOnlyExceptions[0].Voucher = null;
            ExpectRefusedArchive("roc forged archives", arc);
        }

        static void CheckPost()
        {
            ExpectPost("roc post admit", Cfg("create", So), PostAsk.Of("create", So, null, Crm), true);
            ExpectPost("roc post other trust", Cfg("create", So), PostAsk.Of("create", So, null, "tool"), false);
            ExpectPost("roc post no caller", Cfg("create", So), PostAsk.Of("create", So, null, null), false);
            ExpectPost("roc post caller only", Cfg("create", So), PostAsk.Of("create", So, null, null).With("crm:c"), false);
            ExpectPost("roc post digest", Cfg("create", So),
                PostAsk.Of("create", So, null, Crm).With("sha256:" + new string('a', 64)), true);
            ExpectPost("roc post arap", Cfg("verify", Inv), PostAsk.Of("verify", Inv, "arap_verify", Crm), false);
            ExpectPost("roc post verify", Cfg("verify", Inv), PostAsk.Of("verify", Inv, "verify", Crm), true);
        }

        static void CheckHealth()
        {
            try
            {
                ReadOnlyGate.Configure(Cfg("create", So));
                string json = ReadOnlyGate.HealthFragment();
                Expect("roc health", json.IndexOf("\"callers\":[\"crm\"],\"type\":\"vouchers\",\"op\":\"create\"", StringComparison.Ordinal) >= 0
                    && json.IndexOf("\"operators\"", StringComparison.Ordinal) < 0);
                Dictionary<string, object> map = (Dictionary<string, object>)ReadOnlyGate.ExceptionSummaries()[0];
                Expect("roc summary", map.ContainsKey("callers") && !map.ContainsKey("operators"));
            }
            finally
            {
                ReadOnlyGate.Configure(null);
            }
        }

        // 入队后判定用的请求：路由 op、单据类型、action、caller_trust 和 caller（缺省为「信任项:client-a」）。
        sealed class PostAsk
        {
            public string Op;
            public string Kind;
            public string Action;
            public string Trust;
            public string Caller;

            public static PostAsk Of(string op, string kind, string action, string trust)
            {
                PostAsk ask = new PostAsk();
                ask.Op = op;
                ask.Kind = kind;
                ask.Action = action;
                ask.Trust = trust;
                ask.Caller = trust == null ? null : trust + ":client-a";
                return ask;
            }

            public PostAsk With(string caller)
            {
                Caller = caller;
                return this;
            }
        }

        static void ExpectPost(string name, BridgeConfig cfg, PostAsk ask, bool ok)
        {
            WorkItem item = ReadOnlyExceptionSelfTest.Item(cfg, Root + ask.Op, null);
            item.Operator = "op001";
            item.Trust = ask.Trust;
            item.Caller = ask.Caller;
            item.Body["type"] = ask.Kind;
            if (ask.Action != null)
            {
                item.Body["action"] = ask.Action;
            }
            string code = null;
            try
            {
                WriteClassGate.Post(item);
            }
            catch (BridgeException ex)
            {
                code = ex.Code;
            }
            Expect(name, ok ? code == null && item.Policy == ReadOnlyGate.ExceptionCode && item.ReadOnlyMatch == ReadOnlyGate.MatchCaller
                : code == ReadOnlyGate.Code && item.Policy == ReadOnlyGate.Code && item.ReadOnlyMatch == "");
        }

        static void ExpectAdmit(string name, BridgeConfig cfg, string path, byte[] body)
        {
            ExpectAdmit(name, cfg, path, body, ReadOnlyGate.MatchCaller);
        }

        static void ExpectAdmit(string name, BridgeConfig cfg, string path, byte[] body, string kind)
        {
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, path, body, draft);
            Expect(name, code == "bad_request" && draft.Policy == ReadOnlyGate.ExceptionCode && draft.ReadOnlyMatch == kind);
        }

        static void ExpectRefused(string name, BridgeConfig cfg, string path, byte[] body)
        {
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, path, body, draft);
            Expect(name, code == ReadOnlyGate.Code && draft.Policy == ReadOnlyGate.Code && string.IsNullOrEmpty(draft.ReadOnlyMatch));
        }

        static void ExpectRefusedArchive(string name, BridgeConfig cfg)
        {
            Dictionary<string, object> body = Login(Listed, "op001", Crm);
            body["code"] = "C900001";
            body["person"] = "EX040001";
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, ArcPerson.Path, Encode(body), draft);
            Expect(name, code == ReadOnlyGate.Code);
        }

        static Dictionary<string, object> Rule(string op, string voucher)
        {
            Dictionary<string, object> rule = new Dictionary<string, object>();
            rule["accounts"] = new object[] { Listed };
            rule["callers"] = new object[] { Crm };
            rule["type"] = "vouchers";
            rule["op"] = op;
            if (voucher != null)
            {
                rule["voucher"] = voucher;
            }
            return rule;
        }

        static Dictionary<string, object> WithCallers(Dictionary<string, object> rule, object[] callers)
        {
            rule["callers"] = callers;
            return rule;
        }

        static void ExpectError(string name, Dictionary<string, object> rule, string text)
        {
            ReadOnlyExceptionSelfTest.ExpectError(name, rule, new string[] { Listed }, text);
        }

        static BridgeConfig Cfg(string op, string voucher)
        {
            BridgeConfig cfg = ReadOnlyGateSelfTest.CfgEx();
            ReadOnlyExceptionRule rule = new ReadOnlyExceptionRule();
            rule.Accounts = new string[] { Listed };
            rule.Callers = new string[] { Crm };
            rule.Type = "vouchers";
            rule.Op = op;
            rule.Voucher = voucher;
            cfg.ReadOnlyExceptions = new ReadOnlyExceptionRule[] { rule };
            return cfg;
        }

        static string ActionOf(string op)
        {
            if (op == "verify" || op == "close" || op == "lock")
            {
                return op;
            }
            return null;
        }

        static byte[] Body(string path, string user, string type, string action, string trust, bool dry)
        {
            return Encode(BodyMap(path, user, type, action, trust, dry));
        }

        // 只带该路由允许的键；口令故意解不开：放行时在解密处 400，拒绝发生在解密之前。trust 为 null 时不带 caller、caller_trust。
        // 账套为 Listed，别的账套由调用方改 acc。
        static Dictionary<string, object> BodyMap(string path, string user, string type, string action, string trust, bool dry)
        {
            Dictionary<string, object> body = Login(Listed, user, trust);
            if (type != null)
            {
                body["type"] = type;
            }
            if (path != Root + "create")
            {
                body["id"] = 7;
            }
            if (action != null)
            {
                body["action"] = action;
            }
            if (dry)
            {
                body[DryRunReq.Field] = true;
            }
            return body;
        }

        static byte[] HeadFixBody(string trust)
        {
            Dictionary<string, object> body = Login(Listed, "op001", trust);
            body["type"] = VoucherHeadFix.Kind;
            body["id"] = 7;
            return Encode(body);
        }

        static byte[] Legacy(string trust)
        {
            Dictionary<string, object> body = Login(Listed, "op001", trust);
            body["id"] = 7;
            body["action"] = "verify";
            return Encode(body);
        }

        // trust 不为 null 时同 API 一样带 caller_trust 和 caller（「信任项:客户端」）。
        static Dictionary<string, object> Login(string acc, string user, string trust)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["acc"] = acc;
            body["year"] = "2026";
            body["operator"] = user;
            body["date"] = "2026-01-15";
            body["password_enc"] = "not-a-ciphertext";
            if (trust != null)
            {
                body[IdemReq.TrustField] = trust;
                body[IdemReq.CallerField] = trust + ":client-a";
            }
            return body;
        }

        // vouchers/create 的销售订单请求体，caller、caller_trust 各自给出（null 不带）。
        static byte[] Raw(string path, string caller, string trust)
        {
            Dictionary<string, object> body = Login(Listed, "op001", null);
            body["type"] = So;
            if (caller != null)
            {
                body[IdemReq.CallerField] = caller;
            }
            if (trust != null)
            {
                body[IdemReq.TrustField] = trust;
            }
            return Encode(body);
        }

        static void ExpectBadTrust(string name, BridgeConfig cfg, string path, byte[] body)
        {
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, path, body, draft);
            Expect(name, code == "bad_request" && string.IsNullOrEmpty(draft.Policy) && string.IsNullOrEmpty(draft.ReadOnlyMatch));
        }

        static byte[] Encode(Dictionary<string, object> body)
        {
            return Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(body));
        }

        static void Expect(string name, bool ok)
        {
            ReadOnlyGateSelfTest.Expect(name, ok);
        }
    }
}
