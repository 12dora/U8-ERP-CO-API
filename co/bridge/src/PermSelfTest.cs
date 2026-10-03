using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的权限部分：只测纯逻辑（登记表覆盖、功能 id 判断、数据权限判断、SQL 条件与参数顺序），不连库。
    internal static class PermSelfTest
    {
        public static void Run()
        {
            CheckRegistry();
            CheckFuncs();
            CheckIds();
            CheckAllow();
            CheckVoucher();
            CheckSql();
            CheckHold();
            CheckCacheKey();
            CheckHooks();
            CheckPair();
            // 权限快照、权限评估（PermSnapshotSelfTest）。
            PermSnapshotSelfTest.Run();
            // 字段权限（PermColumnSelfTest）。
            PermColumnSelfTest.Run();
            // 读权限收紧：凭证按科目整张过滤、水位不给金额、按部门过滤、幂等查询按操作员（ReadLeakSelfTest）。
            ReadLeakSelfTest.Run();
        }

        // 两列主键档案的编码拆分：按第一个冒号拆成两段，缺段或超长 400。
        static void CheckPair()
        {
            ArcPair pair = ArcPair.Of(ArcKind.Find("customer_inventory"));
            string[] parts = pair.Split("C01:05:A", "code");
            Expect("pair split", parts[0] == "C01" && parts[1] == "05:A");
            Expect("pair single", ArcPair.Of(ArcKind.Find("position")) == null);
            ExpectBad("pair no colon", pair, "C01");
            ExpectBad("pair empty second", pair, "C01:");
        }

        static void ExpectBad(string name, ArcPair pair, string code)
        {
            try
            {
                pair.Split(code, "code");
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException("perm " + name);
        }

        // 数据权限挂钩：单张读取的探测 SQL（参数顺序）、档案编码判断。
        static void CheckHooks()
        {
            PermContext p = Controlled();
            List<object> ps = new List<object>();
            string sql = PermHook.ProbeSql(Kinds.Find("transfer"), 7, p, PermRegistry.ForKey("voucher:transfer"), ps);
            Expect("probe head", sql.StartsWith("SELECT TOP 1 1 AS x FROM TransVouch h WHERE h.ID=? AND h.cOWhCode IN (?)"
                + " AND h.cIWhCode IN (?) AND (NOT EXISTS (SELECT 1 FROM TransVouchs pd WHERE pd.ID=h.ID)", StringComparison.Ordinal));
            Expect("probe args", ps.Count == 3 && (int)ps[0] == 7 && (string)ps[1] == "01" && (string)ps[2] == "01");
            Expect("probe body empty set", sql.EndsWith(" AND 1=0))", StringComparison.Ordinal));
            PermRule project = PermRegistry.ForKey("archive:project");
            Expect("archive item", PermHook.ArchiveOk(null, p, project, "00:P1") && !PermHook.ArchiveOk(null, p, project, "01:P1"));
            PermRule wh = PermRegistry.ForKey("archive:warehouse");
            Expect("archive code", PermHook.ArchiveOk(null, p, wh, "01") && !PermHook.ArchiveOk(null, p, wh, "02"));
            Expect("archive off", PermHook.ArchiveOk(null, p, PermRegistry.ForKey("archive:customer"), "C9"));
        }

        // 每个单据类型、档案、报表都有规则；写路由不算读路由。
        static void CheckRegistry()
        {
            List<string> missing = MissingRules();
            Expect("rule missing: " + string.Join(", ", missing.ToArray()), missing.Count == 0);
            Expect("rule gl", PermRegistry.ForKey("gl") != null && PermRegistry.ForKey("stock") != null);
            // 凭证查询按科目过滤（整张凭证，PermObj.Gl）；科目档案仍不按科目过滤。
            PermObj[] gl = PermRegistry.ForKey("gl").Objs;
            Expect("gl account filter", gl.Length == 1 && gl[0].Mode == PermObj.GlLines && gl[0].Obj == PermObj.Account
                && PermRegistry.ForKey("archive:account").Objs.Length == 0);
            Expect("read list", PermRegistry.IsRead(Requests.ListPath) && PermRegistry.IsRead(Requests.GlRoot + "load"));
            Expect("write skipped", !PermRegistry.IsRead("/u8co/v1/vouchers/create") && !PermRegistry.IsRead(Requests.GlRoot + "create"));
            Expect("login skipped", !PermRegistry.IsRead("/u8co/v1/login-check") && !PermRegistry.IsRead("/u8co/v1/meta"));
        }

        // 没有规则的单据类型、档案、报表，一次全部列出。
        static List<string> MissingRules()
        {
            List<string> missing = new List<string>();
            foreach (VoucherKind kind in Kinds.All())
            {
                Need(missing, "voucher:" + kind.Name);
            }
            foreach (ArcKind arc in ArcKind.List())
            {
                Need(missing, "archive:" + arc.Name);
            }
            for (int i = 0; i < Reports.Names.Length; i++)
            {
                string name = Reports.Names[i];
                if (PermRegistry.ForKey("report:" + name) == null)
                {
                    Need(missing, "report:" + name + ":ar");
                    Need(missing, "report:" + name + ":ap");
                }
            }
            return missing;
        }

        static void Need(List<string> missing, string key)
        {
            if (PermRegistry.ForKey(key) == null)
            {
                missing.Add(key);
            }
        }

        static void CheckFuncs()
        {
            PermContext p = Ctx();
            p.Funcs.Add("GL0202");
            Expect("has exact", p.Has("gl0202") && !p.Has("GL02"));
            Expect("has prefix", p.Has("GL%") && !p.Has("SA%"));
            Expect("has any", p.HasAny(new string[] { "SA03010104", "GL0202" }) && !p.HasAny(new string[] { "SA03010104" }));
            Expect("empty auths", p.HasAny(new string[0]));
            p.Supervisor = true;
            Expect("supervisor", p.Has("SA03010104") && !p.Controls(PermObj.Warehouse));
        }

        // 核对过的功能 id：持有正确的 id 放行，持有原先误登记的 id（补料申请单、作废、付款条件等）拒绝。
        static void CheckIds()
        {
            Pins("mo close", MoClose.CloseRule, "MO03001CLS", "MO02009CLOSE");
            Pins("mo open", MoClose.OpenRule, "MO03001UNC", "MO03009OPEN");
            Pins("mo create", MoCreate.CreateRule, "MO02001N", "MO03009N");
            Pins("mo delete", MoDelete.DeleteRule, "MO03001D", "MO02009D");
            Pins("mo update", MoUpdate.UpdateRule, "MO02001M", "MO03009M");
            Pins("sale invoice", "voucher:sale_invoice", "SA03030202", "SA03030204");
            Pins("stock opening", "report:opening_balance:stock", "ST000101", "ST0102");
            Pins("stock opening voucher", "voucher:stock_opening", "ST000101", "ST0102");
            Pins("position", "archive:position", "AS030Q", "AS014Q");
            Pins("customer inventory", "archive:customer_inventory", "AS1204Q", "AS1204");
            CheckAuthCatalogIds();
            // admin 只认 Supervisor：Funcs 里有 admin 行（如某年收回的主管）不算。
            PermRule bank = PermRegistry.ForKey("archive:aa_bank");
            PermContext stale = Ctx();
            stale.Funcs.Add("Admin");
            PermContext sup = Ctx();
            sup.Supervisor = true;
            Expect("ids admin", !stale.Has("admin") && !stale.HasAny(bank.Auths) && sup.HasAny(bank.Auths));
        }

        // 按 U8 授权目录（UA_Auth 名称）更正的 id：持有正确的 id 放行，持有原先误登记的 id（远程发送、成套件、收发类别查询、
        // 收款单删除 / 录入、我的账表、凭证整理、发运方式、明细账等）拒绝。
        static void CheckAuthCatalogIds()
        {
            Pins("ar bill", "voucher:ar_bill", "AR21101", "AR010301");
            Pins("ap bill", "voucher:ap_bill", "AP0601021", "AP010301");
            Pins("position write", ArcGuard.RuleKey("position", "update"), "AS030", "AS014");
            Pins("bank", "archive:bank", "AS013Q", "AS016Q");
            Pins("bank write", ArcGuard.RuleKey("bank", "create"), "AS013", "AS016Q");
            Pins("writeoff", PermRegistry.WriteoffKey("AR"), "AR050201", "AR050203");
            Pins("writeoff auto", PermRegistry.WriteoffAutoKey("AP"), "AP050202", "AP0503");
            Pins("writeoff cancel", PermRegistry.WriteoffCancelKey("AR"), "AR0807", "AR050203");
            Pins("process cancel", PermRegistry.ProcCancelKey("AP"), "AP0807", "AP0502");
            Pins("exgain cancel", PermRegistry.ExGainKey("AR", true), "AR0807", "AR050203");
            Pins("writeoff list", "report:arap_writeoffs:ar", "AR060107", "AR0601");
            Pins("customer contact", ArcGuard.RuleKey("customer_contact", "create"), "CS020204", "AS011");
            Pins("close status", "report:close_status", "GL1512", "GL0202");
            Pins("trade class", "archive:trade_class", "AS050Q", "AS013Q");
            Pins("rd style", "archive:rd_style", "AS016Q", "AS006Q");
            Pins("rd style write", ArcGuard.RuleKey("rd_style", "delete"), "AS016", "AS006");
            Pins("ar aging", "report:arap_aging:ar", "AR060301", "AR060202_01");
            Pins("ar note discount", PermRegistry.NotesProcKey("AR", "discount"), "AR240205", "AR240206");
            Pins("ap note settle", PermRegistry.NotesProcKey("AP", "settle"), "AP240203", "AP240205");
            Pins("ap note return", PermRegistry.NotesProcKey("AP", "return"), "AP240202", "AP240205");
            Pins("writeoff list pick", "report:arap_writeoffs:ap", "AP0503", "AP0601");
            Pins("process list pick", ArapProcListReq.KeyOf("AR"), "AR0503", "AR0601");
            Pins("qm other list", "voucher:qm_other_inspect", "QM030601", "QM02010101");
            Pins("qm other check list", "voucher:qm_other_check", "QM030603", "QM02010201");
        }

        static void Pins(string name, string key, string good, string bad)
        {
            PermRule rule = PermRegistry.ForKey(key);
            PermContext yes = Ctx();
            yes.Funcs.Add(good);
            PermContext no = Ctx();
            no.Funcs.Add(bad);
            Expect("ids " + name, rule != null && yes.HasAny(rule.Auths) && !no.HasAny(rule.Auths));
        }

        static void CheckAllow()
        {
            PermContext p = Controlled();
            Expect("switch off", p.Allow(PermObj.Customer, "C9"));
            Expect("allowed", p.Allow(PermObj.Warehouse, " 01 ") && !p.Allow(PermObj.Warehouse, "02"));
            Expect("blank denied", !p.Allow(PermObj.Warehouse, " "));
            Expect("empty set denies", !p.Allow(PermObj.Inventory, "A"));
            Expect("item pair", p.AllowItem("00", "P1") && !p.AllowItem("01", "P1"));
            p.DataAdmin.Add(PermObj.Warehouse);
            Expect("data admin", p.Allow(PermObj.Warehouse, "02"));
        }

        static void CheckVoucher()
        {
            PermContext p = Controlled();
            p.On.Remove(PermObj.Inventory);
            PermRule rule = new PermRule("t", "测试", new string[0], new PermObj[]
            {
                PermObj.H(PermObj.Warehouse, "cWhCode"),
                PermObj.B(PermObj.Account, "L", "ID", "ID", "ccode")
            });
            p.On.Add(PermObj.Account);
            p.Codes[PermObj.Account] = Set("1001");
            Expect("account needs gl option", !p.Controls(PermObj.Account));
            p.GlSubjCtl = true;
            Expect("account gl option", p.Controls(PermObj.Account));
            List<object> lines = new List<object> { Row("ccode", "2202"), Row("ccode", "1001") };
            PermCheck.CheckVoucher(p, rule, Row("cwhcode", "01"), lines);
            PermCheck.CheckVoucher(p, rule, Row("cWhCode", "01"), new List<object>());
            ExpectDenied("head denied", p, rule, Row("cWhCode", "02"), lines);
            ExpectDenied("head blank", p, rule, Row("cWhCode", ""), lines);
            ExpectDenied("no line", p, rule, Row("cWhCode", "01"), new List<object> { Row("ccode", "2202") });
            PermObj opt = PermObj.Opt(PermObj.Warehouse, "cWhCode");
            Expect("optional blank", PermCheck.RowOk(p, opt, Row("cWhCode", "")) && !PermCheck.RowOk(p, opt, Row("cWhCode", "02")));
        }

        static void CheckSql()
        {
            PermContext p = Controlled();
            StringBuilder sb = new StringBuilder();
            List<object> ps = new List<object>();
            PermSql.AppendCode(sb, ps, p, PermObj.Customer, "h.cCusCode", false);
            Expect("sql off", sb.Length == 0 && ps.Count == 0);
            PermSql.AppendCode(sb, ps, p, PermObj.Warehouse, "h.cWhCode", false);
            Expect("sql in", sb.ToString() == " AND h.cWhCode IN (?)" && ps.Count == 1 && (string)ps[0] == "01");
            sb.Length = 0;
            PermSql.AppendCode(sb, ps, p, PermObj.Inventory, "h.cInvCode", true);
            Expect("sql empty", sb.ToString() == " AND (NULLIF(LTRIM(RTRIM(h.cInvCode)), N'') IS NULL OR 1=0)");
            sb.Length = 0;
            ps.Clear();
            PermSql.AppendBody(sb, ps, p, PermObj.B(PermObj.Warehouse, "DispatchLists", "DLID", "DLID", "cWhCode"), "h.DLID");
            Expect("sql body", sb.ToString().StartsWith(" AND (NOT EXISTS (SELECT 1 FROM DispatchLists pd WHERE pd.DLID=h.DLID)",
                StringComparison.Ordinal) && ps.Count == 1);
        }

        // 授权超过 MaxInline 个：改用 AA_HoldAuth 实时条件，角色按参数传。
        static void CheckHold()
        {
            PermContext p = Controlled();
            StringBuilder sb = new StringBuilder();
            List<object> ps = new List<object>();
            HashSet<string> many = Set();
            for (int i = 0; i <= PermSql.MaxInline; i++)
            {
                many.Add("W" + i.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            p.Codes[PermObj.Warehouse] = many;
            PermSql.AppendCode(sb, ps, p, PermObj.Warehouse, "h.cWhCode", false);
            Expect("sql hold", ps.Count == 2 && (string)ps[0] == PermObj.Warehouse && (string)ps[1] == "U1"
                && sb.ToString().EndsWith("AND ((pa.isUserGroup=0 AND pa.cUserId=?)) AND pa.cACCode=h.cWhCode)", StringComparison.Ordinal));
            sb.Length = 0;
            ps.Clear();
            p.Roles.Add("R1");
            PermSql.AppendCode(sb, ps, p, PermObj.Warehouse, "h.cWhCode", false);
            Expect("sql hold roles", ps.Count == 3 && (string)ps[2] == "R1"
                && sb.ToString().Contains(" OR (pa.isUserGroup=1 AND pa.cUserId IN (?)))"));
        }

        static void CheckCacheKey()
        {
            Expect("cache key case", PermCache.KeyOf("999", 2026, 2026, "op001") == PermCache.KeyOf("999", 2026, 2026, "OP001"));
            Expect("cache key year", PermCache.KeyOf("999", 2026, 2026, "op001") != PermCache.KeyOf("999", 2025, 2026, "op001"));
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

        // 仓库、存货、项目的开关打开：仓库只给 01，存货一个都没给，项目给 00/P1。
        static PermContext Controlled()
        {
            PermContext p = Ctx();
            p.On.Add(PermObj.Warehouse);
            p.On.Add(PermObj.Inventory);
            p.On.Add(PermObj.Item);
            p.Codes[PermObj.Warehouse] = Set("01");
            p.Codes[PermObj.Item] = Set("00" + PermContext.PairSep + "P1");
            return p;
        }

        static HashSet<string> Set(params string[] codes)
        {
            HashSet<string> set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < codes.Length; i++)
            {
                set.Add(codes[i]);
            }
            return set;
        }

        static Dictionary<string, object> Row(string key, string value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row[key] = value;
            return row;
        }

        static void ExpectDenied(string name, PermContext p, PermRule rule, Dictionary<string, object> head, List<object> lines)
        {
            try
            {
                PermCheck.CheckVoucher(p, rule, head, lines);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 403);
                return;
            }
            throw new InvalidOperationException("perm " + name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("perm " + name);
            }
        }
    }
}
