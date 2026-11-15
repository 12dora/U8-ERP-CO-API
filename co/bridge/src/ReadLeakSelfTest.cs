using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的读权限收紧：总账凭证按科目整张过滤、mgmt/meta 不给金额并带权限指纹、mgmt/pnl 按部门 / 项目聚合前过滤、
    // 固定资产报表按卡片部门过滤、幂等结果查询按操作员。只测纯逻辑与 SQL 文本、参数顺序，不连库。
    internal static class ReadLeakSelfTest
    {
        public static void Run()
        {
            CheckGlRule();
            CheckGlSql();
            CheckGlLines();
            CheckMeta();
            CheckPnlDept();
            CheckPnlItem();
            CheckMetaPerm();
            CheckFa();
            CheckIdem();
        }

        static void CheckGlRule()
        {
            PermRule rule = PermRegistry.ForKey("gl");
            Expect("gl rule obj", rule != null && rule.Objs.Length == 1 && rule.Objs[0].Mode == PermObj.GlLines
                && rule.Objs[0].Obj == PermObj.Account && rule.Objs[0].Column == "ccode");
        }

        // 整张凭证：分录里有一条科目不在授权内就去掉（NOT EXISTS … AND NOT (…)），外层没有别名时按表名关联。
        static void CheckGlSql()
        {
            PermContext p = Subj("1001", "6601");
            StringBuilder sb = new StringBuilder();
            List<object> ps = new List<object>();
            PermSql.AppendObj(sb, ps, p, PermObj.Gl(), null);
            string sql = sb.ToString();
            Expect("gl sql strict", sql.StartsWith(" AND GL_accvouch.isignseq IS NOT NULL AND NOT EXISTS (SELECT 1 FROM GL_accvouch pg"
                + " WHERE pg.iperiod=GL_accvouch.iperiod AND pg.isignseq=GL_accvouch.isignseq AND pg.ino_id=GL_accvouch.ino_id"
                + " AND pg.iyear=GL_accvouch.iyear AND pg.csign=GL_accvouch.csign AND NOT (", StringComparison.Ordinal)
                && sql.Contains("LTRIM(RTRIM(ISNULL(pg.ccode, N''))) IN (?, ?)") && sql.EndsWith("))", StringComparison.Ordinal));
            Expect("gl sql args", ps.Count == 2 && Count(sql, '?') == 2);
            // 关联条件都是「pg.列 = 外层.列」，pg 一侧不套函数，按聚集索引（iperiod, isignseq, ino_id）可查找。
            Expect("gl sql sargable", !sql.Contains("(pg.iperiod") && !sql.Contains("(pg.isignseq") && !sql.Contains("(pg.ino_id"));
            CheckGlSqlAlias();
            sb.Length = 0;
            ps.Clear();
            PermContext off = Subj("1001");
            off.GlSubjCtl = false;
            sb.Length = 0;
            PermSql.AppendObj(sb, ps, off, PermObj.Gl(), null);
            Expect("gl sql option off", sb.Length == 0 && ps.Count == 0);
            PermContext admin = Subj("1001");
            admin.Supervisor = true;
            PermSql.AppendObj(sb, ps, admin, PermObj.Gl(), null);
            Expect("gl sql supervisor", sb.Length == 0 && ps.Count == 0);
        }

        // 外层有别名、授权为空集合：按别名关联，一张都不给。
        static void CheckGlSqlAlias()
        {
            StringBuilder sb = new StringBuilder();
            List<object> ps = new List<object>();
            PermSql.AppendObj(sb, ps, Subj(), PermObj.Gl(), "v");
            string sql = sb.ToString();
            Expect("gl sql empty set", sql.StartsWith(" AND v.isignseq IS NOT NULL AND NOT EXISTS", StringComparison.Ordinal)
                && sql.Contains("pg.isignseq=v.isignseq") && sql.Contains("pg.iyear=v.iyear")
                && sql.EndsWith("AND NOT (1=0))", StringComparison.Ordinal) && ps.Count == 0);
        }

        // 单张凭证读取、附件：全部分录放行才给，没有分录也不给。
        static void CheckGlLines()
        {
            PermRule rule = PermRegistry.ForKey("gl");
            PermContext p = Subj("1001", "6601");
            PermCheck.CheckVoucher(p, rule, null, Lines("1001", "6601"));
            Denied("gl one line denied", p, rule, Lines("1001", "2202"));
            Denied("gl blank line denied", p, rule, Lines("1001", ""));
            Denied("gl no lines", p, rule, new List<object>());
            PermContext off = Subj("1001");
            off.GlSubjCtl = false;
            PermCheck.CheckVoucher(off, rule, null, Lines("2202"));
            PermContext admin = Subj();
            admin.Supervisor = true;
            PermCheck.CheckVoucher(admin, rule, null, Lines("2202"));
        }

        static void CheckMeta()
        {
            string mark = ReportsMgmtMeta.MarkSql;
            Expect("meta no money", !mark.Contains("gl_debit") && !mark.Contains("gl_credit") && !mark.Contains("SUM(ISNULL(md")
                && !mark.Contains("SUM(ISNULL(mc"));
            Expect("meta content checksum", mark.Contains("CHECKSUM_AGG(BINARY_CHECKSUM(i_id, ccode, iperiod, md, mc, ibook, iflag))"
                + " AS gl_content_checksum") && mark.Contains("g.gl_content_checksum") && Count(mark, '?') == 1);
        }

        static void CheckPnlDept()
        {
            StringBuilder sb = new StringBuilder();
            List<object> ps = new List<object>();
            ReportsMgmtPnlSql.DeptRows(sb, ps, Dept("D01"));
            Expect("pnl dept rows", sb.ToString() == " AND (NULLIF(LTRIM(RTRIM(v.cdept_id)), N'') IS NULL OR v.cdept_id IN (?))"
                && ps.Count == 1 && (string)ps[0] == "D01");
            sb.Length = 0;
            ps.Clear();
            ReportsMgmtPnlSql.DeptRows(sb, ps, Ctx());
            ReportsMgmtPnlSql.DeptRows(sb, ps, null);
            Expect("pnl dept off", sb.Length == 0 && ps.Count == 0);
        }

        // 按项目：项目开关打开时聚合前按（大类、编码）过滤，空项目放行；开关关着、没有快照时不加条件；Query 里接在 GROUP BY 之前。
        static void CheckPnlItem()
        {
            PermContext p = Ctx();
            p.On.Add(PermObj.Item);
            p.Codes[PermObj.Item] = Set("00" + PermContext.PairSep + "P1");
            StringBuilder sb = new StringBuilder();
            List<object> ps = new List<object>();
            ReportsMgmtPnlSql.ItemRows(sb, ps, p);
            Expect("pnl item rows", sb.ToString() == " AND (NULLIF(LTRIM(RTRIM(v.citem_id)), N'') IS NULL OR EXISTS (SELECT 1 FROM"
                + " (VALUES (?, ?)) pv(c, k) WHERE pv.c=v.citem_class AND pv.k=v.citem_id))"
                && ps.Count == 2 && (string)ps[0] == "00" && (string)ps[1] == "P1");
            sb.Length = 0;
            ps.Clear();
            p.Codes[PermObj.Item] = Set();
            ReportsMgmtPnlSql.ItemRows(sb, ps, p);
            Expect("pnl item empty set", sb.ToString().EndsWith(" OR 1=0)", StringComparison.Ordinal) && ps.Count == 0);
            sb.Length = 0;
            ReportsMgmtPnlSql.ItemRows(sb, ps, Ctx());
            ReportsMgmtPnlSql.ItemRows(sb, ps, null);
            Expect("pnl item off", sb.Length == 0 && ps.Count == 0);
            string src = ReportsMgmtPnlSql.Query(PnlArgs(), 2024, ps, null);
            Expect("pnl item query off", Count(src, '?') == ps.Count && !src.Contains("pv(c, k)"));
        }

        static MgmtGlArgs PnlArgs()
        {
            MgmtGlArgs m = new MgmtGlArgs();
            m.From = 1;
            m.To = 3;
            m.Detail = "leaf";
            m.ProfitAccount = "4103";
            m.PlAccounts = new string[] { "6" };
            m.ByItem = true;
            return m;
        }

        // mgmt/meta 的 perm_fingerprint 与 perm/snapshot 的 fingerprint 同一算法：同一快照两边相等，权限一变就变。
        static void CheckMetaPerm()
        {
            PermContext p = Dept("D01");
            p.Funcs.Add("GL1512");
            Dictionary<string, object> body = new Dictionary<string, object>();
            ReportsMgmtMeta.AddPerm(body, p);
            string fp = body["perm_fingerprint"] as string;
            Expect("meta perm same as snapshot", fp != null && fp.Length == 64 && fp == (string)PermSnapshot.ToJson(p)["fingerprint"]);
            p.Codes[PermObj.Department].Add("D02");
            ReportsMgmtMeta.AddPerm(body, p);
            string widened = (string)body["perm_fingerprint"];
            Expect("meta perm data change", widened != fp && widened == (string)PermSnapshot.ToJson(p)["fingerprint"]);
            p.Funcs.Remove("GL1512");
            ReportsMgmtMeta.AddPerm(body, p);
            Expect("meta perm func change", (string)body["perm_fingerprint"] != widened);
        }

        static void CheckFa()
        {
            ReportArgs a = new ReportArgs();
            a.Name = ReportsFaReq.ChangesName;
            a.FiscalYear = 2026;
            a.Limit = 10;
            FaReportArgs f = new FaReportArgs();
            List<object> ps = new List<object>();
            string sql = ReportsFa.ChangesQuery(a, f, ps, Dept("D01"));
            Expect("fa changes args", Count(sql, '?') == ps.Count && ps.Count == 4 && (string)ps[2] == "D01" && (string)ps[3] == "D01");
            Expect("fa changes card", sql.Contains(" AND EXISTS (SELECT 1 FROM fa_DeptScale fs WHERE fs.sCardNum = v.sCardNum)")
                && sql.Contains("NOT EXISTS (SELECT 1 FROM fa_DeptScale fd WHERE fd.sCardNum = v.sCardNum AND NOT ("));
            Expect("fa changes detail", sql.Contains("NOT EXISTS (SELECT 1 FROM fa_Vouchers_Detail fv WHERE fv.sNum = v.sNum AND NOT ((NULLIF(")
                && sql.EndsWith(" ORDER BY v.sNum", StringComparison.Ordinal));
            CheckFaDepr(a, f);
            CheckFaCard();
        }

        // 固定资产卡片档案：单张读取的探测语句与报表同一口径；不受控时不探。
        static void CheckFaCard()
        {
            List<object> ps = new List<object>();
            string sql = ReportsFaPerm.CardProbe(Dept("D01"), "00021", ps);
            Expect("fa card probe args", sql != null && Count(sql, '?') == ps.Count && ps.Count == 2 && (string)ps[0] == "00021"
                && (string)ps[1] == "D01");
            Expect("fa card probe sql", sql.StartsWith("SELECT TOP 1 1 AS x FROM fa_Cards h WHERE h.sCardNum = ?", StringComparison.Ordinal)
                && sql.Contains("fs.sCardNum = h.sCardNum") && sql.Contains("fd.sCardNum = h.sCardNum AND NOT ("));
            Expect("fa card probe off", ReportsFaPerm.CardProbe(Ctx(), "00021", new List<object>()) == null
                && ReportsFaPerm.CardProbe(null, "00021", new List<object>()) == null);
            PermContext admin = Dept();
            admin.Supervisor = true;
            Expect("fa card probe supervisor", ReportsFaPerm.CardProbe(admin, "00021", new List<object>()) == null);
            PermContext dataAdmin = Dept();
            dataAdmin.DataAdmin.Add(PermObj.Department);
            Expect("fa card probe data admin", ReportsFaPerm.CardProbe(dataAdmin, "00021", new List<object>()) == null);
            List<object> ep = new List<object>();
            Expect("fa card probe empty set", ReportsFaPerm.CardProbe(Dept(), "00021", ep).EndsWith("AND NOT (1=0))", StringComparison.Ordinal)
                && ep.Count == 1);
        }

        static void CheckFaDepr(ReportArgs a, FaReportArgs f)
        {
            a.Name = ReportsFaReq.DeprName;
            List<object> dp = new List<object>();
            string dsql = ReportsFa.DeprQuery(a, f, dp, Dept("D01"));
            Expect("fa depr args", Count(dsql, '?') == dp.Count && dp.Count == 3 && (string)dp[2] == "D01");
            Expect("fa depr dept", dsql.Contains("fd.sCardNum = t.sCardNum")
                && dsql.EndsWith(" ORDER BY t.sCardNum, l.iPeriod", StringComparison.Ordinal));
            List<object> none = new List<object>();
            Expect("fa dept off", !ReportsFa.DeprQuery(a, f, none, Ctx()).Contains("fa_DeptScale") && none.Count == 2);
            List<object> ep = new List<object>();
            Expect("fa dept empty set", ReportsFa.DeprQuery(a, f, ep, Dept()).Contains("AND NOT (1=0))") && ep.Count == 2);
        }

        // 部门开关打开，只授给 codes。
        static PermContext Dept(params string[] codes)
        {
            PermContext p = Ctx();
            p.On.Add(PermObj.Department);
            p.Codes[PermObj.Department] = Set(codes);
            return p;
        }

        static void CheckIdem()
        {
            Expect("idem same op", IdemGet.Allowed("op001", " OP001 ", false));
            Expect("idem other op", !IdemGet.Allowed("op001", "zhangsan", true));
            Expect("idem blank caller", !IdemGet.Allowed("op001", " ", false) && !IdemGet.Allowed("op001", null, false));
            Expect("idem legacy denied", !IdemGet.Allowed("", "op001", false) && !IdemGet.Allowed(null, "op001", false)
                && !IdemGet.Allowed("  ", "op001", false));
            Expect("idem legacy supervisor", IdemGet.Allowed("", "op001", true));
        }

        static PermContext Ctx()
        {
            PermContext p = new PermContext();
            p.Acc = "999";
            p.Operator = "U1";
            p.Year = 2024;
            p.DateYear = 2024;
            p.AcctYear = 2024;
            return p;
        }

        // 科目开关打开、总账选项 bQryCtlSubj 打开，只授给 codes。
        static PermContext Subj(params string[] codes)
        {
            PermContext p = Ctx();
            p.On.Add(PermObj.Account);
            p.GlSubjCtl = true;
            p.Codes[PermObj.Account] = Set(codes);
            return p;
        }

        static HashSet<string> Set(params string[] codes)
        {
            return new HashSet<string>(codes, StringComparer.OrdinalIgnoreCase);
        }

        static List<object> Lines(params string[] codes)
        {
            List<object> lines = new List<object>();
            for (int i = 0; i < codes.Length; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["ccode"] = codes[i];
                lines.Add(row);
            }
            return lines;
        }

        static void Denied(string name, PermContext p, PermRule rule, List<object> lines)
        {
            try
            {
                PermCheck.CheckVoucher(p, rule, null, lines);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 403);
                return;
            }
            throw new InvalidOperationException("read-leak " + name);
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

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("read-leak " + name);
            }
        }
    }
}
