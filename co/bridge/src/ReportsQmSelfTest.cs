using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的质量统计报表部分（reports/qm_indicators、reports/qm_pass_rate）：请求校验、小数解析、不良判断、
    // 统计与分组、合格率的单数与合计。只跑纯函数，不连库。
    internal static class ReportsQmSelfTest
    {
        public static void Run()
        {
            CheckRequest();
            CheckBadRequest();
            CheckNumber();
            CheckStats();
            CheckGroups();
            CheckPassRate();
            CheckSql();
        }

        static void CheckRequest()
        {
            Dictionary<string, object> body = Body("incoming", "2026-04-01");
            QmReportArgs q = ReportsQmReq.Parse("qm_indicators", body);
            Expect("qm req type", q.VoucherType == "QM03", q.Kind == "incoming");
            Expect("qm req defaults", q.Limit == 200, !q.Unverified, q.DateTo == "", q.Inv.Length == 0,
                q.GroupBy == "month");
            body["kind"] = "product";
            body["inv"] = new ArrayList(new object[] { " 9909001 ", "9909001", "9909002" });
            body["inv_class"] = "01.02";
            body["guide"] = new object[] { "001" };
            body["limit"] = 5;
            body["include_unverified"] = true;
            q = ReportsQmReq.Parse("qm_indicators", body);
            Expect("qm req product", q.VoucherType == "QM04", q.Limit == 5, q.Unverified, q.InvClass == "01.02");
            Expect("qm req codes", q.Inv.Length == 2, q.Inv[0] == "9909001", q.Inv[1] == "9909002");
            Expect("qm req guide", q.Guide.Length == 1, q.Guide[0] == "001");
            body = Body("incoming", "2026-04-01");
            body["group_by"] = "vendor";
            body["vendor"] = new ArrayList(new object[] { "S900001" });
            q = ReportsQmReq.Parse("qm_pass_rate", body);
            Expect("qm req vendor", q.GroupBy == "vendor", q.Vendor.Length == 1, q.Guide.Length == 0);
            Expect("qm req owns", ReportsQmReq.Owns("qm_pass_rate"), !ReportsQmReq.Owns("stock_summary"));
            Expect("qm req sub none", ReportsQmReq.SubOf("stock_summary", body) == null);
            Expect("qm req specs", ReportsQmReq.Specs.Length == 2, ReportsQmReq.Names.Length == 2,
                ReportsQmReq.Specs[0][0].EndsWith("/qm_indicators", StringComparison.Ordinal));
        }

        static void CheckBadRequest()
        {
            Bad("qm kind missing", "qm_indicators", Without(Body("incoming", "2026-04-01"), "kind"), "kind");
            Bad("qm kind other", "qm_indicators", Body("other", "2026-04-01"), "kind");
            Bad("qm from missing", "qm_pass_rate", Without(Body("incoming", "2026-04-01"), "date_from"), "date_from");
            Bad("qm from format", "qm_pass_rate", Body("incoming", "2026/04/01"), "date_from");
            Dictionary<string, object> body = Body("incoming", "2026-04-01");
            body["date_to"] = "2026-03-31";
            Bad("qm range", "qm_indicators", body, "date_from");
            body = Body("product", "2026-04-01");
            body["vendor"] = new ArrayList(new object[] { "S900001" });
            Bad("qm vendor product", "qm_indicators", body, "vendor");
            body = Body("product", "2026-04-01");
            body["group_by"] = "vendor";
            Bad("qm group vendor product", "qm_pass_rate", body, "group_by");
            body["group_by"] = "week";
            Bad("qm group enum", "qm_pass_rate", body, "group_by");
            CheckBadValues();
        }

        static void CheckBadValues()
        {
            Dictionary<string, object> body = Body("incoming", "2026-04-01");
            body["limit"] = 0;
            Bad("qm limit low", "qm_indicators", body, "limit");
            body["limit"] = 1001;
            Bad("qm limit high", "qm_pass_rate", body, "limit");
            body["limit"] = "10";
            Bad("qm limit type", "qm_indicators", body, "limit");
            body = Body("incoming", "2026-04-01");
            body["include_unverified"] = "yes";
            Bad("qm bool", "qm_indicators", body, "include_unverified");
            body = Body("incoming", "2026-04-01");
            body["inv_class"] = "01%";
            Bad("qm class chars", "qm_indicators", body, "inv_class");
            body["inv_class"] = "";
            Bad("qm class empty", "qm_indicators", body, "inv_class");
            body = Body("incoming", "2026-04-01");
            body["inv"] = new ArrayList();
            Bad("qm inv empty", "qm_indicators", body, "inv");
            body["inv"] = "9909001";
            Bad("qm inv scalar", "qm_indicators", body, "inv");
            ArrayList many = new ArrayList();
            for (int i = 0; i < 21; i++)
            {
                many.Add("C" + i);
            }
            body["inv"] = many;
            Bad("qm inv many", "qm_indicators", body, "inv");
            body["inv"] = new ArrayList(new object[] { "ok", "a\tb" });
            Bad("qm inv control", "qm_indicators", body, "inv.1");
            body["inv"] = new ArrayList(new object[] { 7 });
            Bad("qm inv type", "qm_indicators", body, "inv.0");
        }

        static void CheckNumber()
        {
            decimal v;
            Expect("qm num plain", ReportsQmStat.TryNumber(" 9.03 ", out v), v == 9.03m);
            Expect("qm num neg", ReportsQmStat.TryNumber("-0.5", out v), v == -0.5m);
            Expect("qm num dot", ReportsQmStat.TryNumber(".000000", out v), v == 0m);
            Expect("qm num text", !ReportsQmStat.TryNumber("A/(3级)", out v));
            Expect("qm num range", !ReportsQmStat.TryNumber("9.5-11.0", out v), !ReportsQmStat.TryNumber("≥95.0", out v));
            Expect("qm num empty", !ReportsQmStat.TryNumber("", out v), !ReportsQmStat.TryNumber("  ", out v),
                !ReportsQmStat.TryNumber(null, out v));
            Expect("qm num group", !ReportsQmStat.TryNumber("1,234", out v), !ReportsQmStat.TryNumber("1e3", out v));
            Expect("qm num huge", !ReportsQmStat.TryNumber("9999999999999999", out v));
            Expect("qm bad", ReportsQmStat.IsBad("不良"), ReportsQmStat.IsBad("不合格"), !ReportsQmStat.IsBad("合格"),
                !ReportsQmStat.IsBad(null), !ReportsQmStat.IsBad(""));
            Expect("qm month", ReportsQmStat.MonthOf("2026-04-30 00:00:00") == "2026-04",
                ReportsQmStat.DayOf("2026-04-30 00:00:00") == "2026-04-30", ReportsQmStat.MonthOf("") == "");
        }

        static void CheckStats()
        {
            List<decimal> values = new List<decimal>(new decimal[] { 2m, 4m, 4m, 4m, 5m, 5m, 7m, 9m });
            Expect("qm mean", ReportsQmStat.Mean(values) == 5m, ReportsQmStat.Mean(new List<decimal>()) == 0m);
            object sd = ReportsQmStat.Stdev(values);
            Expect("qm stdev", sd is decimal, (decimal)sd == 2.13809m);
            Expect("qm stdev one", ReportsQmStat.Stdev(new List<decimal>(new decimal[] { 3m })) == null);
            Expect("qm minmax", (decimal)ReportsQmStat.Min(values) == 2m, (decimal)ReportsQmStat.Max(values) == 9m,
                ReportsQmStat.Min(new List<decimal>()) == null);
            Expect("qm round6", ReportsQmStat.Round6(1m / 3m, 3) is decimal, ReportsQmStat.Round6(1m, 0) == null);
            Dictionary<string, int> counts = new Dictionary<string, int>();
            Expect("qm std none", ReportsQmStat.TopStandard(counts) == null);
            counts["≤10.0"] = 2;
            counts["≤9.0"] = 3;
            counts["≤8.0"] = 3;
            Expect("qm std top", ReportsQmStat.TopStandard(counts) == "≤8.0");
        }

        static void CheckGroups()
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Line("9909002", "001", "外观", "2026-04-02", "C2", "棕色", "合格", "B9"));
            rows.Add(Line("9909001", "002", "水分", "2026-04-03", "C3", "9.5", "合格", "B1"));
            rows.Add(Line("9909001", "002", "水分", "2026-05-04", "C5", "11.5", "不良", "B2"));
            rows.Add(Line("9909001", "002", "水分", "2026-05-06", "C6", "文字", "不良", "B3"));
            rows.Add(Line("9909001", "001", "外观", "2026-05-06", "C6", "", "合格", ""));
            List<QmGroup> groups = ReportsQmIndicators.Groups(rows);
            Expect("qm groups order", groups.Count == 3, groups[0].GuideCode == "001", groups[1].GuideCode == "002",
                groups[2].InvCode == "9909002");
            Dictionary<string, object> item = ReportsQmIndicators.Item(groups[1]);
            Expect("qm item counts", (int)item["n"] == 3, (int)item["numeric_n"] == 2, (int)item["bad_n"] == 2);
            Expect("qm item stats", (decimal)item["mean"] == 10.5m, (decimal)item["min"] == 9.5m,
                (decimal)item["max"] == 11.5m, item["stdev"] is decimal, item["standard"] as string == "≤10.0");
            List<object> months = (List<object>)item["months"];
            Dictionary<string, object> may = (Dictionary<string, object>)months[1];
            Expect("qm item months", months.Count == 2, (string)may["month"] == "2026-05", (int)may["n"] == 2,
                (decimal)may["mean"] == 11.5m, (int)may["bad_n"] == 2);
            List<object> bad = (List<object>)item["bad_batches"];
            Dictionary<string, object> first = (Dictionary<string, object>)bad[0];
            Expect("qm item bad", bad.Count == 2, (string)first["check_code"] == "C6", (string)first["value"] == "文字");
            Dictionary<string, object> text = ReportsQmIndicators.Item(groups[0]);
            Expect("qm item text", text["mean"] == null, text["min"] == null, text["stdev"] == null,
                (int)text["numeric_n"] == 0, text["bad_batches"] is List<object>);
            Expect("qm bad cap", ReportsQmStat.NewestBad(ManyBad(25)).Count == ReportsQmStat.BadBatchMax,
                ReportsQmStat.NewestBad(ManyBad(25))[0].Code == "C24");
        }

        static void CheckPassRate()
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["k1"] = "2026-04";
            row["docs"] = "10";
            row["fail_docs"] = "2";
            row["undecided_docs"] = "1";
            row["con_docs"] = "1";
            row["qty"] = "1000.000000";
            row["reg_qty"] = "800.5";
            row["con_qty"] = ".000000";
            row["dis_qty"] = "199.5";
            Dictionary<string, object> item = ReportsQmPassRate.Item(row, "month");
            Expect("qm rate item", (string)item["month"] == "2026-04", (int)item["pass_docs"] == 7,
                (int)item["fail_docs"] == 2, (int)item["undecided_docs"] == 1, (int)item["concession_docs"] == 1,
                (decimal)item["reg_qty"] == 800.5m);
            Dictionary<string, decimal> sums = new Dictionary<string, decimal>();
            ReportsQmPassRate.Sum(sums, item);
            ReportsQmPassRate.Sum(sums, item);
            Dictionary<string, object> totals = ReportsQmPassRate.Totals(sums);
            Expect("qm rate totals", (int)totals["docs"] == 20, (int)totals["pass_docs"] == 14,
                (int)totals["undecided_docs"] == 2, (decimal)totals["qty"] == 2000m, totals.Count == 9);
            row["k1"] = "S900001";
            row["k2"] = "";
            Dictionary<string, object> vendor = ReportsQmPassRate.Item(row, "vendor");
            Expect("qm rate vendor", (string)vendor["vendor_code"] == "S900001", vendor["vendor_name"] == null,
                !vendor.ContainsKey("month"));
            row["k1"] = "9909001";
            row["k3"] = "KG";
            Dictionary<string, object> inv = ReportsQmPassRate.Item(row, "inventory");
            Expect("qm rate inv", (string)inv["inv_code"] == "9909001", (string)inv["unit"] == "KG");
        }

        static void CheckSql()
        {
            string[] groups = new string[] { "month", "vendor", "inventory" };
            for (int i = 0; i < groups.Length; i++)
            {
                string sql = ReportsQmPassRateSql.For(groups[i], " AND h.CINVCODE IN (?)");
                Expect("qm sql marks " + groups[i], Count(sql, '?') == 4, sql.IndexOf('{') < 0,
                    sql.IndexOf("不接受", StringComparison.Ordinal) >= 0, sql.IndexOf("undecided_docs", StringComparison.Ordinal) >= 0);
            }
            string lines = ReportsQmIndicatorsSql.Lines.Replace("{FILTER}", "");
            Expect("qm sql lines", Count(lines, '?') == 3, lines.IndexOf('{') < 0);
        }

        static int Count(string text, char c)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                n += text[i] == c ? 1 : 0;
            }
            return n;
        }

        static List<QmBadBatch> ManyBad(int count)
        {
            List<QmBadBatch> list = new List<QmBadBatch>();
            for (int i = 0; i < count; i++)
            {
                QmBadBatch b = new QmBadBatch();
                b.Seq = i;
                b.Date = "2026-04-01";
                b.Code = "C" + i.ToString("00");
                list.Add(b);
            }
            return list;
        }

        // 一行检验明细；f 依次是：存货、指标编码、指标名、日期、检验单号、检验值、单项判定、批号。
        static Dictionary<string, object> Line(params string[] f)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["inv"] = f[0];
            row["inv_name"] = "示例存货";
            row["guide"] = f[1];
            row["guide_name"] = f[2];
            row["guide_unit"] = "KG";
            row["std"] = "≤10.0";
            row["val"] = f[5];
            row["jug"] = f[6];
            row["dt"] = f[3];
            row["code"] = f[4];
            row["batch"] = f[7];
            return row;
        }

        static Dictionary<string, object> Body(string kind, string from)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["kind"] = kind;
            body["date_from"] = from;
            return body;
        }

        static Dictionary<string, object> Without(Dictionary<string, object> body, string key)
        {
            body.Remove(key);
            return body;
        }

        static void Bad(string name, string route, Dictionary<string, object> body, string field)
        {
            try
            {
                ReportsQmReq.Check(route, body);
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && ex.Field == field)
                {
                    return;
                }
                throw new InvalidOperationException(name + " field=" + ex.Field);
            }
            throw new InvalidOperationException(name);
        }

        // 逐项核对，任一项为假即失败。
        static void Expect(string name, params bool[] checks)
        {
            for (int i = 0; i < checks.Length; i++)
            {
                if (!checks[i])
                {
                    throw new InvalidOperationException(name + " #" + i);
                }
            }
        }
    }
}
