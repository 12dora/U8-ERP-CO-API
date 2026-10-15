using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的库龄分析 stock_age 与收发存汇总的金额扩展：参数校验、行计算（成本兜底、合并、库龄、呆滞、排序、分段）、
    // SQL 占位符个数、整月判断。不连库。
    internal static class StockAgeSelfTest
    {
        static readonly DateTime AsOf = new DateTime(2026, 10, 15);

        public static void Run()
        {
            CheckParse();
            CheckRefused();
            CheckWiring();
            CheckRows();
            CheckMerge();
            CheckRate();
            CheckOrder();
            CheckMasked();
            CheckBuckets();
            CheckSql();
            CheckMonths();
            CheckOpening();
            CheckSummaryFlag();
        }

        static void CheckParse()
        {
            StockAgeArgs a = ReportsStockAgeReq.Parse(Body("{}"));
            Expect("defaults", All(a.IdleDays == 180, a.Buckets.Length == 3, a.Buckets[0] == 30, a.Buckets[2] == 180,
                a.Wh.Length == 0, a.Inv.Length == 0, a.InvClass.Length == 0, a.ByWh, a.Sort == "amount", a.Limit == 200));
            a = ReportsStockAgeReq.Parse(Body("{\"idle_days\":365,\"buckets\":[7,14],\"wh\":[\"01\",\" 02 \"],"
                + "\"inv\":[\"9909001\"],\"inv_class\":\"0101\",\"by_wh\":false,\"sort\":\"age\",\"limit\":2000}"));
            Expect("full", All(a.IdleDays == 365, a.Buckets.Length == 2, a.Wh[1] == "02", a.Inv[0] == "9909001",
                a.InvClass == "0101", !a.ByWh, a.Sort == "age", a.Limit == 2000));
        }

        static void CheckRefused()
        {
            Refused("idle 0", "{\"idle_days\":0}");
            Refused("idle big", "{\"idle_days\":3651}");
            Refused("idle text", "{\"idle_days\":\"180\"}");
            Refused("buckets empty", "{\"buckets\":[]}");
            Refused("buckets 7", "{\"buckets\":[1,2,3,4,5,6,7]}");
            Refused("buckets order", "{\"buckets\":[30,30]}");
            Refused("buckets desc", "{\"buckets\":[90,30]}");
            Refused("buckets zero", "{\"buckets\":[0,30]}");
            Refused("buckets big", "{\"buckets\":[3651]}");
            Refused("buckets text", "{\"buckets\":[\"30\"]}");
            Refused("buckets scalar", "{\"buckets\":30}");
            Refused("wh empty", "{\"wh\":[]}");
            Refused("wh 21", "{\"wh\":[\"1\",\"2\",\"3\",\"4\",\"5\",\"6\",\"7\",\"8\",\"9\",\"10\",\"11\",\"12\",\"13\",\"14\","
                + "\"15\",\"16\",\"17\",\"18\",\"19\",\"20\",\"21\"]}");
            Refused("wh blank", "{\"wh\":[\" \"]}");
            Refused("wh scalar", "{\"wh\":\"01\"}");
            Refused("inv long", "{\"inv\":[\"" + new string('1', 61) + "\"]}");
            Refused("inv class wildcard", "{\"inv_class\":\"01%\"}");
            Refused("by_wh text", "{\"by_wh\":\"true\"}");
            Refused("sort", "{\"sort\":\"qty\"}");
            Refused("limit 0", "{\"limit\":0}");
            Refused("limit 2001", "{\"limit\":2001}");
        }

        static void CheckWiring()
        {
            Expect("owns", ReportsStockAgeReq.Owns("stock_age") && !ReportsStockAgeReq.Owns("stock_summary"));
            Expect("sub", ReportsStockAgeReq.SubOf("stock_age", Body("{}")) == "ST" && ReportsStockAgeReq.SubOf("bom", Body("{}")) == null);
            Expect("names", ReportsStockAgeReq.Names.Length == 1 && ReportsStockAgeReq.Names[0] == "stock_age");
            Expect("spec", ReportsStockAgeReq.Specs.Length == 1 && ReportsStockAgeReq.Specs[0][0] == "/u8co/v1/reports/stock_age"
                && Array.IndexOf(ReportsStockAgeReq.Specs[0], "idle_days") > 0);
            ReportsStockAgeReq.Check("bom", Body("{\"idle_days\":0}"));
            Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map =
                new Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>();
            ReportsStockAgeReq.AddRunners(map);
            Expect("runner", map.ContainsKey("stock_age") && map.Count == 1);
        }

        static void CheckRows()
        {
            AgeLine own = ReportsStockAgeCalc.FromRow(Row(10m, new string[] { "5", "100", null, null }, "2026-07-01", "2026-09-01"));
            Expect("own price", own.Amount == 200m && own.CostQty == 10m);
            AgeLine back = ReportsStockAgeCalc.FromRow(Row(10m, new string[] { "0", "0", "4", "40" }, null, null));
            Expect("fallback price", back.Amount == 100m && back.LastIn == null && back.LastOut == null);
            AgeLine none = ReportsStockAgeCalc.FromRow(Row(10m, new string[4], null, null));
            Expect("no price", none.Amount == null && none.CostQty == 0m && none.Qty == 10m);
            Expect("dates", own.LastIn.Value == new DateTime(2026, 7, 1) && own.LastOut.Value == new DateTime(2026, 9, 1));
            Expect("fields", own.Inv == "9909001" && own.Wh == "01" && own.Unit == "千克" && own.InvStd == null);
        }

        static void CheckMerge()
        {
            List<AgeLine> lines = new List<AgeLine>();
            lines.Add(Line("A", "01", 10m, 100m, "2026-01-01", "2026-03-01"));
            lines.Add(Line("A", "02", 5m, null, "2026-02-01", null));
            lines.Add(Line("B", "01", 1m, null, null, null));
            List<AgeLine> merged = ReportsStockAgeCalc.MergeByInv(lines);
            Expect("merge count", merged.Count == 2 && merged[0].Inv == "A" && merged[1].Inv == "B");
            Expect("merge sums", All(merged[0].Qty == 15m, merged[0].Amount == 100m, merged[0].CostQty == 10m,
                merged[0].Wh == null, merged[0].LastIn.Value == new DateTime(2026, 2, 1),
                merged[0].LastOut.Value == new DateTime(2026, 3, 1)));
            Expect("merge uncosted", merged[1].Amount == null && merged[1].LastIn == null);
        }

        static void CheckRate()
        {
            AgeLine fresh = Line("A", "01", 1m, null, "2026-10-10", null);
            ReportsStockAgeCalc.Rate(fresh, AsOf, 180);
            Expect("fresh age", fresh.Age == 5 && !fresh.Idle);
            AgeLine stale = Line("A", "01", 1m, null, "2026-01-01", null);
            ReportsStockAgeCalc.Rate(stale, AsOf, 180);
            Expect("never out idle", stale.Age == 287 && stale.Idle);
            AgeLine moving = Line("A", "01", 1m, null, "2025-01-01", "2026-09-20");
            ReportsStockAgeCalc.Rate(moving, AsOf, 180);
            Expect("moving", moving.Age == 652 && !moving.Idle);
            AgeLine edge = Line("A", "01", 1m, null, "2025-01-01", "2026-04-18");
            ReportsStockAgeCalc.Rate(edge, AsOf, 180);
            Expect("idle boundary", edge.Idle);
            AgeLine unknown = Line("A", "01", 1m, null, null, null);
            ReportsStockAgeCalc.Rate(unknown, AsOf, 180);
            Expect("never in", unknown.Age == null && unknown.Idle);
        }

        static void CheckOrder()
        {
            List<AgeLine> lines = new List<AgeLine>();
            lines.Add(Line("C", "01", 1m, 50m, "2026-10-01", null));
            lines.Add(Line("B", "01", 1m, null, null, null));
            lines.Add(Line("A", "02", 1m, 50m, "2026-01-01", null));
            lines.Add(Line("A", "01", 1m, 80m, "2026-09-01", null));
            lines.Add(Line("D", "01", 1m, null, "2025-01-01", null));
            for (int i = 0; i < lines.Count; i++)
            {
                ReportsStockAgeCalc.Rate(lines[i], AsOf, 180);
            }
            ReportsStockAgeCalc.Order(lines, "amount");
            Expect("by amount", Names(lines) == "A01,A02,C01,B01,D01");
            ReportsStockAgeCalc.Order(lines, "age");
            Expect("by age", Names(lines) == "B01,D01,A02,A01,C01");
        }

        // 无金额权限：清掉金额后按库龄排，同龄的按存货编码，不泄露金额高低。
        static void CheckMasked()
        {
            List<AgeLine> lines = new List<AgeLine>();
            lines.Add(Line("B", "01", 1m, 500m, "2026-10-01", null));
            lines.Add(Line("A", "01", 1m, 10m, "2026-10-01", null));
            lines.Add(Line("C", "01", 1m, 900m, "2026-01-01", null));
            for (int i = 0; i < lines.Count; i++)
            {
                ReportsStockAgeCalc.Rate(lines[i], AsOf, 180);
            }
            ReportsStockAgeCalc.DropAmounts(lines);
            ReportsStockAgeCalc.Order(lines, "age");
            Expect("masked order", Names(lines) == "C01,A01,B01");
            AgeReport report = ReportsStockAgeCalc.Summarize(lines, new int[] { 30 });
            Expect("masked uncosted", report.Total.Costed == 0 && lines[0].Amount == null && lines[0].CostQty == 0m);
        }

        static void CheckBuckets()
        {
            int[] bounds = new int[] { 30, 90, 180 };
            Expect("bucket edges", All(ReportsStockAgeCalc.BucketOf(bounds, 0) == 0, ReportsStockAgeCalc.BucketOf(bounds, 30) == 0,
                ReportsStockAgeCalc.BucketOf(bounds, 31) == 1, ReportsStockAgeCalc.BucketOf(bounds, 90) == 1,
                ReportsStockAgeCalc.BucketOf(bounds, 180) == 2, ReportsStockAgeCalc.BucketOf(bounds, 181) == 3,
                ReportsStockAgeCalc.BucketOf(bounds, null) == 3));
            List<AgeLine> lines = new List<AgeLine>();
            lines.Add(Line("A", "01", 1m, 10m, "2026-10-10", "2026-10-11"));
            lines.Add(Line("B", "01", 1m, 20m, "2026-01-01", null));
            lines.Add(Line("C", "01", 1m, null, null, null));
            for (int i = 0; i < lines.Count; i++)
            {
                ReportsStockAgeCalc.Rate(lines[i], AsOf, 180);
            }
            AgeReport report = ReportsStockAgeCalc.Summarize(lines, bounds);
            Expect("bucket count", report.Buckets.Length == 4);
            Expect("bucket sums", All(report.Buckets[0].Items == 1, report.Buckets[0].Amount == 10m, report.Buckets[1].Items == 0,
                report.Buckets[2].Items == 0, report.Buckets[3].Items == 2, report.Buckets[3].Amount == 20m,
                report.Buckets[3].Costed == 1));
            Expect("totals", All(report.Total.Items == 3, report.Total.Amount == 30m, report.Total.Costed == 2,
                report.Idle.Items == 2, report.Idle.Amount == 20m));
        }

        static void CheckSql()
        {
            StockAgeArgs s = ReportsStockAgeReq.Parse(Body("{}"));
            List<object> ps = new List<object>();
            string sql = ReportsStockAgeSql.Build(null, s, "2026-10-15", 2026, 9, ps);
            Expect("sql plain", All(Count(sql, '?') == ps.Count, ps.Count == 5, !sql.Contains("{"), sql.Contains("FROM CurrentStock c"),
                sql.Contains("IA_Summary a"), (int)ps[1] == 2026, (int)ps[2] == 9));
            s = ReportsStockAgeReq.Parse(Body("{\"wh\":[\"01\",\"02\"],\"inv\":[\"X1\"],\"inv_class\":\"0101\"}"));
            ps = new List<object>();
            sql = ReportsStockAgeSql.Build(null, s, "2026-10-15", 0, 0, ps);
            Expect("sql filtered", All(Count(sql, '?') == ps.Count, ps.Count == 5 + 2 * 3 + 1, sql.Contains("c.cWhCode IN (?, ?)"),
                sql.Contains("m.cInvCode IN (?)"), sql.Contains("i.cInvCCode LIKE ?"), (string)ps[ps.Count - 1] == "0101%",
                (string)ps[0] == "01" && (string)ps[3] == "2026-10-15" && (string)ps[4] == "01"));
            Expect("sql values not inlined", !sql.Contains("X1") && !sql.Contains("0101%"));
            Expect("cost month sql", Count(ReportsStockAgeSql.CostMonth, '?') == 1);
        }

        static void CheckMonths()
        {
            int[] m = ReportsStockAmount.WholeMonths("2026-09-01", "2026-09-30");
            Expect("one month", m != null && m[0] == 202608 && m[1] == 202609 && m[2] == 202609);
            m = ReportsStockAmount.WholeMonths("2026-01-01", "2026-09-30");
            Expect("year start", m != null && m[0] == 202600 && m[1] == 202601 && m[2] == 202609);
            m = ReportsStockAmount.WholeMonths("2025-11-01", "2026-02-28");
            Expect("across years", m != null && m[0] == 202510 && m[2] == 202602);
            m = ReportsStockAmount.WholeMonths("2028-02-01", "2028-02-29");
            Expect("leap", m != null && m[2] == 202802);
            Expect("mid month from", ReportsStockAmount.WholeMonths("2026-09-02", "2026-09-30") == null);
            Expect("mid month to", ReportsStockAmount.WholeMonths("2026-09-01", "2026-09-29") == null);
            Expect("bad date", ReportsStockAmount.WholeMonths("2026-09-01", "") == null);
            Expect("next month", All(ReportsStockAmount.NextMonth(202609) == 202610, ReportsStockAmount.NextMonth(202612) == 202701));
        }

        static void CheckOpening()
        {
            Dictionary<int, int[]> states = new Dictionary<int, int[]>();
            states[202608] = new int[] { 1, 0 };
            int[] ym = new int[] { 202608, 202609, 202609 };
            Expect("opening prior month", ReportsStockAmount.OpeningMonth(states, ym) && ym[0] == 202608);
            states = new Dictionary<int, int[]>();
            states[202400] = new int[] { 1, 0 };
            states[202412] = new int[] { 1, 0 };
            ym = new int[] { 202411, 202412, 202412 };
            Expect("opening falls back to year start", ReportsStockAmount.OpeningMonth(states, ym) && ym[0] == 202400);
            states[202405] = new int[] { 1, 0 };
            ym = new int[] { 202411, 202412, 202412 };
            Expect("opening gap refused", !ReportsStockAmount.OpeningMonth(states, ym) && ym[0] == 202411);
            ym = new int[] { 202400, 202401, 202401 };
            Expect("january without year start", !ReportsStockAmount.OpeningMonth(new Dictionary<int, int[]>(), ym));
            ym = new int[] { 202610, 202611, 202611 };
            Expect("no year start", !ReportsStockAmount.OpeningMonth(new Dictionary<int, int[]>(), ym));
        }

        static void CheckSummaryFlag()
        {
            ReportArgs a = new ReportArgs();
            a.Name = "stock_summary";
            Expect("summary default", !ReportsStockReq.Parse(a, Body("{\"date_from\":\"2026-09-01\"}")).IncludeAmount);
            Expect("summary flag", ReportsStockReq.Parse(a, Body("{\"date_from\":\"2026-09-01\",\"include_amount\":true}")).IncludeAmount);
            try
            {
                ReportsStockReq.Parse(a, Body("{\"date_from\":\"2026-09-01\",\"include_amount\":1}"));
            }
            catch (BridgeException ex)
            {
                Expect("summary flag type", ex.Status == 400);
                return;
            }
            throw new InvalidOperationException("summary flag type");
        }

        static string Names(List<AgeLine> lines)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < lines.Count; i++)
            {
                sb.Append(i == 0 ? "" : ",").Append(lines[i].Inv).Append(lines[i].Wh);
            }
            return sb.ToString();
        }

        // 一行查询结果：qty、cost = { ia_n, ia_m, iv_n, iv_m }、最后入库、最后出库；null 表示该列为空（行里缺席）。
        static Dictionary<string, object> Row(decimal qty, string[] cost, string li, string lo)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["inv"] = "9909001";
            row["inv_name"] = "产品A";
            row["unit"] = "千克";
            row["wh"] = "01";
            row["wh_name"] = "仓库甲";
            row["qty"] = qty.ToString(CultureInfo.InvariantCulture);
            Put(row, "ia_n", cost[0]);
            Put(row, "ia_m", cost[1]);
            Put(row, "iv_n", cost[2]);
            Put(row, "iv_m", cost[3]);
            Put(row, "li", li);
            Put(row, "lo", lo);
            return row;
        }

        static void Put(Dictionary<string, object> row, string name, string value)
        {
            if (value != null)
            {
                row[name] = value;
            }
        }

        static AgeLine Line(string inv, string wh, decimal qty, decimal? amount, string li, string lo)
        {
            AgeLine line = new AgeLine();
            line.Inv = inv;
            line.Wh = wh;
            line.Qty = qty;
            line.Amount = amount;
            line.CostQty = amount.HasValue ? qty : 0m;
            line.LastIn = li == null ? (DateTime?)null : DateTime.ParseExact(li, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            line.LastOut = lo == null ? (DateTime?)null : DateTime.ParseExact(lo, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            return line;
        }

        static void Refused(string name, string json)
        {
            try
            {
                ReportsStockAgeReq.Check("stock_age", Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
        }

        static int Count(string text, char c)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == c)
                {
                    n++;
                }
            }
            return n;
        }

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
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
