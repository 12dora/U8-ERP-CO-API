using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的只读例外单据登记表部分（ReadOnlyVoucherOps，由 ReadOnlyGateSelfTest 调用）：
    // type=vouchers 的 create / update / close / verify / generate 一项一对 (op, voucher)。配置：缺 voucher、未登记的对、
    // 未登记的 op、archives 用在单据规则上、voucher 用在 archives 规则上都在加载时报错；head_fix、so_contract 可省 voucher。
    // 闸门：每一对只放行列出的操作员、账套、单据类型和自己的路径（含预演），verify 含弃审、close 不含打开，
    // generate 只认参照销售订单；其它单据类型、vouchers/delete、vouchers/lock、旧版审核路由仍 403。入队后同样判断。
    // 与 API tests/test_readonly_exceptions_voucher_ops.py 同一组用例。不连库、不建 COM、不登录。
    internal static class ReadOnlyVoucherOpsSelfTest
    {
        const string Listed = ReadOnlyGateSelfTest.Listed;
        const string Root = ReadOnlyVoucherOps.Root;
        const string So = "sale_order";
        const string Dl = "dispatch";

        public static void Run()
        {
            CheckRegistry();
            CheckConfig();
            CheckPairs();
            CheckNarrow();
            CheckGenerate();
            CheckForged();
            CheckPost();
            CheckHealth();
            CheckOperatorCase();
        }

        // 操作员只忽略 ASCII 大小写：opß 与 opss / OPSS、Kelvin 符号 K 与 k 都不相等（与 API ascii_fold 一致）。
        static void CheckOperatorCase()
        {
            Expect("rov ascii fold", AsciiIgnoreCase.Instance.Equals("SVC01", "svc01")
                && !AsciiIgnoreCase.Instance.Equals("op\u00df", "opss")
                && !AsciiIgnoreCase.Instance.Equals("op\u00df", "OPSS")
                && !AsciiIgnoreCase.Instance.Equals("op\u212a", "opk")
                && !AsciiIgnoreCase.Instance.Equals("op\u00e9", "op\u00c9")
                && AsciiIgnoreCase.Instance.GetHashCode("OpA") == AsciiIgnoreCase.Instance.GetHashCode("opa"));
            BridgeConfig cfg = Cfg("create", So);
            cfg.ReadOnlyExceptions[0].Operators = new string[] { "op\u00df" };
            string path = Root + "create";
            ExpectAdmit("rov sharp s exact", cfg, path, Body(path, Listed, "op\u00df", So, null, false));
            ExpectRefused("rov sharp s vs ss", cfg, path, Body(path, Listed, "opss", So, null, false));
            ExpectRefused("rov sharp s vs SS", cfg, path, Body(path, Listed, "OPSS", So, null, false));
            ReadOnlyExceptionRule[] rules = ReadOnlyExceptionSelfTest.Parse(
                WithOperators(Rule("create", So), "op\u00df", "opss"), new string[] { Listed });
            Expect("rov sharp s not dup", rules[0].Operators.Length == 2);
            ExpectError("rov ascii dup", WithOperators(Rule("create", So), "opA", "opa"), "重复");
        }

        static Dictionary<string, object> WithOperators(Dictionary<string, object> rule, string a, string b)
        {
            rule["operators"] = new object[] { a, b };
            return rule;
        }

        static void CheckRegistry()
        {
            Expect("rov rows", ReadOnlyVoucherOps.Rows.Length == 7
                && ReadOnlyVoucherOps.VoucherOf("verify", Dl) == Dl && ReadOnlyVoucherOps.VoucherOf("create", Dl) == null
                && ReadOnlyVoucherOps.VoucherOf(VoucherHeadFix.Op, null) == VoucherHeadFix.Kind
                && ReadOnlyVoucherOps.VoucherOf("create", null) == null && ReadOnlyVoucherOps.Find("delete") == null);
            Expect("rov generate source", ReadOnlyVoucherOps.Find("generate").Source == So);
        }

        static void CheckConfig()
        {
            string[] ro = new string[] { Listed };
            string[][] pairs = new string[][]
            {
                new string[] { "create", So }, new string[] { "update", So }, new string[] { "close", So },
                new string[] { "verify", So }, new string[] { "verify", Dl }, new string[] { "generate", Dl }
            };
            for (int i = 0; i < pairs.Length; i++)
            {
                ReadOnlyExceptionRule[] rules = ReadOnlyExceptionSelfTest.Parse(Rule(pairs[i][0], pairs[i][1]), ro);
                Expect("rov parse " + pairs[i][0] + " " + pairs[i][1], rules.Length == 1 && rules[0].Type == "vouchers"
                    && rules[0].Op == pairs[i][0] && rules[0].Voucher == pairs[i][1] && rules[0].Archives == null);
                ReadOnlyExceptionSelfTest.ExpectError("rov missing " + pairs[i][0], Rule(pairs[i][0], null), ro, "必须给出");
            }
            ExpectError("rov create dispatch", Rule("create", Dl), "目前只允许");
            ExpectError("rov generate sale_order", Rule("generate", So), "目前只允许");
            ExpectError("rov case", Rule("create", "Sale_Order"), "目前只允许");
            ExpectError("rov empty", Rule("create", ""), "目前只允许");
            Dictionary<string, object> number = Rule("create", null);
            number["voucher"] = 1;
            ExpectError("rov number", number, "目前只允许");
            ExpectError("rov head_fix other", Rule(VoucherHeadFix.Op, So), "目前只允许");
            ExpectError("rov delete op", Rule("delete", So), VoucherHeadFix.Op);
            ExpectError("rov lock op", Rule("lock", So), VoucherHeadFix.Op);
            Dictionary<string, object> arc = Rule("create", So);
            arc["archives"] = new object[] { "customer" };
            ExpectError("rov archives key", arc, "只能用于");
            Dictionary<string, object> onArchives = ReadOnlyGateSelfTest.RuleDict();
            onArchives["voucher"] = So;
            ExpectError("rov voucher on archives", onArchives, "只能用于 type=vouchers");
            Expect("rov head_fix voucher", ReadOnlyExceptionSelfTest.Parse(Rule(VoucherHeadFix.Op, Dl), ro)[0].Voucher == Dl);
            Expect("rov so_contract fixed", ReadOnlyExceptionSelfTest.Parse(Rule(VoucherSoContract.Op, null), ro)[0].Voucher == null);
        }

        // 每一对：放行列出的操作员（不分大小写）和预演；其它操作员、账套、单据类型、路径一律拒绝。
        static void CheckPairs()
        {
            CheckPair("create", So, null);
            CheckPair("update", So, null);
            CheckPair("close", So, "close");
            CheckPair("verify", So, "verify");
            CheckPair("verify", So, "unverify");
            CheckPair("verify", Dl, "verify");
            CheckPair("verify", Dl, "unverify");
            CheckPair("generate", Dl, null);
        }

        static void CheckPair(string op, string kind, string action)
        {
            BridgeConfig cfg = Cfg(op, kind);
            string name = "rov " + op + " " + kind + " " + (action ?? "") + " ";
            string path = Root + op;
            ExpectAdmit(name + "admit", cfg, path, Body(path, Listed, "svc01", kind, action, false));
            ExpectAdmit(name + "case", cfg, path, Body(path, Listed, "SVC01", kind, action, false));
            ExpectAdmit(name + "dry", cfg, path, Body(path, Listed, "svc01", kind, action, true));
            ExpectRefused(name + "operator", cfg, path, Body(path, Listed, "op001", kind, action, false));
            ExpectRefused(name + "account", cfg, path, Body(path, ReadOnlyGateSelfTest.ExtraAcc, "svc01", kind, action, false));
            ExpectRefused(name + "type", cfg, path, Body(path, Listed, "svc01", "purchase_order", action, false));
            ExpectRefused(name + "no type", cfg, path, Body(path, Listed, "svc01", null, action, false));
            string[] others = new string[] { "create", "update", "close", "verify", "generate", "delete", "lock" };
            for (int i = 0; i < others.Length; i++)
            {
                if (others[i] != op)
                {
                    string other = Root + others[i];
                    ExpectRefused(name + others[i], cfg, other, Body(other, Listed, "svc01", kind, ActionOf(others[i]), false));
                }
            }
        }

        // create + sale_order 不开放发货单新增及其预演；verify 只开放列出的单据类型；close 不开放打开；旧版审核路由仍拒绝。
        static void CheckNarrow()
        {
            string create = Root + "create";
            BridgeConfig so = Cfg("create", So);
            ExpectRefused("rov create dispatch", so, create, Body(create, Listed, "svc01", Dl, null, false));
            ExpectRefused("rov create dispatch dry", so, create, Body(create, Listed, "svc01", Dl, null, true));
            ExpectRefused("rov create case", so, create, Body(create, Listed, "svc01", "Sale_Order", null, false));
            string verify = Root + "verify";
            ExpectRefused("rov verify so only", Cfg("verify", So), verify, Body(verify, Listed, "svc01", Dl, "verify", false));
            ExpectRefused("rov verify dl only", Cfg("verify", Dl), verify, Body(verify, Listed, "svc01", So, "verify", false));
            ExpectRefused("rov verify other action", Cfg("verify", So), verify, Body(verify, Listed, "svc01", So, "close", false));
            string close = Root + "close";
            ExpectRefused("rov close open", Cfg("close", So), close, Body(close, Listed, "svc01", So, "open", false));
            BridgeConfig all = Cfg("verify", So);
            ExpectRefused("rov legacy so", all, "/u8co/v1/sale-orders/verify", Legacy("verify"));
            ExpectRefused("rov legacy dl", Cfg("verify", Dl), "/u8co/v1/dispatches/verify", Legacy("verify"));
        }

        // generate：source_type 省略或为 sale_order 放行，其它来源（含大小写不同、null）拒绝。
        static void CheckGenerate()
        {
            BridgeConfig cfg = Cfg("generate", Dl);
            string path = Root + "generate";
            ExpectAdmit("rov gen source", cfg, path, GenBody(So));
            string[] bad = new string[] { "sale_return", "dispatch", "Sale_Order", "" };
            for (int i = 0; i < bad.Length; i++)
            {
                ExpectRefused("rov gen source " + bad[i], cfg, path, GenBody(bad[i]));
            }
            ExpectRefused("rov gen source null", cfg, path, GenBody(null));
        }

        // 直接构造的规则：未登记的 op、缺 voucher、voucher 不在该行里、带 archives 一律不放行。
        static void CheckForged()
        {
            string create = Root + "create";
            byte[] body = Body(create, Listed, "svc01", So, null, false);
            ExpectRefused("rov forged no voucher", Cfg("create", null), create, body);
            ExpectRefused("rov forged dispatch", Cfg("create", Dl), create, Body(create, Listed, "svc01", Dl, null, false));
            string delete = Root + "delete";
            ExpectRefused("rov forged delete", Cfg("delete", So), delete, Body(delete, Listed, "svc01", So, null, false));
            BridgeConfig arc = Cfg("create", So);
            arc.ReadOnlyExceptions[0].Archives = new string[] { "customer" };
            ExpectRefused("rov forged archives", arc, create, body);
        }

        static void CheckPost()
        {
            ExpectPost("rov post create", Cfg("create", So), Ask("create", So, null, null), true);
            ExpectPost("rov post create dispatch", Cfg("create", So), Ask("create", Dl, null, null), false);
            ExpectPost("rov post unverify", Cfg("verify", So), Ask("verify", So, "unverify", null), true);
            ExpectPost("rov post close open", Cfg("close", So), Ask("close", So, "open", null), false);
            ExpectPost("rov post gen", Cfg("generate", Dl), Ask("generate", Dl, null, So), true);
            ExpectPost("rov post gen other", Cfg("generate", Dl), Ask("generate", Dl, null, "sale_return"), false);
        }

        // 入队后自检的一次请求：路由的 op、请求体 type、action、source_type（后两者 null 表示不带）。
        sealed class PostAsk
        {
            public string Op;
            public string Kind;
            public string Action;
            public string Source;
        }

        static PostAsk Ask(string op, string kind, string action, string source)
        {
            PostAsk ask = new PostAsk();
            ask.Op = op;
            ask.Kind = kind;
            ask.Action = action;
            ask.Source = source;
            return ask;
        }

        static void ExpectPost(string name, BridgeConfig cfg, PostAsk ask, bool ok)
        {
            WorkItem item = ReadOnlyExceptionSelfTest.Item(cfg, Root + ask.Op, null);
            item.Body["type"] = ask.Kind;
            if (ask.Action != null)
            {
                item.Body["action"] = ask.Action;
            }
            if (ask.Source != null)
            {
                item.Body["source_type"] = ask.Source;
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
            Expect(name, ok ? code == null && item.Policy == ReadOnlyGate.ExceptionCode
                : code == ReadOnlyGate.Code && item.Policy == ReadOnlyGate.Code);
        }

        static void CheckHealth()
        {
            try
            {
                ReadOnlyGate.Configure(Cfg("create", So));
                string json = ReadOnlyGate.HealthFragment();
                Expect("rov health", json.IndexOf("\"op\":\"create\",\"voucher\":\"sale_order\"", StringComparison.Ordinal) >= 0);
                Dictionary<string, object> map = (Dictionary<string, object>)ReadOnlyGate.ExceptionSummaries()[0];
                Expect("rov summary", (string)map["voucher"] == So);
                ReadOnlyGate.Configure(Cfg(VoucherHeadFix.Op, null));
                Expect("rov health fixed", ReadOnlyGate.HealthFragment().IndexOf("\"voucher\"", StringComparison.Ordinal) < 0);
            }
            finally
            {
                ReadOnlyGate.Configure(null);
            }
        }

        static string ActionOf(string op)
        {
            if (op == "verify" || op == "close" || op == "lock")
            {
                return op;
            }
            return null;
        }

        static Dictionary<string, object> Rule(string op, string voucher)
        {
            Dictionary<string, object> rule = ReadOnlyGateSelfTest.RuleDict();
            rule["type"] = "vouchers";
            rule["op"] = op;
            if (voucher != null)
            {
                rule["voucher"] = voucher;
            }
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
            rule.Operators = new string[] { "svc01" };
            rule.Type = "vouchers";
            rule.Op = op;
            rule.Voucher = voucher;
            cfg.ReadOnlyExceptions = new ReadOnlyExceptionRule[] { rule };
            return cfg;
        }

        static void ExpectAdmit(string name, BridgeConfig cfg, string path, byte[] body)
        {
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, path, body, draft);
            Expect(name, code == "bad_request" && draft.Policy == ReadOnlyGate.ExceptionCode);
        }

        static void ExpectRefused(string name, BridgeConfig cfg, string path, byte[] body)
        {
            AuditDraft draft = new AuditDraft();
            string code = ReadOnlyGateSelfTest.CodeWith(cfg, path, body, draft);
            Expect(name, code == ReadOnlyGate.Code && draft.Policy == ReadOnlyGate.Code);
        }

        // 只带该路由允许的键（Requests.RouteSpecs）；口令故意解不开：放行时在解密处 400，拒绝发生在解密之前。
        static byte[] Body(string path, string acc, string user, string type, string action, bool dry)
        {
            Dictionary<string, object> body = Login(acc, user);
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
            return Encode(body);
        }

        static byte[] GenBody(string source)
        {
            Dictionary<string, object> body = Login(Listed, "svc01");
            body["type"] = Dl;
            body["id"] = 7;
            body["source_type"] = source;
            return Encode(body);
        }

        static byte[] Legacy(string action)
        {
            Dictionary<string, object> body = Login(Listed, "svc01");
            body["id"] = 7;
            body["action"] = action;
            return Encode(body);
        }

        static Dictionary<string, object> Login(string acc, string user)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["acc"] = acc;
            body["year"] = "2026";
            body["operator"] = user;
            body["date"] = "2026-01-15";
            body["password_enc"] = "not-a-ciphertext";
            return body;
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
