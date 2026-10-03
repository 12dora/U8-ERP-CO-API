using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的采购结算单写入部分（PuSettleSelfTest.Run 调用）：请求规则（不收 lines、表头只收 settle_date、
    // settle_date 替换登录日期）、meta 的生单规格、预演模式、写规则的功能 id 与数据权限行、删除另锁 new:purchase_settle、
    // 签名表。只跑纯函数，不连库。
    internal static class PuSettleWriteSelfTest
    {
        public static void Run()
        {
            CheckRequest();
            CheckMeta();
            CheckWiring();
        }

        static void CheckRequest()
        {
            WorkItem item = Item(Head("settle_date", "2025-09-30"), new object[0]);
            PuSettleReq.Apply(item);
            Expect("settle date login", item.Date == "2025-09-30");
            Dictionary<string, object> twice = Head("settle_date", "2025-09-30");
            twice["Settle_Date"] = "2025-09-29";
            Refused("settle date twice", Item(twice, new object[0]), "head.Settle_Date");
            DateTime today = new DateTime(2025, 10, 3);
            PuSettleReq.CheckDay("2025-10-03", today);
            PuSettleReq.CheckDay("2024-12-31", today);
            DayRefused("settle date future", "2025-10-04", today);
            WorkItem plain = Item(new Dictionary<string, object>(), new object[0]);
            PuSettleReq.Apply(plain);
            Expect("settle date kept", plain.Date == "2026-10-03");
            Refused("settle lines", Item(null, new object[] { new Dictionary<string, object>() }), "lines");
            Refused("settle head key", Item(Head("cMemo", "x"), new object[0]), "head.cMemo");
            Refused("settle date format", Item(Head("settle_date", "2025/09/30"), new object[0]), "head.settle_date");
            Refused("settle date type", Item(Head("SETTLE_DATE", 20260930), new object[0]), "head.SETTLE_DATE");
            WorkItem other = Item(Head("settle_date", "2025-09-30"), new object[0]);
            other.Type = Kinds.Find("purchase_invoice");
            PuSettleReq.Apply(other);
            Expect("settle other kind", other.Date == "2026-10-03");
        }

        static void CheckMeta()
        {
            Dictionary<string, object> spec = MetaGenerate.Of(PuSettleRead.KindName, "purchase_invoice");
            Dictionary<string, object> head = spec["head"] as Dictionary<string, object>;
            string[] exact = head == null ? null : head["exact"] as string[];
            Expect("settle meta head", exact != null && exact.Length == 1 && exact[0] == PuSettleReq.DateKey);
            Expect("settle meta lines", spec["lines"] == null && (int)spec["lines_max"] == 0);
            VoucherKind kind = Kinds.Find(PuSettleRead.KindName);
            Dictionary<string, object> writable = MetaWritable.Of(kind);
            Expect("settle meta ops", writable["create"] != null && writable["update"] == null);
        }

        static void CheckWiring()
        {
            Expect("settle dry generate", DryRunModes.Lookup("vouchers/generate", PuSettleRead.KindName, "generate",
                "purchase_invoice") == DryRunModes.Rollback);
            Expect("settle dry delete", DryRunModes.Lookup("vouchers/delete", PuSettleRead.KindName, "delete", "")
                == DryRunModes.Rollback);
            Expect("settle dry update", DryRunModes.Lookup("vouchers/update", PuSettleRead.KindName, "update", "")
                == DryRunModes.Refuse);
            Rule("settle rule generate", PuSettleReq.GenerateRule, "PU040301");
            Rule("settle rule delete", PuSettleReq.DeleteRule, "PU040315");
            Dictionary<string, object> head = Head("cVenCode", "V01");
            head["cPTCode"] = "01";
            Dictionary<string, object> line = Head("cInvCode", "M-1");
            line["cWhCode"] = "W1";
            List<Dictionary<string, object>> rows = PuSettleGate.PermRows(head,
                new List<Dictionary<string, object>> { line });
            Expect("settle perm rows", rows.Count == 1 && (string)rows[0]["cVenCode"] == "V01"
                && (string)rows[0]["cInvCode"] == "M-1" && (string)rows[0]["cWhCode"] == "W1"
                && (string)rows[0]["cDepCode"] == "" && (string)rows[0]["cPTCode"] == "01");
            WorkItem del = Item(null, null);
            del.Path = "/u8co/v1/vouchers/delete";
            del.HasId = true;
            del.Id = 7;
            string[] keys = DocLocks.KeysOf(del);
            Expect("settle delete locks", Array.IndexOf(keys, "purchase_settle:7") >= 0
                && Array.IndexOf(keys, "new:purchase_settle") >= 0);
            Expect("settle sigs", Sig("CheckSettle") && Sig("bRdBVAutoSettle"));
        }

        static void Rule(string name, string key, string auth)
        {
            PermRule rule = PermRegistry.ForKey(key);
            Expect(name, rule != null && rule.Auths.Length == 1 && rule.Auths[0] == auth && rule.Objs.Length == 6);
        }

        static bool Sig(string member)
        {
            foreach (SigNeed need in SigTable.Build())
            {
                if (need.ProgId == "VoucherCO_PU.clsVoucherCO_PU" && need.Member == member
                    && need.Routes.IndexOf("generate", StringComparison.Ordinal) >= 0)
                {
                    return true;
                }
            }
            return false;
        }

        static void Refused(string name, WorkItem item, string field)
        {
            try
            {
                PuSettleReq.Apply(item);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void DayRefused(string name, string date, DateTime today)
        {
            try
            {
                PuSettleReq.CheckDay(date, today);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == "head.settle_date");
                return;
            }
            throw new InvalidOperationException(name);
        }

        static WorkItem Item(Dictionary<string, object> head, object[] lines)
        {
            WorkItem item = new WorkItem();
            item.Type = Kinds.Find(PuSettleRead.KindName);
            item.Path = "/u8co/v1/vouchers/generate";
            item.Date = "2026-10-03";
            item.Year = "2025";
            item.Head = head;
            item.Lines = lines;
            return item;
        }

        static Dictionary<string, object> Head(string key, object value)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head[key] = value;
            return head;
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
