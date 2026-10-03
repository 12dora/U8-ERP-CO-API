using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的应收 / 应付期初单据部分（openings/arap）：请求校验、期初标志与方向、单据日期、
    // 登记（写闸门、幂等、预演、锁键、权限）、测试账套名单。只跑纯函数，不连库、不建 COM。
    internal static class OpeningsArapSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckMarks();
            CheckWiring();
            TestAccountGateSelfTest.Check("arap open", TestAccountGate.OpeningsArapText);
        }

        static void CheckParse()
        {
            Dictionary<string, object> body = Create("ar", 1234.5m);
            body["department"] = " 01 ";
            OpeningsArapAsk ask = OpeningsArapReq.Parse(body);
            Expect("arap open create", Describe(ask) == "ar|create|0|C001|1234.5|1122|01||0");
            Expect("arap open sub", Wiring(ask) == "AR|ar_bill|" + OpeningsArapReq.ArRule + "|opening_arap_create");
            body = Create("ap", -88m);
            body["currency"] = "USD";
            body["exch_rate"] = 7.1;
            ask = OpeningsArapReq.Parse(body);
            Expect("arap open negative", Describe(ask) == "ap|create|0|C001|-88|1122||USD|7.1");
            Expect("arap open ap", Wiring(ask) == "AP|ap_bill|" + OpeningsArapReq.ApRule + "|opening_arap_create");
            Expect("arap open unverify", Describe(OpeningsArapReq.Parse(ById("ap", "unverify", 42))) == "ap|unverify|42||0||||0");
            ask = OpeningsArapReq.Parse(ById("ar", "delete", 7L));
            Expect("arap open delete", Describe(ask) == "ar|delete|7||0||||0" && !ask.Create);
        }

        static string Describe(OpeningsArapAsk a)
        {
            return string.Join("|", new string[]
            {
                a.Side, a.Action, Num(a.Id), a.Partner ?? "", Num(a.Amount), a.Account ?? "", a.Department ?? "",
                a.Currency ?? "", Num(a.Rate)
            });
        }

        static string Wiring(OpeningsArapAsk a)
        {
            return string.Join("|", new string[]
            {
                OpeningsArapReq.SubOf(a), OpeningsArapReq.KindOf(a), OpeningsArapReq.RuleKey(a), OpeningsArapReq.AuditAction(a)
            });
        }

        static string Num(IFormattable value)
        {
            return value.ToString(null, System.Globalization.CultureInfo.InvariantCulture);
        }

        static void CheckBad()
        {
            Bad("arap open side", Create("AR", 1m), "side");
            Bad("arap open action", ById("ar", "close", 1), "action");
            Dictionary<string, object> body = Create("ar", 1m);
            body["id"] = 3;
            Bad("arap open create id", body, "id");
            body = ById("ar", "verify", 3);
            body["amount"] = 5;
            Bad("arap open verify amount", body, "amount");
            Bad("arap open zero", Create("ar", 0m), "amount");
            Bad("arap open cents", Create("ar", 1.005m), "amount");
            body = Create("ar", 1m);
            body["amount"] = "100";
            Bad("arap open amount text", body, "amount");
            body = Create("ar", 1m);
            body.Remove("partner");
            Bad("arap open partner", body, "partner");
            body = Create("ar", 1m);
            body["account"] = "  ";
            Bad("arap open account", body, "account");
            body = Create("ar", 1m);
            body["exch_rate"] = 0;
            Bad("arap open rate", body, "exch_rate");
            Bad("arap open no id", ById("ar", "verify", null), "id");
            Bad("arap open id zero", ById("ar", "verify", 0), "id");
            Bad("arap open id text", ById("ar", "verify", "5"), "id");
        }

        static void CheckMarks()
        {
            ArapSpec ar = ArapReq.Spec(Kinds.Find("ar_bill"));
            ArapSpec ap = ArapReq.Spec(Kinds.Find("ap_bill"));
            Expect("arap open marks ar", Marks(ar, false) == "True|True" && Marks(ar, true) == "True|False");
            Expect("arap open marks ap", Marks(ap, false) == "True|False" && Marks(ap, true) == "True|True");
            Expect("arap open date", OpeningsArap.DayBefore("2024-01-01") == "2023-12-31"
                && OpeningsArap.DayBefore("2024-12-01") == "2024-11-30" && OpeningsArap.DayBefore("2024-03-01") == "2024-02-29");
        }

        static string Marks(ArapSpec spec, bool reverse)
        {
            Dictionary<string, string> head = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            OpeningsArap.OpeningMarks(head, spec, reverse);
            return head["bStartFlag"] + "|" + head["bd_c"];
        }

        static void CheckWiring()
        {
            string path = OpeningsArapReq.Path;
            Expect("arap open write", WriteGate.IsWrite(path) && IdemReq.Supports(path) && DryRunReq.Accepts(path));
            Expect("arap open dry mode", DryRunModes.Lookup(path, "", "opening_arap_create", "") == DryRunModes.Rollback
                && (string)MetaDryRun.Routes()["openings/arap"] == DryRunModes.Rollback);
            Expect("arap open perm", PermRegistry.ForKey(OpeningsArapReq.ArRule) != null
                && PermRegistry.ForKey(OpeningsArapReq.ApRule) != null && !PermRegistry.IsRead(path));
            Expect("arap open lock create", Locks(Create("ar", 1m), "new:ar_bill"));
            Expect("arap open lock id", Locks(ById("ap", "verify", 9), "ap_bill:9"));
        }

        static bool Locks(Dictionary<string, object> body, string want)
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = OpeningsArapReq.Path;
            item.Body = body;
            string[] keys = DocLocks.KeysOf(item);
            return Array.IndexOf(keys, want) >= 0 && Array.IndexOf(keys, WriteGate.Key) >= 0 && !RouteClass.IsSqlRead(item);
        }

        static Dictionary<string, object> Create(string side, decimal amount)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["side"] = side;
            body["action"] = "create";
            body["partner"] = "C001";
            body["amount"] = amount;
            body["account"] = "1122";
            return body;
        }

        static Dictionary<string, object> ById(string side, string action, object id)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["side"] = side;
            body["action"] = action;
            if (id != null)
            {
                body["id"] = id;
            }
            return body;
        }

        static void Bad(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                OpeningsArapReq.Parse(body);
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
