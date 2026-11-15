using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的月末结账部分（periods/close）：请求校验、测试账套名单、顺序闸门、through 的步骤、登记
    // （写闸门、幂等、预演、锁键、权限）。只跑纯函数，不连库、不建 COM。
    internal static class PeriodCloseSelfTest
    {
        static readonly bool[] AllStarted = new bool[] { true, true, true, true, true, true, true };

        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckTestAccounts();
            CheckGate();
            CheckOrder();
            CheckPlan();
            CheckFacts();
            CheckWiring();
            CheckStockSql();
            CheckStockKeys();
            CheckStockText();
            CheckStockBody();
            CheckIaText();
            CheckIaBody();
        }

        static void CheckParse()
        {
            PeriodAsk ask = PeriodCloseReq.Parse(Body("gl", 2026, 9, "close"));
            Expect("period parse close", Text(ask) == "6 2026 9 close False");
            Expect("period sub", PeriodCloseReq.SubOf(ask) == "GL");
            Expect("period audit close", PeriodCloseReq.AuditAction(ask) == PeriodCloseReq.Action);
            ask = PeriodCloseReq.Parse(Body("pu", 2026, 1, "reopen"));
            Expect("period parse reopen", Text(ask) == "0 2026 1 reopen False");
            Expect("period audit reopen", PeriodCloseReq.AuditAction(ask) == PeriodCloseReq.UndoAction);
            Expect("period sub pu", PeriodCloseReq.SubOf(ask) == "PU");
            Dictionary<string, object> through = Body(null, 2026, 8, "close");
            through["through"] = true;
            ask = PeriodCloseReq.Parse(through);
            Expect("period parse through", Text(ask) == "-1 2026 8 close True");
            Expect("period sub through", PeriodCloseReq.SubOf(ask) == "GL");
            through["module"] = "sa";
            Expect("period through ignores module", PeriodCloseReq.Parse(through).Mod == -1);
            Expect("period rule gl reopen", PeriodCloseReq.RuleKey(PeriodModules.Gl, false) == "write:period:gl:reopen");
            Expect("period rule pu reopen", PeriodCloseReq.RuleKey(0, false) == "write:period:pu:close");
            Expect("period rule ap close", PeriodCloseReq.RuleKey(5, true) == "write:period:ap:close");
        }

        static void CheckBad()
        {
            Bad("period module", Body("xx", 2026, 9, "close"), "module");
            Bad("period module case", Body("GL", 2026, 9, "close"), "module");
            Bad("period module missing", Body(null, 2026, 9, "close"), "module");
            Bad("period year", Body("gl", 26, 9, "close"), "fiscal_year");
            Bad("period zero", Body("gl", 2026, 0, "close"), "period");
            Bad("period thirteen", Body("gl", 2026, 13, "close"), "period");
            Bad("period action", Body("gl", 2026, 9, "post"), "action");
            Dictionary<string, object> text = Body("gl", 2026, 9, "close");
            text["period"] = "9";
            Bad("period text", text, "period");
            Dictionary<string, object> reopen = Body(null, 2026, 9, "reopen");
            reopen["through"] = true;
            Bad("period through reopen", reopen, "through");
            Dictionary<string, object> flag = Body("gl", 2026, 9, "close");
            flag["through"] = "yes";
            Bad("period through text", flag, "through");
        }

        static void CheckTestAccounts()
        {
            TestAccountGateSelfTest.CheckGate("period", PeriodCloseReq.TestOnly, PeriodCloseReq.RequireTestAccount);
            BridgeConfig cfg = new BridgeConfig();
            cfg.TestAccounts = new string[] { "998" };
            Expect("period test listed cfg", BridgeConfig.IsTestAccount(cfg, "998"));
            Expect("period test null cfg", !BridgeConfig.IsTestAccount(null, "998"));
            ConfigRules.CheckTestAccounts(new string[] { "998" });
            try
            {
                ConfigRules.CheckTestAccounts(new string[] { "98" });
            }
            catch (InvalidOperationException)
            {
                return;
            }
            throw new InvalidOperationException("period test accounts bad code");
        }

        static void CheckGate()
        {
            // 2025 全年已结账；2026 年 1–8 期已结账，9–12 期未结账（各模块相同）。
            PeriodMend m = Mend(2025, 12, 2026, 8);
            Expect("period close first open", PeriodGate.Close(m, 6, 2026, 9) == null);
            Expect("period close done", PeriodGate.Close(m, 6, 2026, 8) == "该期间已结账");
            Expect("period close later", PeriodGate.Close(m, 6, 2026, 10).StartsWith("上一期间还没结账", StringComparison.Ordinal));
            Expect("period missing", PeriodGate.Missing(m, 2027, 1) != null);
            Expect("period present", PeriodGate.Missing(m, 2026, 12) == null);
            Expect("period reopen last", PeriodGate.Reopen(m, 6, 2026, 8) == null);
            Expect("period reopen earlier", PeriodGate.Reopen(m, 6, 2026, 7) != null);
            Expect("period reopen open", PeriodGate.Reopen(m, 6, 2026, 9) == "该期间还没结账");
            // 上年 12 期未结账时，本年 1 期不能结账。
            m = Mend(2025, 11, 0, 0);
            Expect("period close prior year", PeriodGate.Close(m, 0, 2026, 1).IndexOf("2025 年 12 期", StringComparison.Ordinal) > 0);
            Expect("period reopen across year", PeriodGate.Reopen(m, 0, 2025, 11) == null);
        }

        static void CheckOrder()
        {
            PeriodMend m = Mend(2025, 12, 2026, 8);
            m.Set(0, 2026, 9, true);
            Expect("period gl needs others", PeriodGate.Order(m, 6, 2026, 9, AllStarted, true) != null);
            bool[] glOnly = new bool[] { false, false, false, false, false, false, true };
            Expect("period gl alone", PeriodGate.Order(m, 6, 2026, 9, glOnly, true) == null);
            Expect("period ap after pu", PeriodGate.Order(m, 5, 2026, 9, AllStarted, true) == null);
            Expect("period ar needs sa", PeriodGate.Order(m, 4, 2026, 9, AllStarted, true) != null);
            // 采购取消结账：同期库存、存货、应付或总账已结账时拒绝。
            Expect("period pu reopen blocked", PeriodGate.Order(m, 0, 2026, 8, AllStarted, false) != null);
            Expect("period gl reopen free", PeriodGate.Order(m, 6, 2026, 8, AllStarted, false) == null);
            List<int> deps = PeriodModules.Dependents(0);
            Expect("period pu dependents", Join(deps.ToArray()) == "2,3,5,6");
            List<string> subs = new List<string>(new string[] { "GL", "PU", "QM" });
            bool[] started = PeriodModules.StartedOf(subs);
            Expect("period started pu", started[0]);
            Expect("period started gl", started[6]);
            Expect("period not started sa", !started[1]);
        }

        static void CheckPlan()
        {
            // 2025–2026 年各期都未结账（空账套）：从 2025 年 1 期结到 2026 年 2 期是 14 期。
            PeriodMend m = Mend(0, 0, 0, 0);
            List<int[]> open = m.OpenUpTo(6, 2026, 2);
            Expect("period plan count", open.Count == 14);
            Expect("period plan first", open[0][0] * 100 + open[0][1] == 202501);
            Expect("period plan last", open[13][0] * 100 + open[13][1] == 202602);
            PeriodRun run = new PeriodRun();
            run.Ask = new PeriodAsk();
            run.Ask.Close = true;
            run.Steps.Add(new int[] { 6, 2025, 1 });
            Expect("period mismatch", PeriodClose.Mismatch(m, run) != null);
            m.Set(6, 2025, 1, true);
            Expect("period match", PeriodClose.Mismatch(m, run) == null);
            run.Ask.Through = true;
            Dictionary<string, object> body = PeriodClose.Body(run);
            Expect("period through body", (int)body["count"] == 1 && body.ContainsKey("through") && !body.ContainsKey("closed"));
            Expect("period day", PeriodCloseChecks.Day(2026, 12) == "2026-12-01");
            Expect("period next day", PeriodCloseChecks.NextDay(2026, 12) == "2027-01-01");
        }

        // 采购 / 存货期初记账、总账前的固定资产 / 薪资 / 成本、上下月。
        static void CheckFacts()
        {
            Expect("period shift back", Join(PeriodCloseChecks.Shift(2026, 1, -1)) == "2025,12");
            Expect("period shift next", Join(PeriodCloseChecks.Shift(2026, 12, 1)) == "2027,1");
            List<string[]> extras = PeriodModules.ExtrasOf(new List<string>(new string[] { "GL", "CA", "FA", "QM" }));
            Expect("period extras", extras.Count == 2 && extras[0][0] == "bflag_FA" && extras[1][0] == "bflag_CA");
            Expect("period extras none", PeriodModules.ExtrasOf(new List<string>(new string[] { "GL" })).Count == 0);
            PeriodRun run = new PeriodRun();
            run.Ask = new PeriodAsk();
            run.Ask.Close = true;
            run.Ask.Mod = PeriodModules.Ia;
            run.PuPosted = false;
            run.IaPosted = false;
            string pu = PeriodCloseFacts.Gate(run, new int[] { PeriodModules.Pu, 2026, 1 });
            Expect("period pu opening", pu != null && pu.IndexOf("openings/post", StringComparison.Ordinal) >= 0);
            Expect("period sa no opening", PeriodCloseFacts.Gate(run, new int[] { 1, 2026, 1 }) == null);
            Expect("period ia opening warns", PeriodCloseFacts.Gate(run, new int[] { PeriodModules.Ia, 2026, 1 }) == null);
            PeriodCloseFacts.Gate(run, new int[] { PeriodModules.Ia, 2026, 2 });
            Expect("period ia warning once", run.Warnings.Count == 1
                && run.Warnings[0].StartsWith("ia_opening_not_posted", StringComparison.Ordinal));
            Expect("period body warnings", PeriodClose.Body(run).ContainsKey("warnings"));
            run.Ask.Close = false;
            Expect("period reopen ignores opening", PeriodCloseFacts.Gate(run, new int[] { PeriodModules.Pu, 2026, 1 }) == null);
            PeriodRun plain = new PeriodRun();
            plain.Ask = run.Ask;
            Expect("period body no warnings", !PeriodClose.Body(plain).ContainsKey("warnings"));
            Expect("period module consts", PeriodModules.Code(PeriodModules.Pu) == "pu" && PeriodModules.Code(PeriodModules.St) == "st"
                && PeriodModules.Code(PeriodModules.Ia) == "ia");
        }

        static void CheckWiring()
        {
            string path = PeriodCloseReq.Path;
            Expect("period write", WriteGate.IsWrite(path) && IdemReq.Supports(path) && DryRunReq.Accepts(path));
            Expect("period dry mode", DryRunModes.Lookup(path, "", PeriodCloseReq.Action, "") == DryRunModes.Rollback);
            Expect("period meta dry", (string)MetaDryRun.Routes()["periods/close"] == DryRunModes.Rollback);
            for (int m = 0; m < PeriodModules.Count; m++)
            {
                Expect("period perm " + PeriodModules.Code(m), PermRegistry.ForKey(PeriodCloseReq.RuleKey(m, true)) != null);
            }
            Expect("period perm reopen", PermRegistry.ForKey(PeriodCloseReq.RuleKey(PeriodModules.Gl, false)) != null);
            Expect("period not read", !PermRegistry.IsRead(path));
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = path;
            item.Body = Body("gl", 2026, 9, "close");
            string[] keys = DocLocks.KeysOf(item);
            Expect("period locks", Array.IndexOf(keys, "period:gl") >= 0 && Array.IndexOf(keys, WriteGate.Key) >= 0);
            item.Body["through"] = true;
            keys = DocLocks.KeysOf(item);
            Expect("period through locks", string.Join(",", keys).StartsWith("period:pu,period:sa,", StringComparison.Ordinal));
            Expect("period not sql read", !RouteClass.IsSqlRead(item));
        }

        // 库存月结：SQL 文本的关键条件（七种单据、假退料、三个期初标志、只取上月结存）、表名与参数。
        static void CheckStockSrc()
        {
            Expect("stock types", string.Join(",", PeriodStockSql.Types) == "01,08,09,10,11,32,34");
            string first = "ISNULL(a.bomfirst,0)=0 AND ISNULL(a.bpufirst,0)=0 AND ISNULL(a.biafirst,0)=0";
            foreach (string type in PeriodStockSql.Types)
            {
                string sql = PeriodStockSql.SrcFill(type);
                Expect("stock src " + type, sql.IndexOf("FROM rdrecord" + type + " a INNER JOIN rdrecords" + type + " b", StringComparison.Ordinal) > 0
                    && sql.IndexOf(first, StringComparison.Ordinal) > 0 && sql.IndexOf("FROM #st_args", StringComparison.Ordinal) > 0);
                Expect("stock fake return " + type, (sql.IndexOf("假退料", StringComparison.Ordinal) > 0) == (type == "11"));
            }
            Expect("stock src bad type", Throws(delegate { PeriodStockSql.SrcFill("12"); }));
        }

        static void CheckStockSql()
        {
            CheckStockSrc();
            string account = PeriodStockSql.Pass(false);
            string audited = PeriodStockSql.Pass(true);
            Expect("stock pass account", account.IndexOf("FROM ST_MonthAccounts WHERE iYear=@py AND iMonth=@pm", StringComparison.Ordinal) > 0
                && account.IndexOf(PeriodStockSql.AccountFilter, StringComparison.Ordinal) > 0 && account.IndexOf("#expAs", StringComparison.Ordinal) > 0);
            Expect("stock pass audited", audited.IndexOf("FROM ST_MonthAccountVs WHERE iYear=@py AND iMonth=@pm", StringComparison.Ordinal) > 0
                && audited.IndexOf(PeriodStockSql.AuditedFilter, StringComparison.Ordinal) > 0 && audited.IndexOf("#expVs", StringComparison.Ordinal) > 0);
            Expect("stock pass round", account.IndexOf("ROUND(sgn_qty,@qdec)", StringComparison.Ordinal) > 0
                && account.IndexOf("ROUND(sgn_num,@ndec)", StringComparison.Ordinal) > 0);
            Expect("stock check prev", PeriodStockSql.CheckPass.IndexOf("FROM ST_MonthAccountCheck WHERE iYear=@py AND iMonth=@pm",
                StringComparison.Ordinal) > 0);
            foreach (string table in PeriodStockSql.Tables)
            {
                Expect("stock write " + table, PeriodStockSql.Write.IndexOf("INSERT INTO " + table + " (", StringComparison.Ordinal) > 0
                    && PeriodStockSql.Write.IndexOf("DELETE FROM " + table + " WHERE", StringComparison.Ordinal) > 0);
                Expect("stock delete " + table, PeriodStockSql.DeleteOf(table).StartsWith("DELETE FROM " + table + " ", StringComparison.Ordinal));
            }
            Expect("stock delete bad", Throws(delegate { PeriodStockSql.DeleteOf("GL_mend"); }));
            Expect("stock count args", PeriodStockSql.CountArgs(2026, 8).Length == 10);
            Expect("stock prev month", Join(PeriodCloseChecks.Shift(2026, 1, -1)) == "2025,12");
            Expect("stock last day", PeriodStock.LastDay(2026, 2) == "2026-02-28" && PeriodStock.LastDay(2024, 2) == "2024-02-29"
                && PeriodStock.LastDay(2026, 12) == "2026-12-31");
            Expect("stock drop temps", PeriodStockSql.DropTemps().IndexOf("DROP TABLE #expCk;", StringComparison.Ordinal) > 0);
        }

        // 批号、供应商照 U8 写空串（不写 NULL）；期初两种特殊情况的检查覆盖七种单据。
        static void CheckStockKeys()
        {
            string all = PeriodStockSql.Pass(false) + PeriodStockSql.Pass(true) + PeriodStockSql.CheckPass + PeriodStockSql.Write;
            Expect("stock no null batch", all.IndexOf("NULLIF(ISNULL(cBatch", StringComparison.Ordinal) < 0
                && all.IndexOf("NULLIF(cbvencode", StringComparison.Ordinal) < 0);
            Expect("stock blank batch", PeriodStockSql.Pass(false).IndexOf("ISNULL(cBatch,N''), dVDate", StringComparison.Ordinal) > 0);
            string cross = PeriodStockSql.QcCrossMonthSql();
            string first = PeriodStockSql.QcBeforeFirstSql();
            foreach (string type in PeriodStockSql.Types)
            {
                Expect("stock qc " + type, cross.IndexOf("FROM rdrecord" + type + " WHERE bIsSTQc=1", StringComparison.Ordinal) > 0
                    && first.IndexOf("FROM rdrecord" + type + " WHERE bIsSTQc=1", StringComparison.Ordinal) > 0);
            }
            Expect("stock qc cross", cross.IndexOf("DATEDIFF(month, q.dDate, q.dVeriDate)<>0", StringComparison.Ordinal) > 0);
            Expect("stock qc first", first.IndexOf("FROM GL_mend", StringComparison.Ordinal) > 0
                && first.IndexOf("NOT EXISTS (SELECT 1 FROM ST_MonthAccount", StringComparison.Ordinal) > 0);
        }

        // 结账前检查的单据清单与拒绝原因。
        static void CheckStockText()
        {
            List<string> names = new List<string>();
            foreach (string[] spec in PeriodStockSql.Unaudited)
            {
                names.Add(spec[0]);
                Expect("stock unaudited scope " + spec[0], spec[1] == "m" || spec[1] == "u" || spec[1] == "a");
            }
            string all = string.Join("|", names.ToArray());
            foreach (string name in new string[] { "盘点单", "调拨单", "报废单", "库存期初单据", "本月采购入库单", "本月销售出库单" })
            {
                Expect("stock unaudited " + name, all.IndexOf(name, StringComparison.Ordinal) >= 0);
            }
            Expect("stock unaudited 11", PeriodStockSql.Unaudited[10][2].IndexOf("假退料", StringComparison.Ordinal) > 0);
            Expect("stock unaudited 01 first", PeriodStockSql.Unaudited[6][2].IndexOf("bIsSTQc=0", StringComparison.Ordinal) > 0);
            string text = PeriodCloseChecks.UnauditedText(new List<string>(new string[] { "盘点单", "本月其他入库单" }));
            Expect("stock unaudited text", text.IndexOf("盘点单、本月其他入库单", StringComparison.Ordinal) > 0
                && text.IndexOf("请先审核", StringComparison.Ordinal) > 0);
            Expect("stock option dep", PeriodCloseChecks.DefineSetText("cdepcode").IndexOf("部门", StringComparison.Ordinal) > 0);
            Expect("stock option item", PeriodCloseChecks.DefineSetText("item").IndexOf("收发类别", StringComparison.Ordinal) > 0
                && PeriodCloseChecks.DefineSetText("item").IndexOf("接口暂不支持", StringComparison.Ordinal) > 0);
        }

        // 响应与预演里的 stock_rows。
        static void CheckStockBody()
        {
            Dictionary<string, object> rows = new Dictionary<string, object>();
            foreach (string key in new string[] { "account", "accounts", "v", "vs", "check" })
            {
                rows[key] = 3;
            }
            Dictionary<string, object> other = new Dictionary<string, object>(rows);
            Expect("stock same", PeriodStock.Same(rows, other));
            other["check"] = 4;
            Expect("stock differ", !PeriodStock.Same(rows, other));
            PeriodRun run = new PeriodRun();
            run.Ask = new PeriodAsk();
            run.Ask.Close = true;
            run.Ask.Mod = PeriodModules.St;
            run.Ask.Year = 2026;
            run.Ask.Period = 8;
            run.Steps.Add(new int[] { PeriodModules.St, 2026, 8 });
            Expect("stock body none", !PeriodClose.Body(run).ContainsKey("stock_rows"));
            run.StockRows[202608] = rows;
            Expect("stock body", PeriodClose.Body(run)["stock_rows"] == rows);
            run.Ask.Through = true;
            List<object> through = (List<object>)PeriodClose.Body(run)["through"];
            Expect("stock through", ((Dictionary<string, object>)through[0])["stock_rows"] == rows);
        }

        // 存货核算结账脚本：拒绝编号 → 中文 409（英文原文不外露），U8 存储过程的原文去掉过程名。
        static void CheckIaText()
        {
            string at = PeriodGate.At(PeriodModules.Ia, 2026, 7);
            BridgeException ex = PeriodIa.At(IaRun.Refusal(new ScriptRefusal(50041, "prior IA month not closed: 2026-6", null)), at);
            Expect("ia prior", ex.Status == 409 && ex.Code == "state_mismatch" && ex.Message.StartsWith(at, StringComparison.Ordinal)
                && ex.Message.IndexOf("2026-6", StringComparison.Ordinal) > 0 && ex.Message.IndexOf("prior", StringComparison.Ordinal) < 0);
            ex = PeriodIa.At(IaRun.Refusal(new ScriptRefusal(50000, "IA_Close: 存货 A01 结存为负: 请检查", null)), at);
            Expect("ia u8", ex.Code == "u8_rejected" && ex.Message == at + "U8 拒绝：存货 A01 结存为负: 请检查");
            BridgeException hinted = new BridgeException(409, "state_mismatch", "x", "f", "h");
            ex = PeriodIa.At(hinted, at);
            Expect("ia at keeps", ex.Message == at + "x" && ex.Field == "f" && ex.Hint == "h" && ex.Detail == null);
            CheckIaTime();
        }

        // 剩余时间门槛：min(iaCommandSeconds / 2, 300)。
        static void CheckIaTime()
        {
            Expect("ia time 900", PeriodIa.Enough(300, 900) && !PeriodIa.Enough(299, 900));
            Expect("ia time 300", PeriodIa.Enough(150, 300) && !PeriodIa.Enough(149, 300));
        }

        // 响应与预演里的 ia_counts：单个存货核算在顶层，through 在各步。
        static void CheckIaBody()
        {
            Dictionary<string, object> counts = new Dictionary<string, object>();
            counts["bflag_IA"] = 1;
            PeriodRun run = new PeriodRun();
            run.Ask = new PeriodAsk();
            run.Ask.Close = true;
            run.Ask.Mod = PeriodModules.Ia;
            run.Ask.Year = 2026;
            run.Ask.Period = 7;
            run.Steps.Add(new int[] { PeriodModules.Ia, 2026, 7 });
            Expect("ia body none", !PeriodClose.Body(run).ContainsKey("ia_counts"));
            run.IaCounts[202607] = counts;
            Dictionary<string, object> body = PeriodClose.Body(run);
            Expect("ia body", body["ia_counts"] == counts && !body.ContainsKey("stock_rows"));
            run.Ask.Through = true;
            List<object> through = (List<object>)PeriodClose.Body(run)["through"];
            Expect("ia through", ((Dictionary<string, object>)through[0])["ia_counts"] == counts);
            run.Ask.Through = false;
            run.Ask.Mod = PeriodModules.St;
            Expect("ia body other module", !PeriodClose.Body(run).ContainsKey("ia_counts"));
        }

        static bool Throws(Action action)
        {
            try
            {
                action();
            }
            catch (BridgeException)
            {
                return true;
            }
            return false;
        }

        // 年度 2025–2026 各 12 期；(closedYear, closedTo) 及以前、(year2, to2) 年内 1..to2 期的各模块标志为 1。
        static PeriodMend Mend(int closedYear, int closedTo, int year2, int to2)
        {
            List<int[]> rows = new List<int[]>();
            for (int y = 2025; y <= 2026; y++)
            {
                for (int p = 1; p <= 12; p++)
                {
                    bool on = y < closedYear || (y == closedYear && p <= closedTo) || (y == year2 && p <= to2);
                    int f = on ? 1 : 0;
                    rows.Add(new int[] { y, p, f, f, f, f, f, f, f });
                }
            }
            return PeriodMend.From(rows);
        }

        static string Text(PeriodAsk ask)
        {
            return Join(new int[] { ask.Mod, ask.Year, ask.Period }).Replace(",", " ") + " " + ask.ActionText() + " "
                + (ask.Through ? "True" : "False");
        }

        static string Join(int[] values)
        {
            string[] parts = new string[values.Length];
            for (int i = 0; i < values.Length; i++)
            {
                parts[i] = PeriodGate.N(values[i]);
            }
            return string.Join(",", parts);
        }

        static Dictionary<string, object> Body(string module, int year, int period, string action)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (module != null)
            {
                body["module"] = module;
            }
            body["fiscal_year"] = year;
            body["period"] = period;
            body["action"] = action;
            return body;
        }

        static void Bad(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                PeriodCloseReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
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
