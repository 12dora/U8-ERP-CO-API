using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的经营管理报表（总账口径）：mgmt/pnl、mgmt/meta、mgmt/cash_stock 的参数校验、SQL 文本与占位符、
    // 读线程池、登录子系统与权限规则。不连库。
    internal static class MgmtGlSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckRefused();
            CheckPnlSql();
            CheckCashSql();
            CheckMetaSql();
            CheckWiring();
        }

        static void CheckParse()
        {
            MgmtGlArgs m = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.PnlName, Body("{\"period_from\":1,\"period_to\":8}"));
            Expect("pnl defaults", All(m.From == 1, m.To == 8, !m.Unposted, m.Detail == "prefix4", !m.ByDept, !m.ByItem,
                m.PlAccounts.Length == 1 && m.PlAccounts[0] == "6", m.ProfitAccount == "4103"));
            m = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.PnlName, Body("{\"period_from\":3,\"period_to\":3,\"include_unposted\":true,"
                + "\"detail\":\"leaf\",\"dims\":[\"item\",\"dept\"],\"pl_accounts\":[\"5\"],\"profit_account\":\"3131\"}"));
            Expect("pnl full", All(m.Unposted, m.Detail == "leaf", m.ByDept, m.ByItem, m.PlAccounts[0] == "5", m.ProfitAccount == "3131"));
            m = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.PnlName, Body("{\"period_from\":1,\"period_to\":1,\"dims\":[]}"));
            Expect("pnl no dims", !m.ByDept && !m.ByItem);
            MgmtGlArgs c = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.CashName, Body("{\"period\":8}"));
            Expect("cash defaults", All(c.Period == 8, c.Top == 200, c.PurchaseSource == "auto", !c.Unverified,
                string.Join(",", c.CashAccounts) == "1001,1002,1012", string.Join(",", c.NotesAccounts) == "1121"));
            c = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.CashName, Body("{\"period\":12,\"top\":5,\"purchase_source\":\"receipt\","
                + "\"cash_accounts\":[\"1002\"],\"include_unverified\":true}"));
            Expect("cash full", All(c.Top == 5, c.PurchaseSource == "receipt", c.Unverified, c.CashAccounts.Length == 1));
            ReportsReq.Parse(ReportsMgmtGlReq.MetaName, Body("{\"fiscal_year\":2026}"));
            string[] feb = ReportsMgmtCash.PeriodRange(ReportsMgmtSales.CalendarBounds(2028, 2, 2));
            Expect("month range", feb[0] == "2028-02-01" && feb[1] == "2028-02-29");
            string[] u8 = ReportsMgmtCash.PeriodRange(new string[] { "2026-01-26", "2026-02-26" });
            Expect("u8 period range", u8[0] == "2026-01-26" && u8[1] == "2026-02-25");
        }

        static void CheckRefused()
        {
            string pnl = ReportsMgmtGlReq.PnlName;
            Refused("pnl no period", pnl, "{\"period_to\":3}");
            Refused("pnl order", pnl, "{\"period_from\":4,\"period_to\":3}");
            Refused("pnl period 13", pnl, "{\"period_from\":1,\"period_to\":13}");
            Refused("pnl detail", pnl, "{\"period_from\":1,\"period_to\":3,\"detail\":\"grade2\"}");
            Refused("pnl dims", pnl, "{\"period_from\":1,\"period_to\":3,\"dims\":[\"person\"]}");
            Refused("pnl dims dup", pnl, "{\"period_from\":1,\"period_to\":3,\"dims\":[\"dept\",\"dept\"]}");
            Refused("pnl dims text", pnl, "{\"period_from\":1,\"period_to\":3,\"dims\":\"dept\"}");
            Refused("pnl unposted", pnl, "{\"period_from\":1,\"period_to\":3,\"include_unposted\":1}");
            Refused("pnl pl wildcard", pnl, "{\"period_from\":1,\"period_to\":3,\"pl_accounts\":[\"6%\"]}");
            Refused("pnl pl empty", pnl, "{\"period_from\":1,\"period_to\":3,\"pl_accounts\":[]}");
            Refused("pnl profit", pnl, "{\"period_from\":1,\"period_to\":3,\"profit_account\":\"41_3\"}");
            string cash = ReportsMgmtGlReq.CashName;
            Refused("cash no period", cash, "{}");
            Refused("cash top", cash, "{\"period\":1,\"top\":501}");
            Refused("cash source", cash, "{\"period\":1,\"purchase_source\":\"order\"}");
            Refused("cash accounts", cash, "{\"period\":1,\"cash_accounts\":[\"10'01\"]}");
        }

        static void CheckPnlSql()
        {
            MgmtGlArgs m = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.PnlName, Body("{\"period_from\":2,\"period_to\":5}"));
            List<object> ps = new List<object>();
            string sql = ReportsMgmtPnlSql.Query(m, 2026, ps, null);
            // 年度、起、止、本年利润 ×2 | 年度、起、止、损益 ×1 | TOP、年度。手工结转按科目类型 cclass 判断，不用损益前缀。
            Expect("pnl params", All(Count(sql, '?') == ps.Count, ps.Count == 11, (int)ps[0] == 2026, (int)ps[2] == 5,
                (string)ps[3] == "4103%", (string)ps[4] == "4103%", (string)ps[8] == "6%", (int)ps[9] == ReportsMgmtGlReq.MaxRows + 1,
                (int)ps[10] == 2026));
            Expect("pnl sql", All(sql.Contains("t.coutsign = N'期间损益'"), sql.Contains("OR tc.cclass = N'损益' THEN 0 ELSE 1"),
                sql.Contains("LEFT JOIN code tc ON tc.iyear = t.iyear AND tc.ccode = t.ccode"), !sql.Contains("t.ccode LIKE ? OR t.ccode"), sql.Contains("AND ISNULL(v.ibook, 0) = 1"),
                sql.Contains("NOT EXISTS (SELECT 1 FROM tr WHERE tr.iperiod = v.iperiod AND tr.csign = v.csign"),
                sql.Contains("(v.iflag IS NULL OR v.iflag <> 1)"), sql.Contains("LEFT(v.ccode, 4) AS code"),
                !sql.Contains("cdept_id"), !sql.Contains("Department"), !sql.Contains("cdigest"),
                sql.EndsWith(" ORDER BY g.period, g.code", StringComparison.Ordinal)));
            m = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.PnlName, Body("{\"period_from\":1,\"period_to\":12,\"include_unposted\":true,"
                + "\"detail\":\"leaf\",\"dims\":[\"dept\",\"item\"],\"pl_accounts\":[\"5\",\"6\"]}"));
            ps = new List<object>();
            sql = ReportsMgmtPnlSql.Query(m, 2025, ps, null);
            Expect("pnl leaf params", All(Count(sql, '?') == ps.Count, ps.Count == 12));
            Expect("pnl leaf sql", All(!sql.Contains("ibook, 0) = 1 AND"), !sql.Contains("AND ISNULL(v.ibook, 0) = 1"),
                sql.Contains("v.iperiod AS period, v.ccode AS code, ISNULL(v.cdept_id, N'') AS dept"),
                sql.Contains("GROUP BY v.iperiod, v.ccode, ISNULL(v.cdept_id, N''), ISNULL(v.citem_class, N''), ISNULL(v.citem_id, N'')"),
                sql.Contains("LEFT JOIN Department d ON d.cDepCode = g.dept"),
                sql.EndsWith(" ORDER BY g.period, g.code, g.dept, g.item_class, g.item", StringComparison.Ordinal)));
            string periods = ReportsMgmtPnlSql.PeriodsSql;
            Expect("pnl periods sql", Count(periods, '?') == 3 && periods.Contains("FROM GL_mend m WHERE m.iyear = ?"));
        }

        static void CheckCashSql()
        {
            string[] range = new string[] { "2026-08-01", "2026-08-31" };
            List<object> ps = new List<object>();
            string sql = ReportsMgmtCashSql.Balances(2026, 8, new string[] { "1001", "1002" }, ps, null);
            Expect("cash sql", All(Count(sql, '?') == ps.Count, ps.Count == 4, (string)ps[3] == "1002%",
                sql.Contains("c.bend = 1"), sql.Contains("WHEN N'贷' THEN -ISNULL(s.me, 0)")));
            MgmtGlArgs m = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.CashName, Body("{\"period\":8,\"top\":7}"));
            ps = new List<object>();
            sql = ReportsMgmtCashSql.Production(false, m, range, ps, null);
            Expect("production sql", All(Count(sql, '?') == ps.Count, ps.Count == 3, (int)ps[0] == 7, (string)ps[1] == range[0],
                sql.Contains("cHandler"), sql.Contains("h.dDate < DATEADD(day, 1, CONVERT(date, ?, 23))"),
                sql.Contains("ORDER BY SUM(ISNULL(b.iQuantity, 0)) DESC")));
            m.Unverified = true;
            ps = new List<object>();
            sql = ReportsMgmtCashSql.Production(true, m, range, ps, null);
            Expect("production total", All(Count(sql, '?') == 2, ps.Count == 2, !sql.Contains("cHandler"), !sql.Contains("TOP")));
            m.Unverified = false;
            m.Top = 9;
            ps = new List<object>();
            sql = ReportsMgmtCashSql.Purchases(true, false, m, range, ps, null);
            Expect("purchase invoice", All(Count(sql, '?') == ps.Count, ps.Count == 3, sql.Contains("FROM PurBillVouch h"),
                sql.Contains("h.dPBVDate >= CONVERT(date, ?, 23)"), !sql.Contains("cHandler"), sql.Contains("SUM(ISNULL(b.iSum, 0))")));
            ps = new List<object>();
            sql = ReportsMgmtCashSql.Purchases(false, true, m, range, ps, null);
            Expect("purchase receipt", All(Count(sql, '?') == ps.Count, ps.Count == 2, sql.Contains("FROM RdRecord01 h"),
                sql.Contains("cHandler"), sql.Contains("COUNT(DISTINCT h.cVenCode) AS n")));
            ps = new List<object>();
            sql = ReportsMgmtCashSql.Inventory(2026, 8, ps, null);
            Expect("cash inventory sql", All(Count(sql, '?') == 2, ps.Count == 2, sql.Contains("FROM IA_Summary s")));
            ps = new List<object>();
            sql = ReportsMgmtCashSql.NotesOpen(ps, null);
            Expect("cash notes sql", All(Count(sql, '?') == 0, ps.Count == 0, sql.Contains("cFlag = N'AR'"),
                sql.Contains("NULLIF(LTRIM(RTRIM(cEndorser)), N'')) AS cCusCode")));
            Expect("cash fixed sql", Count(ReportsMgmtCashSql.CloseSql, '?') == 2);
        }

        static void CheckMetaSql()
        {
            string start = ReportsMgmtMeta.StartSql();
            Expect("meta start sql", All(Count(start, '?') == 0, start.Contains("cName = N'dSaleStartDate'"),
                start.Contains("cName = N'dGLStartDate'"), start.Contains("cSysID = N'AP'")));
            string mark = ReportsMgmtMeta.MarkSql;
            Expect("meta mark sql", All(Count(mark, '?') == 1, mark.Contains("MAX(pubufts))) FROM code"),
                mark.Contains("MAX(pubufts))) FROM GL_mend"), mark.Contains("(SELECT MAX(i_id) FROM GL_accvouch)"),
                mark.Contains("FROM GL_accvouch WHERE iyear = ?) g"), mark.Contains("CHECKSUM_AGG(CHECKSUM(ccode, iperiod)) AS gl_checksum")));
        }

        static void CheckWiring()
        {
            string[] names = new string[] { ReportsMgmtGlReq.PnlName, ReportsMgmtGlReq.MetaName, ReportsMgmtGlReq.CashName };
            string[] paths = new string[] { ReportsMgmtGlReq.PnlPath, ReportsMgmtGlReq.MetaPath, ReportsMgmtGlReq.CashPath };
            string[] rules = new string[] { ReportsMgmtGlReq.PnlRule, ReportsMgmtGlReq.MetaRule, ReportsMgmtGlReq.CashRule };
            for (int i = 0; i < names.Length; i++)
            {
                WorkItem item = Item(paths[i]);
                PermRule rule = PermRegistry.ForKey(rules[i]);
                Expect("mgmt route " + names[i], All(Array.IndexOf(Reports.Names, names[i]) >= 0, Reports.SubOf(names[i], null) == "GL",
                    PermRegistry.IsRead(paths[i]), RouteClass.IsSqlRead(item), !WriteGate.IsWrite(item), rule != null,
                    PermRegistry.Find(item) == rule, rule.Auths.Length > 0));
            }
            Expect("mgmt pnl rule", PermRegistry.ForKey(ReportsMgmtGlReq.PnlRule).Objs[0].Obj == PermObj.Account
                && Array.IndexOf(PermRegistry.ForKey(ReportsMgmtGlReq.PnlRule).Auths, "GL030301") >= 0);
            Expect("mgmt meta rule", PermRegistry.ForKey(ReportsMgmtGlReq.MetaRule).Objs.Length == 0);
            PermObj[] cash = PermRegistry.ForKey(ReportsMgmtGlReq.CashRule).Objs;
            Expect("mgmt cash rule", All(cash.Length == 5, cash[0].Obj == PermObj.Account, cash[1].Obj == PermObj.Customer,
                cash[2].Obj == PermObj.Inventory, cash[3].Obj == PermObj.Warehouse && cash[3].Optional,
                cash[4].Obj == PermObj.Vendor && cash[4].Column == "cVenCode"));
        }

        static void Refused(string name, string report, string json)
        {
            try
            {
                ReportsReq.Parse(report, Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static WorkItem Item(string path)
        {
            WorkItem item = new WorkItem();
            item.Path = path;
            item.Body = Body("{}");
            return item;
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
