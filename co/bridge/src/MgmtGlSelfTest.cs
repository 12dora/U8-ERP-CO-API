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
            CheckCashClass();
            CheckSections();
            CheckProductionRule();
            CheckProductionPermSql();
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

        // 产量的存货分类：class_grade 往上取祖先分类（? 排在 TOP 之后、日期之前），inv_class 过滤排行和合计两条 SQL。
        static void CheckCashClass()
        {
            string[] range = new string[] { "2026-08-01", "2026-08-31" };
            MgmtGlArgs c = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.CashName, Body("{\"period\":8,\"top\":7}"));
            Expect("cash class defaults", c.ClassGrade == 0 && c.InvClass == null);
            c = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.CashName, Body("{\"period\":8,\"top\":7,\"class_grade\":2,\"inv_class\":\"10_01\"}"));
            Expect("cash class parse", c.ClassGrade == 2 && c.InvClass == "10_01");
            List<object> ps = new List<object>();
            string sql = ReportsMgmtCashSql.Production(false, c, range, ps, null);
            Expect("production class", All(Count(sql, '?') == ps.Count, ps.Count == 5, (int)ps[0] == 7, (int)ps[1] == 2,
                (string)ps[2] == range[0], (string)ps[3] == range[1], (string)ps[4] == "10\\_01%",
                sql.Contains("COALESCE(cg.cInvCCode, i.cInvCCode) AS class_code"), sql.Contains("cc.cInvCName AS class_name"),
                sql.Contains("LEFT JOIN InventoryClass cc ON cc.cInvCCode = COALESCE(cg.cInvCCode, i.cInvCCode)"),
                sql.Contains("i.cInvCCode LIKE ? ESCAPE"), sql.Contains("OUTER APPLY (SELECT TOP 1 g.cInvCCode FROM InventoryClass g"),
                sql.Contains("GROUP BY b.cInvCode, i.cInvName, u.cComUnitName, COALESCE(cg.cInvCCode, i.cInvCCode), cc.cInvCName ORDER BY")));
            ps = new List<object>();
            sql = ReportsMgmtCashSql.Production(true, c, range, ps, null);
            Expect("production class total", All(Count(sql, '?') == ps.Count, ps.Count == 3, (string)ps[2] == "10\\_01%",
                sql.Contains("JOIN Inventory i ON i.cInvCode = b.cInvCode"), !sql.Contains("OUTER APPLY"), !sql.Contains("TOP")));
            c.ClassGrade = 0;
            c.InvClass = null;
            ps = new List<object>();
            sql = ReportsMgmtCashSql.Production(false, c, range, ps, null);
            Expect("production own class", All(Count(sql, '?') == ps.Count, ps.Count == 3, !sql.Contains("OUTER APPLY"),
                sql.Contains("i.cInvCCode AS class_code"), !sql.Contains("LIKE")));
            Refused("cash class grade", ReportsMgmtGlReq.CashName, "{\"period\":1,\"class_grade\":0}");
            Refused("cash class text", ReportsMgmtGlReq.CashName, "{\"period\":1,\"inv_class\":\" \"}");
        }

        // cash_stock 的 sections：缺省五段、取值与去重校验；只要 production 时功能权限和登录子系统都不同。
        static void CheckSections()
        {
            string cash = ReportsMgmtGlReq.CashName;
            MgmtGlArgs c = ReportsMgmtGlReq.Parse(cash, Body("{\"period\":8}"));
            Expect("sections default", All(c.Sections.Length == 5, c.Wants("cash"), c.Wants("notes"), c.Wants("inventory"),
                c.Wants("production"), c.Wants("purchases"), !c.ProductionOnly));
            c = ReportsMgmtGlReq.Parse(cash, Body("{\"period\":8,\"sections\":[\"production\"]}"));
            Expect("sections production", All(c.ProductionOnly, c.Wants("production"), !c.Wants("cash"), !c.Wants("notes"),
                !c.Wants("inventory"), !c.Wants("purchases")));
            c = ReportsMgmtGlReq.Parse(cash, Body("{\"period\":8,\"sections\":[\"purchases\",\"cash\"]}"));
            Expect("sections some", All(!c.ProductionOnly, c.Wants("purchases"), c.Wants("cash"), !c.Wants("production"), c.Sections[0] == "purchases"));
            c = ReportsMgmtGlReq.Parse(cash, Body("{\"period\":8,\"sections\":[\"production\",\"cash\"]}"));
            Expect("sections production plus", !c.ProductionOnly);
            Refused("sections empty", cash, "{\"period\":1,\"sections\":[]}");
            Refused("sections unknown", cash, "{\"period\":1,\"sections\":[\"gl\"]}");
            Refused("sections dup", cash, "{\"period\":1,\"sections\":[\"cash\",\"cash\"]}");
            Refused("sections text", cash, "{\"period\":1,\"sections\":\"production\"}");
            Refused("sections number", cash, "{\"period\":1,\"sections\":[1]}");
        }

        // 只要产量：规则是产成品入库单查询的功能 id（ASM0301），不收总账 id，数据权限对象与 voucher:product_in 完全相同；
        // 有任何别的段、没有 sections 或 sections 不合法都走总账规则。
        static void CheckProductionRule()
        {
            string path = ReportsMgmtGlReq.CashPath;
            WorkItem only = Item(path, "{\"period\":8,\"sections\":[\"production\"]}");
            PermRule rule = PermRegistry.Find(only);
            PermRule voucher = PermRegistry.ForKey("voucher:product_in");
            Expect("production rule found", rule != null && voucher != null);
            Expect("production rule key", All(rule.Key == ReportsMgmtGlReq.ProductionRule,
                ReportsMgmtGlReq.ProductionRule == "report:mgmt/cash_stock#production", rule.Auths.Length == 1,
                rule.Auths[0] == PermRegistry.ProductInAuth, voucher.Auths.Length == 1, voucher.Auths[0] == rule.Auths[0]));
            CheckSameObjs(rule, voucher);
            Expect("production pins", All(Holds(rule, "ASM0301"), !Holds(rule, "GL030301"), !Holds(rule, "GL0305"), !Holds(rule, "GL1512"),
                !Holds(rule, "ASM0401")));
            PermRule gl = PermRegistry.ForKey(ReportsMgmtGlReq.CashRule);
            Expect("cash gl pins", All(Holds(gl, "GL030301"), !Holds(gl, "ASM0301")));
            string[] others = new string[] { "{\"period\":8}", "{\"period\":8,\"sections\":[\"production\",\"purchases\"]}",
                "{\"period\":8,\"sections\":[\"cash\"]}", "{\"period\":8,\"sections\":[\"nope\"]}",
                "{\"period\":8,\"sections\":[\"production\",\"production\"]}", "{\"period\":8,\"sections\":\"production\"}" };
            for (int i = 0; i < others.Length; i++)
            {
                Expect("cash gl rule " + i, PermRegistry.Find(Item(path, others[i])) == gl);
            }
            Expect("sections sub", All(Reports.SubOf(ReportsMgmtGlReq.CashName, only.Body) == "ST",
                Reports.SubOf(ReportsMgmtGlReq.CashName, Item(path, others[0]).Body) == "GL",
                Reports.SubOf(ReportsMgmtGlReq.CashName, Item(path, others[2]).Body) == "GL",
                Reports.SubOf(ReportsMgmtGlReq.CashName, Item(path, others[3]).Body) == "GL",
                Reports.SubOf(ReportsMgmtGlReq.CashName, null) == "GL"));
            Expect("sections still sql read", All(PermRegistry.IsRead(path), RouteClass.IsSqlRead(only), !WriteGate.IsWrite(only)));
        }

        // 产量规则的数据权限对象与 voucher:product_in 逐项相同（对象、模式、列、可空），且部门、仓库必有。
        static void CheckSameObjs(PermRule rule, PermRule voucher)
        {
            Expect("production objs count", rule.Objs.Length == voucher.Objs.Length && rule.Objs.Length > 0);
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                PermObj a = rule.Objs[i];
                PermObj b = voucher.Objs[i];
                Expect("production obj " + i, All(a.Obj == b.Obj, a.Mode == b.Mode, a.Column == b.Column, a.Optional == b.Optional));
            }
            Expect("production dept required", ObjOf(rule, PermObj.Department) != null && !ObjOf(rule, PermObj.Department).Optional);
            Expect("production warehouse required", ObjOf(rule, PermObj.Warehouse) != null && !ObjOf(rule, PermObj.Warehouse).Optional);
            Expect("production person rd optional", All(ObjOf(rule, PermObj.Person) != null && ObjOf(rule, PermObj.Person).Optional,
                ObjOf(rule, PermObj.RdStyle) != null && ObjOf(rule, PermObj.RdStyle).Optional));
        }

        static PermObj ObjOf(PermRule rule, string obj)
        {
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                if (rule.Objs[i].Obj == obj)
                {
                    return rule.Objs[i];
                }
            }
            return null;
        }

        // 产量 SQL 在数据权限受控时带部门、仓库、业务员、收发类别（表头 h）和存货（表体行 b）的条件，? 与参数一一对应。
        static void CheckProductionPermSql()
        {
            PermContext p = new PermContext();
            p.Acc = "999";
            p.Operator = "U1";
            p.Year = 2026;
            string[] objs = new string[] { PermObj.Department, PermObj.Warehouse, PermObj.Person, PermObj.RdStyle, PermObj.Inventory };
            string[] codes = new string[] { "D901", "01", "P1", "R1", "A901" };
            for (int i = 0; i < objs.Length; i++)
            {
                p.On.Add(objs[i]);
                p.Codes[objs[i]] = new HashSet<string>(new string[] { codes[i] }, StringComparer.OrdinalIgnoreCase);
            }
            WorkContext ctx = new WorkContext();
            ctx.Item = Item(ReportsMgmtGlReq.CashPath, "{\"period\":8,\"sections\":[\"production\"]}");
            ctx.Perm = p;
            MgmtGlArgs m = ReportsMgmtGlReq.Parse(ReportsMgmtGlReq.CashName, ctx.Item.Body);
            string[] range = new string[] { "2026-08-01", "2026-08-31" };
            foreach (bool totals in new bool[] { false, true })
            {
                List<object> ps = new List<object>();
                string sql = ReportsMgmtCashSql.Production(totals, m, range, ps, ctx);
                Expect("production perm sql " + totals, All(Count(sql, '?') == ps.Count, ps.Count == (totals ? 7 : 8),
                    sql.Contains("h.cDepCode IN (?)"), sql.Contains("h.cWhCode IN (?)"), sql.Contains("b.cInvCode IN (?)"),
                    sql.Contains("(NULLIF(LTRIM(RTRIM(h.cPersonCode)), N'') IS NULL OR h.cPersonCode IN (?))"),
                    sql.Contains("(NULLIF(LTRIM(RTRIM(h.cRdCode)), N'') IS NULL OR h.cRdCode IN (?))"),
                    !sql.Contains("h.cCusCode"), !sql.Contains("h.cVenCode"), (string)ps[ps.Count - 5] == "01",
                    (string)ps[ps.Count - 4] == "D901", (string)ps[ps.Count - 1] == "A901"));
            }
        }

        static bool Holds(PermRule rule, string auth)
        {
            PermContext p = new PermContext();
            p.Funcs.Add(auth);
            return p.HasAny(rule.Auths);
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
            return Item(path, "{}");
        }

        static WorkItem Item(string path, string json)
        {
            WorkItem item = new WorkItem();
            item.Path = path;
            item.Body = Body(json);
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
