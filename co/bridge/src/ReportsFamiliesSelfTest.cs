using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的分族报表接线部分（ReportsFamilies）：17 张报表都登记在名单、字段表、读线程池和权限登记表里，
    // 登录子系统、规则后缀、金额遮蔽标记符合约定。只测纯逻辑，不连库。各族自己的取值校验与 SQL 在各族的自检里。
    internal static class ReportsFamiliesSelfTest
    {
        static readonly string[] Expected = new string[]
        {
            "qm_indicators", "qm_pass_rate", "production_daily", "mo_progress", "mo_consumption", "stock_age",
            "purchase_analysis", "arrival_otif", "open_orders", "sales_price_history", "customer_churn",
            "receipts_payments", "notes_due", "cash_flow", "close_check", "ia_anomalies", "cost_unit"
        };

        public static void Run()
        {
            CheckNames();
            CheckRoutes();
            CheckSubs();
            CheckRules();
            CheckMask();
            CheckColumnPlans();
        }

        // 名单：17 张、不重复，都在 Reports.Names 里，ReportsFamilies 认得。
        static void CheckNames()
        {
            Expect("fam count", ReportsFamilies.Names.Length == Expected.Length);
            for (int i = 0; i < Expected.Length; i++)
            {
                Expect("fam name " + Expected[i], Array.IndexOf(ReportsFamilies.Names, Expected[i]) >= 0
                    && Array.IndexOf(Reports.Names, Expected[i]) >= 0 && ReportsFamilies.Owns(Expected[i]));
            }
            Expect("fam not others", !ReportsFamilies.Owns("close_status") && !ReportsFamilies.Owns("stock_summary"));
            for (int i = 0; i < Reports.Names.Length; i++)
            {
                Expect("report name unique " + Reports.Names[i], Array.IndexOf(Reports.Names, Reports.Names[i]) == i);
            }
        }

        // 路由：读线程池、读路由、不是写路由，字段表里查得到（路径取自 Dispatch 的分派表，字段取自 ReportSpecs）。
        static void CheckRoutes()
        {
            Dictionary<string, string[]> meta = new Dictionary<string, string[]>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, string[]> pair in Requests.MetaRoutes())
            {
                meta[pair.Key] = pair.Value;
            }
            for (int i = 0; i < Expected.Length; i++)
            {
                string path = Requests.ReportRoot + Expected[i];
                WorkItem item = Item(path, null);
                Expect("fam route " + path, PermRegistry.IsRead(path) && RouteClass.IsSqlRead(item) && !WriteGate.IsWrite(item));
                Expect("fam spec " + path, meta.ContainsKey(path) && meta[path] != null && Array.IndexOf(meta[path], "acc") >= 0);
            }
            Expect("summary amount field", Array.IndexOf(meta[Requests.ReportRoot + "stock_summary"], "include_amount") >= 0);
        }

        // 登录子系统（CONTRACT §1.1）：质量与生产订单随单据类型，其余固定；收付款、到期票据按 side。
        static void CheckSubs()
        {
            string[][] fixedSubs = new string[][]
            {
                new string[] { "production_daily", "ST" }, new string[] { "stock_age", "ST" },
                new string[] { "purchase_analysis", "PU" }, new string[] { "arrival_otif", "PU" },
                new string[] { "open_orders", "SA" }, new string[] { "sales_price_history", "SA" },
                new string[] { "customer_churn", "SA" }, new string[] { "cash_flow", "GL" },
                new string[] { "close_check", "GL" }, new string[] { "ia_anomalies", "IA" }, new string[] { "cost_unit", "GL" }
            };
            for (int i = 0; i < fixedSubs.Length; i++)
            {
                Expect("fam sub " + fixedSubs[i][0], Reports.SubOf(fixedSubs[i][0], Body(null, null)) == fixedSubs[i][1]);
            }
            Expect("sub receipts", Reports.SubOf("receipts_payments", Body("side", "ar")) == "AR"
                && Reports.SubOf("receipts_payments", Body("side", "ap")) == "AP");
            Expect("sub notes", Reports.SubOf("notes_due", Body("side", "ar")) == "AR"
                && Reports.SubOf("notes_due", Body("side", "ap")) == "AP");
            string[] others = new string[] { "qm_indicators", "qm_pass_rate", "mo_progress", "mo_consumption" };
            for (int i = 0; i < others.Length; i++)
            {
                Expect("sub " + others[i], !string.IsNullOrEmpty(Reports.SubOf(others[i], Body("kind", "incoming"))));
            }
        }

        // 权限规则：不分条的取 report:<name>；应收 / 应付、来料 / 产品按 side / kind 取 :ar|:ap、:incoming|:product，没带取前者。
        static void CheckRules()
        {
            for (int i = 0; i < Expected.Length; i++)
            {
                string name = Expected[i];
                string[] suffixes = PermRegistry.KeySuffixes(name);
                bool split = PermRegistry.ForKey("report:" + name) == null;
                if (!split)
                {
                    ExpectKey(name, Body(null, null), "report:" + name);
                    continue;
                }
                Expect("suffix count " + name, suffixes.Length == 2 && suffixes[0] != suffixes[1]);
                string field = suffixes[0] == "ar" ? "side" : "kind";
                ExpectKey(name, Body(null, null), "report:" + name + ":" + suffixes[0]);
                ExpectKey(name, Body(field, suffixes[0]), "report:" + name + ":" + suffixes[0]);
                ExpectKey(name, Body(field, suffixes[1]), "report:" + name + ":" + suffixes[1]);
            }
            Expect("suffix arap", PermRegistry.KeySuffixes("arap_aging")[1] == "ap" && PermRegistry.KeySuffixes("qm_pass_rate")[1] == "product");
            Expect("suffix default", PermRegistry.KeySuffixes("arap_writeoffs")[0] == "ar");
        }

        static void ExpectKey(string name, Dictionary<string, object> body, string key)
        {
            WorkItem item = Item(Requests.ReportRoot + name, body);
            PermRule rule = PermRegistry.Find(item);
            Expect("fam rule " + key, rule != null && rule.Key == key && rule == PermRegistry.ForKey(key) && rule.Auths.Length > 0);
        }

        // 金额遮蔽：标记符与已有的 masked_fields 取并集、排序、去重；规则 mask:amount 只收 IA3011_01。
        static void CheckMask()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            PermRegistry.NoteAmountMasked(body);
            PermRegistry.NoteAmountMasked(body);
            List<string> names = body[PermMask.Key] as List<string>;
            Expect("mask amount", names != null && names.Count == 1 && names[0] == "amount");
            Dictionary<string, object> more = new Dictionary<string, object>();
            more[PermMask.Key] = new List<string> { "iUnitCost" };
            PermRegistry.NoteAmountMasked(more);
            names = more[PermMask.Key] as List<string>;
            Expect("mask union", names != null && names.Count == 2 && names[0] == "amount" && names[1] == "iUnitCost");
            PermRule rule = PermRegistry.ForKey(PermRegistry.AmountRule);
            Expect("mask rule", rule != null && rule.Auths.Length == 1 && rule.Auths[0] == "IA3011_01" && rule.Objs.Length == 0);
        }

        // 字段权限范围（PermColumnReports）：有金额的报表按单据类型的对象遮，没有金额或没有字段权限对象的不遮。
        static void CheckColumnPlans()
        {
            ExpectPlan("purchase_analysis", Body("source", "receipt"), "", "24");
            ExpectPlan("purchase_analysis", Body(null, null), "", "25");
            ExpectPlan("open_orders", Body(null, null), "", "17");
            ExpectPlan("sales_price_history", Body(null, null), "", "07");
            ExpectPlan("customer_churn", Body(null, null), "", "07");
            ExpectPlan("receipts_payments", Body("side", "ar"), "", "VCH_AR48");
            ExpectPlan("receipts_payments", Body("side", "ap"), "", "VCH_AP49");
            ExpectPlan("ia_anomalies", Body(null, null), "unbilled", "24");
            ExpectPlan("stock_summary", Body(null, null), "", "0303");
            ExpectPlan("stock_age", Body(null, null), "", "0303");
            string[] plain = new string[]
            {
                "qm_indicators", "qm_pass_rate", "production_daily", "mo_progress", "mo_consumption", "arrival_otif", "notes_due",
                "cash_flow", "close_check", "cost_unit"
            };
            for (int i = 0; i < plain.Length; i++)
            {
                Expect("plan none " + plain[i], PermColumnMap.Plan(Item(Requests.ReportRoot + plain[i], null)).Count == 0);
            }
            Expect("keys sale order", PermColumnMap.KeysOfType("sale_order") != null && PermColumnMap.KeysOfType("sale_invoice") != null
                && PermColumnMap.KeysOfType("purchase_invoice") != null);
            Expect("alias money", PermColumnMap.IsMoneyKey("amount_tax") && PermColumnMap.IsMoneyKey("unit_price_tax")
                && PermColumnMap.IsMoneyKey("est_amount") && PermColumnMap.IsMoneyKey("net_amount") && PermColumnMap.IsMoneyKey("closing_amt")
                && !PermColumnMap.IsMoneyKey("qty"));
        }

        static void ExpectPlan(string name, Dictionary<string, object> body, string section, string key)
        {
            List<ColScope> plan = PermColumnMap.Plan(Item(Requests.ReportRoot + name, body));
            Expect("plan " + name + " " + key, plan.Count == 1 && plan[0].Section == section && plan[0].Keys != null
                && Array.IndexOf(plan[0].Keys, key) >= 0);
        }

        static Dictionary<string, object> Body(string key, string value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (key != null)
            {
                body[key] = value;
            }
            return body;
        }

        static WorkItem Item(string path, Dictionary<string, object> body)
        {
            WorkItem item = new WorkItem();
            item.Path = path;
            item.Body = body ?? new Dictionary<string, object>();
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
