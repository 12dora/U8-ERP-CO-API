using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户、供应商子档案部分（ArcPartner）：只测纯逻辑（档案登记、锁键、权限登记、默认账户规则、报文），不连库、不建 COM。
    internal static class ArcPartnerSelfTest
    {
        public static void Run()
        {
            CheckKinds();
            CheckRules();
            CheckDefaults();
            CheckContact();
            CheckOpenCode();
        }

        static void CheckKinds()
        {
            string[] names = new string[] { ArcPartner.CustomerBank, ArcPartner.VendorBank, ArcPartner.CustomerContact, ArcPartner.VendorContact };
            for (int i = 0; i < names.Length; i++)
            {
                ArcKind k = ArcKind.Find(names[i]);
                Expect("partner kind " + names[i], k != null && !k.ReadOnly && k.RoRead && ArcPartner.Is(k) && ArcPair.Of(k) != null);
            }
            Expect("partner code max", ArcKind.Find(ArcPartner.CustomerBank).CodeMax == 71 && ArcKind.Find(ArcPartner.VendorContact).CodeMax == 51);
            string[] keys = ArcPartner.LockKeys(ArcPartner.VendorBank, "V01:6222");
            Expect("partner lock keys", keys.Length == 2 && keys[0] == "arc:vendor:V01" && keys[1] == "arc:vendor_bank:V01:6222");
            Expect("partner lock other", ArcPartner.LockKeys("customer", "C01") == null);
        }

        static void CheckRules()
        {
            string[] names = new string[] { ArcPartner.CustomerBank, ArcPartner.VendorBank, ArcPartner.CustomerContact, ArcPartner.VendorContact };
            string[] ops = new string[] { "create", "update", "delete" };
            for (int i = 0; i < names.Length; i++)
            {
                Expect("partner read rule " + names[i], PermRegistry.ForKey("archive:" + names[i]) != null);
                for (int j = 0; j < ops.Length; j++)
                {
                    Expect("partner write rule " + names[i] + " " + ops[j], PermRegistry.ForKey(ArcGuard.RuleKey(names[i], ops[j])) != null);
                }
            }
        }

        // 已有 A（默认）、B：新增默认的 C 把 A 清成 0；默认账户不能取消默认、不能在还有其他账户时删除；第一个账户必须是默认。
        static void CheckDefaults()
        {
            BankPlan plan = Decide("create", "C01:C", Fields("branch", "x", "default", true), 2);
            Expect("bank add default", plan.After.Count == 3 && plan.After[0]["bDefault"] == "0" && plan.After[2]["bDefault"] == "1"
                && plan.DefaultRow() == plan.Target);
            plan = Decide("create", "C01:C", Fields("branch", "x"), 2);
            Expect("bank add plain", plan.After[0]["bDefault"] == "1" && plan.Target["bDefault"] == "0");
            plan = Decide("create", "C01:C", Fields("branch", "x"), 0);
            Expect("bank add first", plan.Target["bDefault"] == "1");
            plan = Decide("delete", "C01:B", null, 2);
            Expect("bank delete plain", plan.Deleted && plan.After.Count == 1 && plan.Target == null);
            plan = Decide("update", "C01:B", Fields("default", "1"), 2);
            Expect("bank switch default", plan.After[0]["bDefault"] == "0" && plan.After[1]["bDefault"] == "1");
            Rejected("bank first not default", "create", "C01:C", Fields("branch", "x", "default", false), 0, 409);
            Rejected("bank undefault", "update", "C01:A", Fields("default", "0"), 2, 409);
            Rejected("bank delete default", "delete", "C01:A", null, 2, 409);
            Rejected("bank exists", "create", "C01:a", Fields("branch", "x"), 2, 409);
            Rejected("bank missing", "delete", "C01:Z", null, 2, 404);
        }

        static void CheckContact()
        {
            ArcBag bag = new ArcBag();
            bag.Put("sex", "男");
            bag.Put("name", "张三&");
            string xml = ArcPartnerContact.Envelope("add", "C01", "L1", bag);
            Expect("contact envelope", xml.IndexOf("roottag='customerlinker'", StringComparison.Ordinal) > 0
                && xml.IndexOf("<customerlinker><code>L1</code><name>张三&amp;</name><of_customer>C01</of_customer><sex>男</sex></customerlinker>",
                    StringComparison.Ordinal) > 0);
            xml = ArcPartnerContact.Envelope("Delete", "C01", "L2", new ArcBag());
            Expect("contact delete envelope", xml.IndexOf("proc='Delete'", StringComparison.Ordinal) > 0
                && xml.IndexOf("<customerlinker><code>L2</code><of_customer>C01</of_customer></customerlinker>", StringComparison.Ordinal) > 0);
            Rejected400("contact sex", delegate
            {
                ArcPartnerContactMap.Check(Req("create", ArcPartner.CustomerContact, "C01:L1", Fields("name", "a", "sex", "M")));
            });
            Rejected400("contact birthday", delegate
            {
                ArcPartnerContactMap.Check(Req("update", ArcPartner.CustomerContact, "C01:L1", Fields("birthday", "2026-02-30")));
            });
            ArcPartnerContactMap.Check(Req("update", ArcPartner.CustomerContact, "C01:L1", Fields("be_main_linker", true, "marriage", "未婚")));
            CheckVendorContact();
        }

        // 供应商联系人：报文 code 留空、of_vendor；不开放 position、favorite；新增编码第二段留空。
        static void CheckVendorContact()
        {
            ArcReq req = Req("create", ArcPartner.VendorContact, "V01:", Fields("name", "张三", "mobile", "13800000000", "be_main_linker", false));
            string xml = ArcVenContact.Envelope("V01", req.Fields);
            Expect("vendor contact envelope", xml.IndexOf("roottag='vendorcontact'", StringComparison.Ordinal) > 0
                && xml.IndexOf("<vendorcontact><code></code><name>张三</name><of_vendor>V01</of_vendor><mobile>13800000000</mobile>"
                    + "<be_main_linker>0</be_main_linker></vendorcontact>", StringComparison.Ordinal) > 0);
            Rejected400("vendor contact position", delegate
            {
                Req("update", ArcPartner.VendorContact, "V01:L1", Fields("position", "x"));
            });
            Req("update", ArcPartner.VendorContact, "V01:L1", Fields("memo", "x", "self_define1", "y"));
            string[] parts = ArcPair.Of(ArcKind.Find(ArcPartner.VendorContact)).Split("V01:", "code");
            Expect("vendor contact open code", parts[0] == "V01" && parts[1] == "");
            Expect("vendor contact pairs", ArcPartnerContactMap.VendorPairs().Length == ArcPartnerContactMap.Pairs().Length - 4);
        }

        // 客户联系人新增的编码写成 "<客户编码>:"（U8 自动编号）；其他两列主键档案的空第二段仍 400。
        static void CheckOpenCode()
        {
            string[] parts = ArcPair.Of(ArcKind.Find(ArcPartner.CustomerContact)).Split("C01:", "code");
            Expect("contact open code", parts[0] == "C01" && parts[1] == "");
            Rejected400("bank empty second", delegate
            {
                ArcPair.Of(ArcKind.Find(ArcPartner.CustomerBank)).Split("C01:", "code");
            });
        }

        // before 个现有账户 A、B、…（A 是默认），按请求算计划。
        static BankPlan Decide(string op, string code, Dictionary<string, object> fields, int before)
        {
            string archive = code.StartsWith("V", StringComparison.Ordinal) ? ArcPartner.VendorBank : ArcPartner.CustomerBank;
            ArcReq req = Req(op, archive, code, fields);
            BankPlan plan = ArcPartnerBank.Start(req);
            plan.Before = new List<Dictionary<string, string>>();
            for (int i = 0; i < before; i++)
            {
                Dictionary<string, string> row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                row["cAccountNum"] = ((char)('A' + i)).ToString();
                row["cBranch"] = "b";
                row["bDefault"] = i == 0 ? "1" : "0";
                plan.Before.Add(row);
            }
            ArcPartnerBank.Decide(plan, req);
            return plan;
        }

        static void Rejected(string name, string op, string code, Dictionary<string, object> fields, int before, int status)
        {
            try
            {
                Decide(op, code, fields, before);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Rejected400(string name, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static ArcReq Req(string op, string archive, string code, Dictionary<string, object> fields)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = archive;
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
