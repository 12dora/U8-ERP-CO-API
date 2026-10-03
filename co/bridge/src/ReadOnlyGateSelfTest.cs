using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的只读账套部分（ReadOnlyGate）：走 Requests.ForPath 的真实解析。名单里的账套对 WriteGate 登记的
    // 每条写路由（枚举登记表，新增写路由自动覆盖）都 403 account_read_only，含预演，写入策略放行、测试账套名单也不放行；
    // 拒绝发生在解密之前（口令故意写成解不开的值，放行时是 400 口令无法解密）。名单外账套不受影响，读路由照常通过。
    // 不连库、不建 COM、不登录。由 SelfTest.RunWritePolicy 调用。
    internal static class ReadOnlyGateSelfTest
    {
        const string Listed = "998";
        const string Other = "997";

        public static void Run()
        {
            CheckConfig();
            CheckHealth();
            WritePolicy.ResetForTest();
            try
            {
                CheckWrites();
                // 写入策略文件放行全部写入时，只读账套照样拒绝。
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
            Expect("ro listed", BridgeConfig.IsReadOnlyAccount(Cfg(), Listed));
            Expect("ro other", !BridgeConfig.IsReadOnlyAccount(Cfg(), Other));
            Expect("ro null cfg", !BridgeConfig.IsReadOnlyAccount(null, Listed));
        }

        static void CheckHealth()
        {
            try
            {
                ReadOnlyGate.Configure(Cfg());
                Expect("ro health", ReadOnlyGate.HealthFragment() == ",\"read_only_accounts\":[\"" + Listed + "\"]");
                Expect("ro meta", ReadOnlyGate.Accounts().Length == 1 && ReadOnlyGate.Accounts()[0] == Listed);
            }
            finally
            {
                ReadOnlyGate.Configure(null);
            }
            Expect("ro health empty", ReadOnlyGate.HealthFragment() == ",\"read_only_accounts\":[]");
        }

        // 每条写路由：名单内账套（普通与预演）403 account_read_only、审计 policy 同名；名单外账套走到解密（400）。
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

        // 读路由（Dispatch 路由表里不在写闸门登记表的）对名单内账套不报 account_read_only。
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

        // 入队后的再查：名单内账套的写任务 403，读任务、名单外账套放行。
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
        }

        // 经 Requests.ForPath 解析，返回错误码（没有抛出时为 null）。
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
                // 读路由各自的字段校验与本闸门无关。
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
            // 解不开的口令：闸门放行时报 400 口令无法解密，证明拒绝在解密之前。
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
            // 第二级写入开关和测试账套名单都不能放行只读账套。
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

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
