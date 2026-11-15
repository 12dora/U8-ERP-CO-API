using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的收付款与现金流报表族 Fin 部分（不连库）：参数校验与缺省、登录子系统、SQL 的 ? 与参数一一对应、
    // 口径条件（单据类型、审核、期初、未记账）、票据状态与到期天数、现金流量项目的符号与分期折叠。
    internal static class FinSelfTest
    {
        public static void Run()
        {
            CheckRoutes();
            CheckRecPayParse();
            CheckRecPayRefused();
            CheckNotesParse();
            CheckCashParse();
            CheckRecPaySql();
            CheckRecPayMaskedOrder();
            CheckNotesSql();
            CheckCashSql();
            CheckHelpers();
            CheckRecPayItems();
            CheckNotesItem();
            CheckCashFold();
        }

        static void CheckRoutes()
        {
            Expect("fin names", All(ReportsFinReq.Names.Length == 3, ReportsFinReq.Owns("receipts_payments"),
                ReportsFinReq.Owns("notes_due"), ReportsFinReq.Owns("cash_flow"), !ReportsFinReq.Owns("close_status")));
            Expect("fin specs", All(ReportsFinReq.Specs.Length == 3, ReportsFinReq.Specs[0][0] == "/u8co/v1/reports/receipts_payments",
                ReportsFinReq.Specs[1][0] == "/u8co/v1/reports/notes_due", ReportsFinReq.Specs[2][0] == "/u8co/v1/reports/cash_flow",
                Array.IndexOf(ReportsFinReq.Specs[0], "after") < 0, Array.IndexOf(ReportsFinReq.Specs[2], "fiscal_year") > 0));
            Expect("fin sub ar", ReportsFinReq.SubOf("receipts_payments", Side("ar")) == "AR");
            Expect("fin sub ap", All(ReportsFinReq.SubOf("receipts_payments", Side("ap")) == "AP",
                ReportsFinReq.SubOf("notes_due", Side("ap")) == "AP", ReportsFinReq.SubOf("notes_due", Side("ar")) == "AR"));
            Expect("fin sub gl", ReportsFinReq.SubOf("cash_flow", new Dictionary<string, object>()) == "GL");
            Expect("fin sub other", ReportsFinReq.SubOf("gl_balance", Side("ar")) == null);
            Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map =
                new Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>();
            ReportsFinReq.AddRunners(map);
            Expect("fin runners", All(map.Count == 3, map.ContainsKey("receipts_payments"), map.ContainsKey("notes_due"),
                map.ContainsKey("cash_flow")));
        }

        static void CheckRecPayParse()
        {
            FinRecPayArgs q = ReportsFinParse.RecPay(Rec("ar", "2026-09-01"));
            Expect("rec defaults", All(q.Side == "ar", q.DateFrom == "2026-09-01", q.DateTo == "", q.GroupBy.Length == 1,
                q.GroupBy[0] == "day", q.Partners.Length == 0, q.Persons.Length == 0, q.Depts.Length == 0, !q.Unverified,
                q.Limit == 200));
            Dictionary<string, object> body = Rec("ap", "2026-09-01");
            body["date_to"] = "2026-09-30";
            body["group_by"] = new object[] { "settle", "month" };
            body["partner"] = new object[] { " S900001 ", "S900002" };
            body["person"] = new ArrayList(new object[] { "P01" });
            body["dept"] = new object[] { "D01" };
            body["include_unverified"] = true;
            body["limit"] = 1000;
            q = ReportsFinParse.RecPay(body);
            Expect("rec fields", All(q.Side == "ap", q.DateTo == "2026-09-30", q.GroupBy[0] == "settle", q.GroupBy[1] == "month",
                q.Partners[0] == "S900001", q.Partners.Length == 2, q.Persons[0] == "P01", q.Depts[0] == "D01", q.Unverified,
                q.Limit == 1000));
            body["group_by"] = new object[0];
            Expect("rec empty group", ReportsFinParse.RecPay(body).GroupBy.Length == 0);
        }

        static void CheckRecPayRefused()
        {
            Refused("rec no side", Without(Rec("ar", "2026-09-01"), "side"), "side");
            Refused("rec bad side", Rec("both", "2026-09-01"), "side");
            Refused("rec no from", Without(Rec("ar", "2026-09-01"), "date_from"), "date_from");
            Refused("rec bad date", Rec("ar", "2026-9-1"), "date_from");
            Refused("rec from > to", With(Rec("ar", "2026-09-10"), "date_to", "2026-09-01"), "date_from");
            Refused("rec group 3", With(Rec("ar", "2026-09-01"), "group_by", new object[] { "day", "month", "partner" }), "group_by");
            Refused("rec group text", With(Rec("ar", "2026-09-01"), "group_by", "day"), "group_by");
            Refused("rec group bad", With(Rec("ar", "2026-09-01"), "group_by", new object[] { "day", "week" }), "group_by.1");
            Refused("rec group dup", With(Rec("ar", "2026-09-01"), "group_by", new object[] { "day", "day" }), "group_by.1");
            Refused("rec group nonstring", With(Rec("ar", "2026-09-01"), "group_by", new object[] { 1 }), "group_by.0");
            Refused("rec partner empty", With(Rec("ar", "2026-09-01"), "partner", new object[0]), "partner");
            Refused("rec partner 21", With(Rec("ar", "2026-09-01"), "partner", Many(21)), "partner");
            Refused("rec partner blank", With(Rec("ar", "2026-09-01"), "partner", new object[] { "C1", "  " }), "partner.1");
            Refused("rec partner long", With(Rec("ar", "2026-09-01"), "partner", new object[] { new string('C', 61) }), "partner.0");
            Refused("rec partner control", With(Rec("ar", "2026-09-01"), "dept", new object[] { "D\u0001" }), "dept.0");
            Refused("rec person text", With(Rec("ar", "2026-09-01"), "person", "P01"), "person");
            Refused("rec unverified text", With(Rec("ar", "2026-09-01"), "include_unverified", "true"), "include_unverified");
            Refused("rec limit 0", With(Rec("ar", "2026-09-01"), "limit", 0), "limit");
            Refused("rec limit 1001", With(Rec("ar", "2026-09-01"), "limit", 1001), "limit");
            Refused("rec limit text", With(Rec("ar", "2026-09-01"), "limit", "5"), "limit");
        }

        static void CheckNotesParse()
        {
            FinNotesArgs q = ReportsFinParse.Notes(Side("ar"));
            Expect("notes defaults", All(q.Side == "ar", q.ExpireFrom == "", q.ExpireTo == "", q.OpenOnly, q.Partners.Length == 0,
                q.Limit == 200));
            Dictionary<string, object> body = Side("ap");
            body["expire_from"] = "2026-10-01";
            body["expire_to"] = "2026-10-31";
            body["open_only"] = false;
            body["partner"] = new object[] { "S900001" };
            body["limit"] = 5;
            q = ReportsFinParse.Notes(body);
            Expect("notes fields", All(q.ExpireFrom == "2026-10-01", q.ExpireTo == "2026-10-31", !q.OpenOnly,
                q.Partners[0] == "S900001", q.Limit == 5));
            Refused("notes no side", new Dictionary<string, object>(), "side", ReportsFinReq.NotesName);
            Refused("notes bad side", Side("x"), "side", ReportsFinReq.NotesName);
            Refused("notes from > to", With(With(Side("ar"), "expire_from", "2026-10-31"), "expire_to", "2026-10-01"), "expire_from",
                ReportsFinReq.NotesName);
            Refused("notes bad date", With(Side("ar"), "expire_to", "next month"), "expire_to", ReportsFinReq.NotesName);
            Refused("notes open text", With(Side("ar"), "open_only", 1), "open_only", ReportsFinReq.NotesName);
            Refused("notes partner empty", With(Side("ar"), "partner", new object[0]), "partner", ReportsFinReq.NotesName);
            Refused("notes limit", With(Side("ar"), "limit", 1001), "limit", ReportsFinReq.NotesName);
        }

        static void CheckCashParse()
        {
            FinCashArgs q = ReportsFinParse.Cash(new Dictionary<string, object>());
            Expect("cash defaults", All(q.From == 1, q.To == 0, !q.Unposted));
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["period_from"] = 3;
            body["period_to"] = 9;
            body["include_unposted"] = true;
            q = ReportsFinParse.Cash(body);
            Expect("cash fields", All(q.From == 3, q.To == 9, q.Unposted));
            Refused("cash from 0", With(new Dictionary<string, object>(), "period_from", 0), "period_from", ReportsFinReq.CashName);
            Refused("cash to 13", With(new Dictionary<string, object>(), "period_to", 13), "period_to", ReportsFinReq.CashName);
            Refused("cash from > to", With(With(new Dictionary<string, object>(), "period_from", 6), "period_to", 5), "period_from",
                ReportsFinReq.CashName);
            Refused("cash unposted text", With(new Dictionary<string, object>(), "include_unposted", "yes"), "include_unposted",
                ReportsFinReq.CashName);
        }

        // 字段权限会遮金额时：截断和输出顺序都不用 g.amt，换成单据数 g.docs（日期维度仍先按日期）。
        static void CheckRecPayMaskedOrder()
        {
            FinRecPayArgs q = ReportsFinParse.RecPay(With(Rec("ar", "2026-09-01"), "group_by", new object[] { "month", "partner" }));
            q.NoAmountOrder = true;
            string sql = ReportsFinRecPaySql.Group(null, q, "2026-09-30", 11, new List<object>());
            Expect("rec masked cut", sql.Contains("kept AS (SELECT TOP (?) g.* FROM g ORDER BY g.docs DESC, g.k_month, g.k_partner)"));
            Expect("rec masked order", sql.EndsWith("ORDER BY g.k_month, g.docs DESC, g.k_partner", StringComparison.Ordinal)
                && !sql.Contains("ORDER BY g.amt"));
        }

        // 分组查询的 ? 顺序：主类型、退款类型、cFlag、主类型、退款类型、起、止、（编码…）、TOP 行数。
        static void CheckRecPaySql()
        {
            FinRecPayArgs q = ReportsFinParse.RecPay(With(With(Rec("ar", "2026-09-01"), "group_by", new object[] { "month", "partner" }),
                "partner", new object[] { "C900001", "C900002" }));
            List<object> ps = new List<object>();
            string sql = ReportsFinRecPaySql.Group(null, q, "2026-09-30", 201, ps);
            Expect("rec placeholders", All(Count(sql) == ps.Count, ps.Count == 10, (string)ps[0] == "48", (string)ps[1] == "49",
                (string)ps[2] == "AR", (string)ps[3] == "48", (string)ps[4] == "49", (string)ps[5] == "2026-09-01",
                (string)ps[6] == "2026-09-30", (string)ps[7] == "C900001", (string)ps[8] == "C900002", (int)ps[9] == 201));
            Expect("rec sql", All(sql.Contains("FROM Ap_CloseBill h WHERE h.cFlag=?"), sql.Contains("ISNULL(h.bStartFlag, 0)=0"),
                sql.Contains("NULLIF(LTRIM(RTRIM(h.cCheckMan)), N'') IS NOT NULL"), sql.Contains("h.cDwCode IN (?, ?)"),
                sql.Contains("CONVERT(char(7), h.dVouchDate, 23) k_month"), sql.Contains("ISNULL(h.cDwCode, N'') k_partner"),
                sql.Contains("GROUP BY CONVERT(char(7), h.dVouchDate, 23), ISNULL(h.cDwCode, N'')"),
                sql.Contains("LEFT JOIN Customer pc ON pc.cCusCode=g.k_partner"), sql.Contains("pc.cCusName n_partner"),
                sql.Contains("kept AS (SELECT TOP (?) g.* FROM g ORDER BY g.amt DESC, g.k_month, g.k_partner)"),
                sql.EndsWith("ORDER BY g.k_month, g.amt DESC, g.k_partner", StringComparison.Ordinal), !sql.Contains("{")));
            ps = new List<object>();
            q = ReportsFinParse.RecPay(With(With(Rec("ap", "2026-09-01"), "group_by", new object[] { "settle", "person", }),
                "include_unverified", true));
            sql = ReportsFinRecPaySql.Group(null, q, "2026-09-30", 11, ps);
            Expect("rec ap sql", All(Count(sql) == ps.Count, ps.Count == 8, (string)ps[0] == "49", (string)ps[1] == "48",
                (string)ps[2] == "AP", !sql.Contains("cCheckMan"), sql.Contains("LEFT JOIN SettleStyle ss"),
                sql.Contains("LEFT JOIN Person pp"), sql.EndsWith("ORDER BY g.amt DESC, g.k_settle, g.k_person", StringComparison.Ordinal)));
            q = ReportsFinParse.RecPay(With(Rec("ap", "2026-09-01"), "group_by", new object[] { "partner", "department" }));
            sql = ReportsFinRecPaySql.Group(null, q, "2026-09-30", 11, new List<object>());
            Expect("rec ap names", All(sql.Contains("LEFT JOIN Vendor pc ON pc.cVenCode=g.k_partner"), sql.Contains("pc.cVenName n_partner"),
                sql.Contains("LEFT JOIN Department dd ON dd.cDepCode=g.k_department"), sql.Contains("dd.cDepName n_department")));
            ps = new List<object>();
            sql = ReportsFinRecPaySql.Total(null, ReportsFinParse.RecPay(Rec("ar", "2026-09-01")), "2026-09-30", ps);
            Expect("rec total", All(Count(sql) == ps.Count, ps.Count == 7, !sql.Contains("GROUP BY"), sql.Contains("COUNT(*) docs")));
        }

        // 列表的 ? 顺序：TOP 行数、cFlag、起、止、（编码…）。
        static void CheckNotesSql()
        {
            FinNotesArgs q = ReportsFinParse.Notes(With(Side("ar"), "partner", new object[] { "C900001" }));
            List<object> ps = new List<object>();
            string sql = ReportsFinNotesSql.List(null, q, "2026-10-15", "2026-11-14", 201, ps);
            Expect("notes placeholders", All(Count(sql) == ps.Count, ps.Count == 5, (int)ps[0] == 201, (string)ps[1] == "AR",
                (string)ps[2] == "2026-10-15", (string)ps[3] == "2026-11-14", (string)ps[4] == "C900001"));
            Expect("notes sql", All(sql.Contains("FROM AP_Note h"), sql.Contains("h.iRAmount_Local, 0)<>0"),
                sql.Contains("LEFT JOIN Customer pc ON pc.cCusCode=h.cDwCode"), sql.Contains("pc.cCusName"),
                sql.Contains("OUTER APPLY (SELECT TOP 1 s.cProcStyle FROM AP_Note_Sub s WHERE s.cLink=h.cLink"),
                sql.EndsWith("ORDER BY h.dExpireDate, h.cVouchID", StringComparison.Ordinal), !sql.Contains("{")));
            q = ReportsFinParse.Notes(With(Side("ap"), "open_only", false));
            ps = new List<object>();
            sql = ReportsFinNotesSql.List(null, q, "2026-10-15", "2026-11-14", 6, ps);
            Expect("notes all ap", All(Count(sql) == ps.Count, ps.Count == 4, (string)ps[1] == "AP", !sql.Contains("<>0"),
                sql.Contains("LEFT JOIN Vendor pc ON pc.cVenCode=h.cDwCode"), sql.Contains("pc.cVenName")));
            ps = new List<object>();
            sql = ReportsFinNotesSql.Total(null, q, "2026-10-15", "2026-11-14", ps);
            Expect("notes total", All(Count(sql) == ps.Count, ps.Count == 3, sql.Contains("COUNT(*) cnt")));
        }

        static void CheckCashSql()
        {
            List<object> ps = new List<object>();
            string sql = ReportsFinCashSql.Items(false, 2026, 1, 9, ps);
            Expect("cash items posted", All(Count(sql) == ps.Count, ps.Count == 3, (int)ps[0] == 2026, (int)ps[1] == 1, (int)ps[2] == 9,
                sql.Contains("AND ISNULL(g.ibook, 0)=1"), sql.Contains("g.iflag<>1"), sql.Contains("g.isignseq=c.iSignSeq"),
                sql.Contains("LEFT JOIN fitemss98 f ON f.citemcode=c.cCashItem"), !sql.Contains("{")));
            ps = new List<object>();
            sql = ReportsFinCashSql.Items(true, 2026, 1, 9, ps);
            Expect("cash items unposted", All(Count(sql) == ps.Count, !sql.Contains("ibook"), sql.Contains("g.iflag<>1")));
            ps = new List<object>();
            sql = ReportsFinCashSql.Unassigned(false, 2026, 2, 8, ps);
            Expect("cash unassigned", All(Count(sql) == ps.Count, ps.Count == 4, (int)ps[1] == 2, (int)ps[2] == 8, (int)ps[3] == 2026,
                sql.Contains("N'1001%'"), sql.Contains("N'1002%'"), sql.Contains("N'1012%'"), sql.Contains("NOT EXISTS"),
                sql.Contains("ISNULL(g.ibook, 0)=1"), !sql.Contains("{")));
            ps = new List<object>();
            Expect("cash unassigned unposted", !ReportsFinCashSql.Unassigned(true, 2026, 2, 8, ps).Contains("ibook"));
        }

        static void CheckHelpers()
        {
            Expect("due to", All(ReportsFinParse.DueTo("2026-10-15") == "2026-11-14", ReportsFinParse.DueTo("2026-12-15") == "2027-01-14"));
            Expect("days between", All((int)ReportsFinParse.DaysBetween("2026-10-15", "2026-10-25") == 10,
                (int)ReportsFinParse.DaysBetween("2026-10-15", "2026-10-15") == 0,
                (int)ReportsFinParse.DaysBetween("2026-10-15", "2026-10-10") == -5,
                ReportsFinParse.DaysBetween("2026-10-15", "") == null));
            Expect("month of", All(ReportsFinParse.MonthOf("2026-10-15") == 10, ReportsFinParse.MonthOf("2026-01-31") == 1));
            try
            {
                ReportsFinParse.MonthOf("2026/10/15");
                throw new InvalidOperationException("month of bad");
            }
            catch (BridgeException ex)
            {
                Expect("month of bad status", ex.Status == 400);
            }
            Expect("rec types", All(ReportsFinRecPaySql.MainType("ar") == "48", ReportsFinRecPaySql.RefundType("ar") == "49",
                ReportsFinRecPaySql.MainType("ap") == "49", ReportsFinRecPaySql.RefundType("ap") == "48"));
            Expect("note status", All(ReportsFinNotes.StatusOf(100m, "9E") == "open", ReportsFinNotes.StatusOf(100m, "") == "open",
                ReportsFinNotes.StatusOf(0m, "9A") == "settled", ReportsFinNotes.StatusOf(0m, "") == "settled",
                ReportsFinNotes.StatusOf(0m, "9D") == "discounted", ReportsFinNotes.StatusOf(0m, "9E") == "endorsed",
                ReportsFinNotes.StatusOf(0m, "9C") == "returned"));
        }

        static void CheckRecPayItems()
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["k_partner"] = "C900001";
            row["n_partner"] = "张三";
            row["k_day"] = "2026-09-02";
            row["docs"] = "3";
            row["amt"] = "1000.5000";
            row["refund"] = "200.2500";
            Dictionary<string, object> item = ReportsFinRecPay.Item(row, new string[] { "day", "partner" });
            Expect("rec item", All((string)item["date"] == "2026-09-02", (string)item["partner_code"] == "C900001",
                (string)item["partner_name"] == "张三", (int)item["docs"] == 3, (decimal)item["amount"] == 1000.50m,
                (decimal)item["refund_amount"] == 200.25m, (decimal)item["net_amount"] == 800.25m, item.Count == 7));
            // 单据上没填部门：编码和名称都是 null；没有符合条件的单据：合计全 0。
            row["k_department"] = "";
            item = ReportsFinRecPay.Item(row, new string[] { "department" });
            Expect("rec item blank", All(item["dept_code"] == null, item["dept_name"] == null, item.Count == 6));
            Dictionary<string, object> total = ReportsFinRecPay.Measures(null, new Dictionary<string, object>());
            Expect("rec zero totals", All((int)total["docs"] == 0, (decimal)total["amount"] == 0m, (decimal)total["refund_amount"] == 0m,
                (decimal)total["net_amount"] == 0m));
        }

        static void CheckNotesItem()
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["code"] = "N0001";
            row["note_type"] = "银行承兑票据";
            row["partner_name"] = "李四";
            row["sign_date"] = "2026-04-15";
            row["expire_date"] = "2026-10-25";
            row["amount"] = "50000.0000";
            row["remainder"] = "20000.0000";
            Dictionary<string, object> item = ReportsFinNotes.Item(row, "2026-10-15");
            Expect("note item", All((string)item["code"] == "N0001", item["partner_code"] == null, (string)item["partner_name"] == "李四",
                (string)item["status"] == "open", (int)item["days_to_due"] == 10, (decimal)item["remainder"] == 20000m,
                (decimal)item["amount"] == 50000m));
            row["remainder"] = "0.0000";
            row["last_proc"] = "9E";
            row["expire_date"] = "2026-10-01";
            item = ReportsFinNotes.Item(row, "2026-10-15");
            Expect("note item endorsed", All((string)item["status"] == "endorsed", (int)item["days_to_due"] == -14));
        }

        // 流入类取贷减借、流出类取借减贷；分期之和等于合计；范围外的期间不出现；按编码排序。
        static void CheckCashFold()
        {
            Expect("cash sign", All(ReportsFinCash.Signed("in", 10m, 100m) == 90m, ReportsFinCash.Signed("out", 100m, 10m) == 90m,
                ReportsFinCash.Signed("out", 0m, 10m) == -10m, ReportsFinCash.Signed(null, 10m, 100m) == 90m,
                ReportsFinCash.DirectionOf("流入") == "in", ReportsFinCash.DirectionOf("流出") == "out",
                ReportsFinCash.DirectionOf("") == null));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(CashRow("04", "流出", 2, "100.50", "0"));
            rows.Add(CashRow("01", "流入", 1, "5", "300"));
            rows.Add(CashRow("04", "流出", 3, "10", "0.25"));
            rows.Add(CashRow("01", "流入", 2, "0", "50.10"));
            rows.Add(CashRow("99", "", 1, "7", "0"));
            List<object> items = ReportsFinCash.Fold(rows, 1, 3);
            Dictionary<string, object> first = (Dictionary<string, object>)items[0];
            Dictionary<string, object> second = (Dictionary<string, object>)items[1];
            Dictionary<string, object> third = (Dictionary<string, object>)items[2];
            Dictionary<string, object> periods = (Dictionary<string, object>)first["periods"];
            Expect("cash order", All(items.Count == 3, (string)first["code"] == "01", (string)second["code"] == "04",
                (string)third["code"] == "99"));
            Expect("cash inflow", All((string)first["direction"] == "in", (decimal)first["amount"] == 345.10m, periods.Count == 3,
                (decimal)periods["1"] == 295m, (decimal)periods["2"] == 50.10m, (decimal)periods["3"] == 0m));
            periods = (Dictionary<string, object>)second["periods"];
            Expect("cash outflow", All((string)second["direction"] == "out", (decimal)second["amount"] == 110.25m,
                (decimal)periods["2"] == 100.50m, (decimal)periods["3"] == 9.75m));
            Expect("cash unknown", All(third["direction"] == null, third["name"] == null, (decimal)third["amount"] == -7m));
            List<object> narrow = ReportsFinCash.Fold(rows, 2, 2);
            periods = (Dictionary<string, object>)((Dictionary<string, object>)narrow[0])["periods"];
            Expect("cash window", All(periods.Count == 1, (decimal)periods["2"] == 50.10m,
                (decimal)((Dictionary<string, object>)narrow[0])["amount"] == 50.10m));
            Expect("cash empty", ReportsFinCash.Fold(new List<Dictionary<string, object>>(), 1, 12).Count == 0);
        }

        static Dictionary<string, object> CashRow(string code, string direction, int period, string md, string mc)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["code"] = code;
            row["direction"] = direction;
            row["per"] = period.ToString();
            row["md"] = md;
            row["mc"] = mc;
            if (direction.Length > 0)
            {
                row["name"] = "项目" + code;
                row["class_code"] = "01";
                row["class_name"] = "经营活动";
            }
            return row;
        }

        static Dictionary<string, object> Side(string side)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["side"] = side;
            return body;
        }

        static Dictionary<string, object> Rec(string side, string from)
        {
            Dictionary<string, object> body = Side(side);
            body["date_from"] = from;
            return body;
        }

        static Dictionary<string, object> With(Dictionary<string, object> body, string key, object value)
        {
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> Without(Dictionary<string, object> body, string key)
        {
            body.Remove(key);
            return body;
        }

        static object[] Many(int count)
        {
            object[] list = new object[count];
            for (int i = 0; i < count; i++)
            {
                list[i] = "C" + i.ToString();
            }
            return list;
        }

        static int Count(string sql)
        {
            int n = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                n += sql[i] == '?' ? 1 : 0;
            }
            return n;
        }

        static void Refused(string name, Dictionary<string, object> body, string field)
        {
            Refused(name, body, field, ReportsFinReq.RecPayName);
        }

        // 走 Check（登录前校验）；必须是 400 且 field 对得上。
        static void Refused(string name, Dictionary<string, object> body, string field, string route)
        {
            try
            {
                ReportsFinReq.Check(route, body);
            }
            catch (BridgeException ex)
            {
                Expect(name, All(ex.Status == 400, ex.Field == field));
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool All(params bool[] checks)
        {
            return Array.TrueForAll(checks, delegate(bool ok) { return ok; });
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
