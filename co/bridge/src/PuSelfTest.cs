using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的采购类报表部分（purchase_analysis、arrival_otif）：登记项、参数解析与拒绝、SQL 的 ? 个数与参数一致、
    // 行转换（数量空值、金额取整、计数）。不连库。
    internal static class PuSelfTest
    {
        public static void Run()
        {
            Family();
            PurchaseDefaults();
            PurchaseValues();
            PurchaseRefused();
            ArrivalValues();
            ArrivalRefused();
            PurchaseSql();
            PurchaseTotalsSql();
            ArrivalSql();
            ArrivalOtherSql();
            PurchaseRows();
            ArrivalRows();
            OverdueRows();
        }

        static void Family()
        {
            Expect("owns purchase", ReportsPuReq.Owns("purchase_analysis") && ReportsPuReq.Owns("arrival_otif"));
            Expect("owns other", !ReportsPuReq.Owns("customer_credit") && !ReportsPuReq.Owns(""));
            Expect("sub", ReportsPuReq.SubOf("arrival_otif", null) == "PU" && ReportsPuReq.SubOf("purchase_analysis", null) == "PU");
            Expect("sub other", ReportsPuReq.SubOf("stock_summary", null) == null);
            Expect("specs", ReportsPuReq.Specs.Length == 2 && ReportsPuReq.Specs[0][0] == Requests.ReportRoot + "purchase_analysis"
                && ReportsPuReq.Specs[1][0] == Requests.ReportRoot + "arrival_otif");
            Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map =
                new Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>();
            ReportsPuReq.AddRunners(map);
            Expect("runners", map.Count == 2 && map.ContainsKey("purchase_analysis") && map.ContainsKey("arrival_otif"));
            ReportsPuReq.Check("purchase_analysis", Body("date_from", "2026-09-01"));
            ReportsPuReq.Check("arrival_otif", Body("date_from", "2026-09-01"));
            ReportsPuReq.Check("stock_summary", new Dictionary<string, object>());
        }

        static void PurchaseDefaults()
        {
            PuArgs a = ReportsPuReq.ParsePurchase(Body("date_from", "2026-09-01"));
            Expect("default dates", a.DateFrom == "2026-09-01" && a.DateTo.Length == 0);
            Expect("default group", a.GroupBy.Length == 1 && a.GroupBy[0] == "material");
            Expect("default source", !a.Receipt && a.Limit == 200);
            Expect("default filters", a.Inv.Length == 0 && a.Vendor.Length == 0 && a.InvClass.Length == 0);
        }

        static void PurchaseValues()
        {
            Dictionary<string, object> body = Body("date_from", "2026-09-01");
            body["date_to"] = "2026-09-30";
            body["group_by"] = new object[] { "vendor", "month", "material" };
            body["inv"] = new ArrayList(new object[] { " 9909001 ", "9909002" });
            body["vendor"] = new object[] { "S900001" };
            body["inv_class"] = "01.02";
            body["source"] = "receipt";
            body["limit"] = 1000;
            PuArgs a = ReportsPuReq.ParsePurchase(body);
            Expect("values dims", a.GroupBy.Length == 3 && a.GroupBy[0] == "vendor" && a.GroupBy[2] == "material");
            Expect("values codes", a.Inv.Length == 2 && a.Inv[0] == "9909001" && a.Vendor[0] == "S900001");
            Expect("values rest", a.Receipt && a.Limit == 1000 && a.InvClass == "01.02" && a.DateTo == "2026-09-30");
            Dictionary<string, object> same = Body("date_from", "2026-09-30");
            same["date_to"] = "2026-09-30";
            Expect("same day", ReportsPuReq.ParsePurchase(same).DateTo == "2026-09-30");
        }

        static void PurchaseRefused()
        {
            Refused("no date_from", delegate { ReportsPuReq.ParsePurchase(new Dictionary<string, object>()); }, "date_from");
            Refused("bad date", delegate { ReportsPuReq.ParsePurchase(Body("date_from", "2026-9-1")); }, "date_from");
            Refused("date order", delegate { ReportsPuReq.ParsePurchase(With("date_to", "2026-08-31")); }, "date_from");
            Refused("group empty", delegate { ReportsPuReq.ParsePurchase(With("group_by", new object[0])); }, "group_by");
            Refused("group four", delegate
            {
                ReportsPuReq.ParsePurchase(With("group_by", new object[] { "material", "vendor", "month", "month" }));
            }, "group_by");
            Refused("group unknown", delegate { ReportsPuReq.ParsePurchase(With("group_by", new object[] { "day" })); }, "group_by.0");
            Refused("group dup", delegate
            {
                ReportsPuReq.ParsePurchase(With("group_by", new object[] { "vendor", "vendor" }));
            }, "group_by.1");
            Refused("group scalar", delegate { ReportsPuReq.ParsePurchase(With("group_by", "material")); }, "group_by");
            Refused("source", delegate { ReportsPuReq.ParsePurchase(With("source", "order")); }, "source");
            Refused("source type", delegate { ReportsPuReq.ParsePurchase(With("source", 1)); }, "source");
            Refused("limit zero", delegate { ReportsPuReq.ParsePurchase(With("limit", 0)); }, "limit");
            Refused("limit big", delegate { ReportsPuReq.ParsePurchase(With("limit", 1001)); }, "limit");
            Refused("limit text", delegate { ReportsPuReq.ParsePurchase(With("limit", "5")); }, "limit");
            Refused("inv empty", delegate { ReportsPuReq.ParsePurchase(With("inv", new object[0])); }, "inv");
            Refused("inv many", delegate { ReportsPuReq.ParsePurchase(With("inv", new object[21])); }, "inv");
            Refused("inv blank", delegate { ReportsPuReq.ParsePurchase(With("inv", new object[] { " " })); }, "inv.0");
            Refused("inv control", delegate { ReportsPuReq.ParsePurchase(With("inv", new object[] { "a\tb" })); }, "inv.0");
            Refused("vendor long", delegate
            {
                ReportsPuReq.ParsePurchase(With("vendor", new object[] { new string('x', 61) }));
            }, "vendor.0");
            Refused("class wildcard", delegate { ReportsPuReq.ParsePurchase(With("inv_class", "01%")); }, "inv_class");
            Refused("check routes", delegate { ReportsPuReq.Check("purchase_analysis", With("limit", 0)); }, "limit");
        }

        static void ArrivalValues()
        {
            PuArgs d = ReportsPuReq.ParseArrival(Body("date_from", "2026-09-01"));
            Expect("arrival default", d.Group == "vendor" && d.OverdueLimit == 50 && d.DateTo.Length == 0);
            Dictionary<string, object> body = Body("date_from", "2026-09-01");
            body["date_to"] = "2026-09-30";
            body["group_by"] = "none";
            body["overdue_limit"] = 200;
            body["vendor"] = new object[] { "S900001" };
            body["inv"] = new object[] { "9909001" };
            PuArgs a = ReportsPuReq.ParseArrival(body);
            Expect("arrival values", a.Group == "none" && a.OverdueLimit == 200 && a.Vendor.Length == 1 && a.Inv.Length == 1);
            Expect("arrival material", ReportsPuReq.ParseArrival(With("group_by", "material")).Group == "material");
        }

        static void ArrivalRefused()
        {
            Refused("arr no date", delegate { ReportsPuReq.ParseArrival(new Dictionary<string, object>()); }, "date_from");
            Refused("arr date order", delegate { ReportsPuReq.ParseArrival(With("date_to", "2026-08-31")); }, "date_from");
            Refused("arr group", delegate { ReportsPuReq.ParseArrival(With("group_by", "month")); }, "group_by");
            Refused("arr group array", delegate { ReportsPuReq.ParseArrival(With("group_by", new object[] { "vendor" })); }, "group_by");
            Refused("arr overdue zero", delegate { ReportsPuReq.ParseArrival(With("overdue_limit", 0)); }, "overdue_limit");
            Refused("arr overdue big", delegate { ReportsPuReq.ParseArrival(With("overdue_limit", 201)); }, "overdue_limit");
            Refused("arr vendor", delegate { ReportsPuReq.ParseArrival(With("vendor", "S900001")); }, "vendor");
            Refused("arr check", delegate { ReportsPuReq.Check("arrival_otif", With("overdue_limit", 0)); }, "overdue_limit");
        }

        static void PurchaseSql()
        {
            PuArgs a = ReportsPuReq.ParsePurchase(With("group_by", new object[] { "material", "vendor", "month" }));
            List<object> ps = new List<object>();
            string sql = ReportsPuSql.GroupSql(a, "2026-09-30", ps);
            Expect("pu group marks", Marks(sql) == ps.Count, ps.Count == 3, !sql.Contains("{KEYS}"), !sql.Contains("{FILTER}"));
            Expect("pu group last", (int)ps[2] == 201, sql.Contains("{PERM}"), sql.Contains("PurBillVouch h"));
            Expect("pu group keys", sql.Contains("inv_code"), sql.Contains("vendor_code"), sql.Contains("mon"),
                sql.Contains("JOIN Inventory"), sql.Contains("JOIN Vendor"));
            string month = ReportsPuSql.GroupSql(ReportsPuReq.ParsePurchase(With("group_by", new object[] { "month" })),
                "2026-09-30", new List<object>());
            Expect("pu month only", !month.Contains("JOIN Inventory"), !month.Contains("JOIN Vendor"), month.Contains("mon"));
            // 字段权限会遮金额时不按金额排序、截断：按分组键。
            Expect("pu amount order", sql.Contains("ORDER BY SUM(l.amount) DESC, "));
            a.NoAmountOrder = true;
            string plain = ReportsPuSql.GroupSql(a, "2026-09-30", new List<object>());
            Expect("pu masked order", !plain.Contains("SUM(l.amount) DESC"), plain.Contains(" ORDER BY l.cInvCode"), !plain.Contains("{ORD}"));
        }

        static void PurchaseTotalsSql()
        {
            Dictionary<string, object> body = With("source", "receipt");
            body["inv"] = new object[] { "9909001", "9909002" };
            body["inv_class"] = "01";
            body["vendor"] = new object[] { "S900001" };
            PuArgs r = ReportsPuReq.ParsePurchase(body);
            List<object> rp = new List<object>();
            string totals = ReportsPuSql.TotalsSql(r, "2026-09-30", rp);
            Expect("pu totals marks", Marks(totals) == rp.Count, rp.Count == 6, totals.Contains("RdRecord01 h"));
            Expect("pu totals order", (string)rp[0] == "2026-09-01", (string)rp[1] == "2026-09-30", (string)rp[2] == "9909001",
                (string)rp[4] == "01%", (string)rp[5] == "S900001");
        }

        static void ArrivalSql()
        {
            Dictionary<string, object> body = With("date_to", "2026-09-30");
            body["vendor"] = new object[] { "S900001", "S900002" };
            body["inv"] = new object[] { "9909001" };
            PuArgs a = ReportsPuReq.ParseArrival(body);
            List<object> ps = new List<object>();
            string sql = ReportsPuArrivalSql.GroupSql(a, "2026-10-15", ps);
            Expect("otif group marks", Marks(sql) == ps.Count, ps.Count == 6, sql.Contains("{PERM}"), sql.Contains("vendor_code"));
            Expect("otif param order", (string)ps[0] == "2026-10-15", (string)ps[1] == "2026-09-01", (string)ps[2] == "2026-09-30",
                (string)ps[3] == "9909001", (string)ps[4] == "S900001");
            Expect("otif buckets", sql.Contains("f.fa IS NULL AND d.dArriveDate<o.asof THEN 1 ELSE 0 END overdue"),
                sql.Contains("f.fa IS NULL AND d.dArriveDate>=o.asof THEN 1 ELSE 0 END not_due"), !sql.Contains("x.arr<d.iQuantity"));
            a.Group = "material";
            Expect("otif material", ReportsPuArrivalSql.GroupSql(a, "2026-10-15", new List<object>()).Contains("inv_code"));
        }

        static void ArrivalOtherSql()
        {
            PuArgs a = ReportsPuReq.ParseArrival(With("overdue_limit", 50));
            List<object> op = new List<object>();
            string overdue = ReportsPuArrivalSql.OverdueSql(a, "2026-10-15", op);
            Expect("otif overdue marks", Marks(overdue) == op.Count, (int)op[op.Count - 1] == 50, overdue.Contains("l.overdue=1"));
            List<object> tp = new List<object>();
            string totals = ReportsPuArrivalSql.TotalsSql(a, "2026-10-15", tp);
            Expect("otif totals marks", Marks(totals) == tp.Count, tp.Count == 2, !totals.Contains("{FILTER}"));
        }

        static void PurchaseRows()
        {
            Dictionary<string, object> row = Row("inv_code", "9909001", "inv_name", "原料甲", "unit", "千克", "vendor_code", "S900001",
                "vendor_name", "供应商甲", "mon", "2026-09", "qty", "12.3456789", "amount", "100.005", "amount_tax", "113.1", "docs", "3");
            PuArgs a = new PuArgs();
            a.GroupBy = new string[] { "material", "vendor", "month" };
            Dictionary<string, object> item = ReportsPuPurchase.Item(row, a);
            Expect("item keys", (string)item["inv_code"] == "9909001", (string)item["unit"] == "千克",
                (string)item["vendor_name"] == "供应商甲", (string)item["month"] == "2026-09");
            Expect("item numbers", (decimal)item["qty"] == 12.345679m, (decimal)item["amount"] == 100.01m,
                (decimal)item["amount_tax"] == 113.1m, (int)item["docs"] == 3);
            a.GroupBy = new string[] { "vendor" };
            item = ReportsPuPurchase.Item(row, a);
            Expect("item no material", item["qty"] == null, !item.ContainsKey("inv_code"), !item.ContainsKey("month"));
            a.Receipt = true;
            item = ReportsPuPurchase.Item(row, a);
            Expect("item receipt", item["amount_tax"] == null, (decimal)item["amount"] == 100.01m);
            Dictionary<string, object> totals = ReportsPuPurchase.Totals(row, true);
            Expect("totals", (decimal)totals["amount"] == 100.01m, totals["amount_tax"] == null, (int)totals["docs"] == 3);
            Expect("totals empty", (decimal)ReportsPuPurchase.Totals(null, false)["amount"] == 0m);
        }

        static void ArrivalRows()
        {
            Dictionary<string, object> row = Row("vendor_code", "S900001", "vendor_name", "供应商甲", "inv_code", "9909001",
                "lines", "10", "arrived_lines", "9", "on_time", "6", "late", "3", "open_overdue", "1", "not_due", "0");
            Dictionary<string, object> vendor = ReportsPuArrival.Group(row, true);
            Expect("group vendor", (string)vendor["vendor_code"] == "S900001", !vendor.ContainsKey("inv_code"),
                (int)vendor["lines"] == 10, (int)vendor["late"] == 3, (int)vendor["not_due"] == 0);
            Dictionary<string, object> material = ReportsPuArrival.Group(row, false);
            Expect("group material", (string)material["inv_code"] == "9909001", !material.ContainsKey("vendor_code"),
                material["unit"] == null, (int)material["open_overdue"] == 1);
            Dictionary<string, object> empty = ReportsPuArrival.Counted(null);
            Expect("counted empty", (int)empty["lines"] == 0, (int)empty["on_time"] == 0, empty.Count == 6);
        }

        static void OverdueRows()
        {
            Dictionary<string, object> over = ReportsPuArrival.OverdueItem(Row("po_code", "PO0001", "po_date", "2026-09-01",
                "vendor_name", "供应商甲", "inv_code", "9909001", "qty", "300000.0000000000", "arrived_qty", "154154",
                "planned_date", "2026-09-27", "days_overdue", "18"));
            Expect("overdue item", (string)over["po_code"] == "PO0001", (decimal)over["qty"] == 300000m,
                (decimal)over["arrived_qty"] == 154154m, (int)over["days_overdue"] == 18,
                (string)over["planned_date"] == "2026-09-27", over["unit"] == null);
        }

        static int Marks(string sql)
        {
            int count = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                if (sql[i] == '?')
                {
                    count++;
                }
            }
            return count;
        }

        static Dictionary<string, object> Body(string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            return body;
        }

        // 带必填的 date_from 再加一个字段。
        static Dictionary<string, object> With(string key, object value)
        {
            Dictionary<string, object> body = Body("date_from", "2026-09-01");
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> Row(params string[] pairs)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                row[pairs[i]] = pairs[i + 1];
            }
            return row;
        }

        static void Refused(string name, Action run, string field)
        {
            try
            {
                run();
            }
            catch (BridgeException ex)
            {
                Expect("refuse " + name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("pu selftest refuse " + name);
        }

        static void Expect(string name, params bool[] checks)
        {
            if (!Array.TrueForAll(checks, delegate(bool ok) { return ok; }))
            {
                throw new InvalidOperationException("pu selftest " + name);
            }
        }
    }
}
