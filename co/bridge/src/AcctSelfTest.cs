using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的账务核对报表族 Acct 部分（不连库）：路由与登录子系统、参数校验与缺省、SQL 的 ? 与参数一一对应、
    // 条件来自共用的常量、期间与日期的算法、结账检查项、存货核算两张清单的取值、单位成本的折叠与期间范围。
    internal static class AcctSelfTest
    {
        public static void Run()
        {
            CheckRoutes();
            CheckParse();
            CheckRefused();
            CheckSql();
            CheckDates();
            CheckCloseItem();
            CheckIaItems();
            CheckIaOrder();
            CheckCostRange();
            CheckCostFold();
        }

        static void CheckRoutes()
        {
            Expect("acct names", All(ReportsAcctReq.Names.Length == 3, ReportsAcctReq.Owns("close_check"),
                ReportsAcctReq.Owns("ia_anomalies"), ReportsAcctReq.Owns("cost_unit"), !ReportsAcctReq.Owns("close_status")));
            Expect("acct specs", All(ReportsAcctReq.Specs.Length == 3, ReportsAcctReq.Specs[0][0] == "/u8co/v1/reports/close_check",
                ReportsAcctReq.Specs[1][0] == "/u8co/v1/reports/ia_anomalies", ReportsAcctReq.Specs[2][0] == "/u8co/v1/reports/cost_unit",
                Array.IndexOf(ReportsAcctReq.Specs[0], "period") > 0, Array.IndexOf(ReportsAcctReq.Specs[1], "limit") > 0,
                Array.IndexOf(ReportsAcctReq.Specs[2], "inv_class") > 0, Array.IndexOf(ReportsAcctReq.Specs[2], "after") < 0));
            Dictionary<string, object> none = new Dictionary<string, object>();
            Expect("acct sub", All(ReportsAcctReq.SubOf("close_check", none) == "GL", ReportsAcctReq.SubOf("ia_anomalies", none) == "IA",
                ReportsAcctReq.SubOf("cost_unit", none) == "GL", ReportsAcctReq.SubOf("close_status", none) == null));
            Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map =
                new Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>();
            ReportsAcctReq.AddRunners(map);
            Expect("acct runners", All(map.Count == 3, map.ContainsKey("close_check"), map.ContainsKey("ia_anomalies"),
                map.ContainsKey("cost_unit")));
        }

        static void CheckParse()
        {
            Dictionary<string, object> none = new Dictionary<string, object>();
            Expect("close default", ReportsAcctParse.Close(none).Period == 0);
            Expect("close period", ReportsAcctParse.Close(One("period", 12)).Period == 12);
            AcctIaArgs ia = ReportsAcctParse.Ia(none);
            Expect("ia default", ia.Period == 0 && ia.Limit == 50);
            ia = ReportsAcctParse.Ia(With(One("period", 1), "limit", 200));
            Expect("ia values", ia.Period == 1 && ia.Limit == 200);
            AcctCostArgs cost = ReportsAcctParse.Cost(none);
            Expect("cost default", All(cost.From == 0, cost.To == 0, cost.Inv.Length == 0, cost.InvClass == ""));
            Dictionary<string, object> body = With(With(One("period_from", 3), "period_to", 3), "inv", new object[] { " 9909001 ", "9909002" });
            body["inv_class"] = "01.02";
            cost = ReportsAcctParse.Cost(body);
            Expect("cost values", All(cost.From == 3, cost.To == 3, cost.Inv.Length == 2, cost.Inv[0] == "9909001",
                cost.InvClass == "01.02"));
            Expect("cost only to", ReportsAcctParse.Cost(One("period_to", 9)).To == 9);
        }

        static void CheckRefused()
        {
            Refused("close period 0", One("period", 0), "period", "close_check");
            Refused("close period 13", One("period", 13), "period", "close_check");
            Refused("close period text", One("period", "9"), "period", "close_check");
            Refused("ia period 13", One("period", 13), "period", "ia_anomalies");
            Refused("ia limit 0", One("limit", 0), "limit", "ia_anomalies");
            Refused("ia limit 201", One("limit", 201), "limit", "ia_anomalies");
            Refused("ia limit text", One("limit", "5"), "limit", "ia_anomalies");
            Refused("cost from 0", One("period_from", 0), "period_from", "cost_unit");
            Refused("cost to 13", One("period_to", 13), "period_to", "cost_unit");
            Refused("cost from > to", With(One("period_from", 6), "period_to", 5), "period_from", "cost_unit");
            Refused("cost inv empty", One("inv", new object[0]), "inv", "cost_unit");
            Refused("cost inv 21", One("inv", Many(21)), "inv", "cost_unit");
            Refused("cost inv text", One("inv", "9909001"), "inv", "cost_unit");
            Refused("cost inv blank", One("inv", new object[] { "9909001", "  " }), "inv.1", "cost_unit");
            Refused("cost inv long", One("inv", new object[] { new string('I', 61) }), "inv.0", "cost_unit");
            Refused("cost inv number", One("inv", new object[] { 1 }), "inv.0", "cost_unit");
            Refused("cost class space", One("inv_class", "01 02"), "inv_class", "cost_unit");
            Refused("cost class number", One("inv_class", 1), "inv_class", "cost_unit");
            bool threw = false;
            try
            {
                ReportsAcctReq.Check("close_status", new Dictionary<string, object>());
            }
            catch (BridgeException ex)
            {
                threw = ex.Status == 400;
            }
            Expect("acct unknown name", threw);
        }

        // 每条 SQL 的 ? 个数等于参数个数；共用的条件原样来自共用常量。
        static void CheckSql()
        {
            string[] items = new string[ReportsAcctCheckSql.All.Length];
            for (int i = 0; i < items.Length; i++)
            {
                AcctCheck check = ReportsAcctCheckSql.All[i];
                items[i] = check.Item;
                Expect("check marks " + check.Item, Marks(check.Sql) == check.Args.Length
                    && ReportsAcctCheckSql.ArgsOf(check, 2026, 9).Length == check.Args.Length);
                Expect("check module " + check.Item, Array.IndexOf(ReportsAcctCheckSql.Modules, check.Module) >= 0);
                Expect("check samples " + check.Item, check.Sql.StartsWith("SELECT TOP (5) ") && check.Sql.IndexOf("COUNT(*) OVER() n") > 0);
            }
            Expect("check items", string.Join(",", items) == "unaudited_dispatch,unaudited_sale_invoice,unaudited_purchase_invoice,"
                + "unverified_purchase_in,unverified_product_in,unverified_material_out,unverified_sale_out,unverified_other_in,"
                + "unverified_other_out,unposted_vouchers,unbooked_ia,unvouchered_ar,unvouchered_ap,pl_not_transferred");
            string[] tables = new string[] { "RdRecord01", "rdrecord10", "rdrecord11", "rdrecord32", "RdRecord08", "RdRecord09" };
            for (int i = 0; i < tables.Length; i++)
            {
                Expect("stock from " + tables[i], ReportsAcctCheckSql.StockFrom(tables[i]).StartsWith(tables[i] + " WHERE ")
                    && Marks(ReportsAcctCheckSql.StockFrom(tables[i])) == 2);
            }
            Expect("close shared", ReportsAcctCheckSql.All[0].Sql.IndexOf(PeriodCloseSql.DispatchFrom) > 0
                && ReportsAcctCheckSql.All[2].Sql.IndexOf(PeriodCloseSql.PuInvoiceFrom) > 0
                && ReportsAcctCheckSql.All[9].Sql.IndexOf(PeriodCloseSql.UnpostedWhere) > 0
                && ReportsAcctCheckSql.All[13].Sql.IndexOf(PeriodCloseSql.ProfitFrom) > 0);
            Expect("closed sql", Marks(ReportsAcctCheckSql.ClosedSql) == 2);
            CheckIaSql();
            CheckCostSql();
        }

        static void CheckIaSql()
        {
            Expect("ia union", Marks(ReportsAcctIaSql.UnbookedUnion) == 12 && ReportsAcctIaSql.Types.Length == 6);
            Expect("ia union types", All(ReportsAcctIaSql.UnbookedUnion.IndexOf("N'sale_out' typ") > 0,
                ReportsAcctIaSql.UnbookedUnion.IndexOf("s.cVouType LIKE N'32%'") > 0,
                ReportsAcctIaSql.UnbookedUnion.IndexOf("FROM rdrecord11 h JOIN rdrecords11 r") > 0));
            Expect("ia unbilled", Marks(ReportsAcctIaSql.Unbilled) == 3 && Marks(ReportsAcctIaSql.Unbooked) == 13);
            Expect("ia unbilled red", ReportsAcctIaSql.Unbilled.IndexOf("iSumBillQuantity,0)<>0) x") > 0
                && ReportsAcctIaSql.Unbilled.IndexOf(",0)>0) x") < 0);
            Expect("ia unbooked cost wh", ReportsAcctIaSql.UnbookedUnion.IndexOf("LEFT JOIN Warehouse w0 ON w0.cWhCode=h.cWhCode") > 0
                && ReportsAcctIaSql.UnbookedUnion.IndexOf("ISNULL(w0.bInCost,1)=1") > 0);
            List<object> ps = new List<object>();
            ReportsAcctIaSql.AddUnbookedDates(ps, "2026-09-01", "2026-10-01");
            Expect("ia dates", ps.Count == 12 && (string)ps[0] == "2026-09-01" && (string)ps[11] == "2026-10-01");
        }

        static void CheckCostSql()
        {
            Expect("cost sql", All(Marks(ReportsAcctCostSql.Unit) == 2, Marks(ReportsAcctCostSql.ClassFilter) == 1,
                Marks(ReportsAcctCostSql.FinishedQty) == 2, Marks(ReportsAcctCostSql.ClosedPeriods) == 1,
                Marks(ReportsAcctCostSql.ElementNames) == 0));
        }

        static void CheckDates()
        {
            Expect("first day", ReportsAcctParse.FirstDay(2026, 2) == "2026-02-01");
            Expect("next day", All(ReportsAcctParse.NextDay(2026, 2) == "2026-03-01", ReportsAcctParse.NextDay(2026, 12) == "2027-01-01"));
            Expect("last day", All(ReportsAcctParse.LastDay(2026, 2) == "2026-02-28", ReportsAcctParse.LastDay(2028, 2) == "2028-02-29",
                ReportsAcctParse.LastDay(2026, 12) == "2026-12-31"));
            Expect("month of", ReportsAcctParse.MonthOf("2026-09-30") == 9);
            bool threw = false;
            try
            {
                ReportsAcctParse.MonthOf("2026/09/30");
            }
            catch (BridgeException ex)
            {
                threw = ex.Status == 400;
            }
            Expect("month of bad", threw);
            Expect("period text", All(ReportsAcctCost.PeriodText(202609) == "2026-09", ReportsAcctCost.PeriodText(202601) == "2026-01"));
            object[] args = ReportsAcctCheckSql.ArgsOf(ReportsAcctCheckSql.All[0], 2026, 12);
            Expect("check args", args.Length == 2 && (string)args[0] == "2026-12-01" && (string)args[1] == "2027-01-01");
            args = ReportsAcctCheckSql.ArgsOf(ReportsAcctCheckSql.All[11], 2026, 9);
            Expect("check args py", args.Length == 2 && (int)args[0] == 9 && (int)args[1] == 2026);
        }

        static void CheckCloseItem()
        {
            AcctCheck check = ReportsAcctCheckSql.All[0];
            Dictionary<string, object> item = ReportsAcctClose.Item(check, new List<Dictionary<string, object>>());
            Expect("close empty", All((int)item["count"] == 0, ((List<object>)item["samples"]).Count == 0,
                (string)item["module"] == "SA", (string)item["item"] == "unaudited_dispatch"));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 1; i <= 5; i++)
            {
                Dictionary<string, object> row = Row("n", "12");
                row["code"] = "DN20261000" + i.ToString();
                rows.Add(row);
            }
            item = ReportsAcctClose.Item(check, rows);
            List<object> samples = (List<object>)item["samples"];
            Expect("close samples", All((int)item["count"] == 12, samples.Count == 5, (string)samples[0] == "DN202610001",
                (string)samples[4] == "DN202610005"));
        }

        static void CheckIaItems()
        {
            Dictionary<string, object> row = Row("code", "PN202609001");
            row["d"] = "2026-09-03";
            row["vname"] = "供应商甲";
            row["inv_code"] = "9909001";
            row["iname"] = "原料甲";
            row["unit"] = "千克";
            row["qty"] = "10.0000000000";
            row["billed"] = "4.5000000000";
            row["est"] = "12.345000";
            row["days"] = "27";
            Dictionary<string, object> item = ReportsAcctIa.UnbilledItem(row);
            Expect("ia unbilled item", All((string)item["code"] == "PN202609001", (string)item["date"] == "2026-09-03",
                (string)item["vendor_name"] == "供应商甲", (decimal)item["qty"] == 10m, (decimal)item["billed_qty"] == 4.5m,
                (decimal)item["residual_qty"] == 5.5m, (decimal)item["est_amount"] == 12.35m, (int)item["days"] == 27));
            row = Row("typ", "material_out");
            row["code"] = "LL20260901";
            row["d"] = "2026-09-04";
            row["inv_code"] = "9909002";
            row["qty"] = "-3.5";
            item = ReportsAcctIa.UnbookedItem(row);
            Expect("ia unbooked item", All((string)item["type"] == "material_out", (string)item["code"] == "LL20260901",
                item["wh_name"] == null, item["inv_name"] == null, (decimal)item["qty"] == -3.5m));
        }

        static void CheckCostRange()
        {
            List<int> closed = new List<int>(new int[] { 1, 2, 3, 8, 9 });
            Expect("range default", Same(ReportsAcctCost.Range(CostArgs(0, 0), closed, 10), 9, 9));
            Expect("range limit", Same(ReportsAcctCost.Range(CostArgs(0, 0), closed, 8), 8, 8));
            Expect("range early limit", Same(ReportsAcctCost.Range(CostArgs(0, 0), closed, 5), 3, 3));
            Expect("range explicit", Same(ReportsAcctCost.Range(CostArgs(2, 4), closed, 10), 2, 4));
            Expect("range only to", Same(ReportsAcctCost.Range(CostArgs(0, 4), closed, 10), 4, 4));
            Expect("range only from", Same(ReportsAcctCost.Range(CostArgs(8, 0), closed, 10), 8, 9));
            Expect("range from after latest", Same(ReportsAcctCost.Range(CostArgs(10, 0), closed, 12), 10, 10));
            Expect("range none", Same(ReportsAcctCost.Range(CostArgs(0, 0), new List<int>(), 12), 0, 0));
            Expect("range none explicit", Same(ReportsAcctCost.Range(CostArgs(5, 6), new List<int>(), 12), 5, 6));
        }

        static void CheckCostFold()
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(CostRow("202608", "11", "9909001", "0", "3.1"));
            rows.Add(CostRow("202608", "11", "9909001", "3", "0.7"));
            rows.Add(CostRow("202609", "11", "9909001", "0", "2.8628"));
            rows.Add(CostRow("202609", "11", "9909001", "1", "1.25E-1"));
            rows.Add(CostRow("202609", "12", "9909002", "0", "5"));
            Dictionary<string, decimal> qty = new Dictionary<string, decimal>();
            qty["202609:11"] = 1500.5m;
            qty["202608:11"] = 0m;
            Dictionary<string, object> names = new Dictionary<string, object>();
            List<object> items = ReportsAcctCost.Fold(rows, qty, names);
            Expect("cost fold count", items.Count == 3 && names.Count == 3 && names.ContainsKey("0") && names.ContainsKey("3"));
            Dictionary<string, object> a = (Dictionary<string, object>)items[0];
            Dictionary<string, object> b = (Dictionary<string, object>)items[1];
            Dictionary<string, object> c = (Dictionary<string, object>)items[2];
            Dictionary<string, object> aEl = (Dictionary<string, object>)a["elements"];
            Dictionary<string, object> bEl = (Dictionary<string, object>)b["elements"];
            Expect("cost fold first", All((string)a["period"] == "2026-08", (string)a["inv_code"] == "9909001", a["qty"] == null,
                aEl.Count == 2, (decimal)aEl["0"] == 3.1m, (decimal)aEl["3"] == 0.7m, (decimal)a["unit_total"] == 3.8m));
            Expect("cost fold second", All((string)b["period"] == "2026-09", (decimal)b["qty"] == 1500.5m, bEl.Count == 2,
                (decimal)bEl["1"] == 0.125m, (decimal)b["unit_total"] == 2.9878m));
            Expect("cost fold other", All((string)c["inv_code"] == "9909002", c["qty"] == null, (decimal)c["unit_total"] == 5m));
            Expect("cost fold empty", ReportsAcctCost.Fold(new List<Dictionary<string, object>>(), qty, names).Count == 0);
        }

        static Dictionary<string, object> CostRow(string per, string part, string inv, string typ, string amt)
        {
            Dictionary<string, object> row = Row("per", per);
            row["part"] = part;
            row["inv_code"] = inv;
            row["inv_name"] = "产品" + inv;
            row["unit"] = "KG";
            row["typ"] = typ;
            row["amt"] = amt;
            return row;
        }

        static AcctCostArgs CostArgs(int from, int to)
        {
            AcctCostArgs q = new AcctCostArgs();
            q.From = from;
            q.To = to;
            return q;
        }

        static bool Same(int[] range, int from, int to)
        {
            return range.Length == 2 && range[0] == from && range[1] == to;
        }

        // SQL 里 ? 的个数。
        static int Marks(string sql)
        {
            int n = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                n += sql[i] == '?' ? 1 : 0;
            }
            return n;
        }

        static object[] Many(int count)
        {
            object[] list = new object[count];
            for (int i = 0; i < count; i++)
            {
                list[i] = "I" + i.ToString();
            }
            return list;
        }

        static Dictionary<string, object> Row(string key, string value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row[key] = value;
            return row;
        }

        static Dictionary<string, object> One(string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> With(Dictionary<string, object> body, string key, object value)
        {
            body[key] = value;
            return body;
        }

        // 校验被拒绝：400，field 等于 field。
        static void Refused(string name, Dictionary<string, object> body, string field, string route)
        {
            try
            {
                ReportsAcctReq.Check(route, body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool All(params bool[] checks)
        {
            for (int i = 0; i < checks.Length; i++)
            {
                if (!checks[i])
                {
                    return false;
                }
            }
            return true;
        }

        // 暂估未回冲的排序：默认按估价从大到小；字段权限会遮估价时按日期，ORDER BY 里不能有估价。
        static void CheckIaOrder()
        {
            Expect("ia order amount", ReportsAcctIaSql.UnbilledOrder.Contains("x.est DESC"));
            Expect("ia order date", ReportsAcctIaSql.UnbilledOrderByDate.Contains("x.dt") && !ReportsAcctIaSql.UnbilledOrderByDate.Contains("x.est"));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("acct " + name);
            }
        }
    }
}
