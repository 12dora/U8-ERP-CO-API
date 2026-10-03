using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一次请求要做的步骤：{ 模块, 年度, 期间 }，按执行顺序。
    internal sealed class PeriodRun
    {
        public PeriodAsk Ask;
        public bool[] Started;
        public readonly List<int[]> Steps = new List<int[]>();
        // 已启用的固定资产、薪资、成本：{ GL_mend 列, 名称 }（PeriodModules.ExtrasOf）。
        public List<string[]> Extras = new List<string[]>();
        // 启用年度第 0 期的期初记账标志（结账时读；没启用或取消结账时按已记账）。
        public bool PuPosted = true;
        public bool IaPosted = true;
        // 响应的 warnings（没有就不出）。
        public readonly List<string> Warnings = new List<string>();
        // 库存各步写入（取消结账：删掉）的快照行数，键 年*100+期（PeriodStock）。
        public readonly Dictionary<int, Dictionary<string, object>> StockRows = new Dictionary<int, Dictionary<string, object>>();
        // 存货核算走脚本的各步的诊断计数（PeriodIa），键同上。
        public readonly Dictionary<int, Dictionary<string, object>> IaCounts = new Dictionary<int, Dictionary<string, object>>();
        // 请求上下文：存货核算脚本在它的请求连接上执行（PeriodIa），记账人、时间预算、审计都从它取。
        public WorkContext Ctx;
    }

    // 月末结账 / 取消结账（periods/close）。U8 的采购、销售、应收、应付、总账月末结账没有可无界面调用的组件，
    // 桥与 U8 界面的结果一致（实测）：过完检查后改 GL_mend 该模块该期的结账标志（采购取消结账同样只改标志）。
    // 库存照 U8 写 / 删 ST_MonthAccount* 月结快照后再改标志（PeriodStock）；存货核算有数据的月份
    // 执行 sql/ia/close.sql / unclose.sql，标志由脚本改（PeriodIa），没有数据的月份只改标志（PeriodCloseChecks、PeriodReopenChecks）；
    // 采购结账要求启用年度已期初记账，总账结账要求已启用的固定资产、薪资、成本已结账（PeriodCloseFacts）。UA_Account_sub.iModiPeri 不改：桥的请求连接从不写 UFSYSTEM。
    // 一个请求一个事务（through 也是）：UPDLOCK, HOLDLOCK 读全部标志 → 逐步闸门 → 改 → 再读 → 提交；提交后在新连接上回读。
    // 预演（rollback）照样走到提交点，由提交钩子回滚。只对配置为测试账套的账套开放（PeriodCloseReq.RequireTestAccount）。
    internal static class PeriodClose
    {
        const string FlagCols = "iyear, iperiod, CONVERT(int, ISNULL(bflag_PU,0)) f0, CONVERT(int, ISNULL(bflag_SA,0)) f1,"
            + " CONVERT(int, ISNULL(bflag_ST,0)) f2, CONVERT(int, ISNULL(bflag_IA,0)) f3, CONVERT(int, ISNULL(bflag_AR,0)) f4,"
            + " CONVERT(int, ISNULL(bflag_AP,0)) f5, CONVERT(int, ISNULL(bflag,0)) f6";
        const string LockedSql = "SELECT " + FlagCols + " FROM GL_mend WITH (UPDLOCK, HOLDLOCK)"
            + " WHERE iperiod BETWEEN 1 AND 12 ORDER BY iyear, iperiod";
        const string PlainSql = "SELECT " + FlagCols + " FROM GL_mend WHERE iperiod BETWEEN 1 AND 12 ORDER BY iyear, iperiod";
        const string SetSql = "UPDATE GL_mend SET {COL}=? WHERE iyear=? AND iperiod=?";
        const int MaxRows = 1200;

        public static ApiResult Run(WorkContext ctx)
        {
            PeriodRun run = new PeriodRun();
            run.Ask = PeriodCloseReq.Parse(ctx.Item.Body);
            run.Ctx = ctx;
            // 入队后再查一次测试账套名单（登录前已查过）。
            PeriodCloseReq.RequireTestAccount(ctx.Item);
            if (!run.Ask.Through)
            {
                PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(PeriodCloseReq.RuleKey(run.Ask.Mod, run.Ask.Close)));
            }
            List<string> subs = PeriodModules.StartedSubs(ctx);
            run.Started = PeriodModules.StartedOf(subs);
            run.Extras = PeriodModules.ExtrasOf(subs);
            int mod = run.Ask.Through ? PeriodModules.Gl : run.Ask.Mod;
            if (!run.Started[mod])
            {
                throw Refuse(PeriodModules.Title(mod) + "未启用");
            }
            CoRows.Note(ctx.Item, Note(run.Ask));
            Tran(ctx, run);
            Reread(ctx, run);
            return ApiResult.Ok(Body(run));
        }

        static string Note(PeriodAsk ask)
        {
            string what = ask.Through ? "逐月结账到" : (ask.Close ? "月末结账 " : "取消结账 ") + PeriodModules.Code(ask.Mod);
            return what + " " + PeriodGate.N(ask.Year) + "-" + PeriodGate.N(ask.Period);
        }

        static void Tran(WorkContext ctx, PeriodRun run)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                PeriodMend mend = Load(conn, LockedSql);
                Plan(ctx, run, mend);
                PeriodCloseFacts.Opening(conn, run, mend.MinYear);
                foreach (int[] step in run.Steps)
                {
                    Apply(conn, run, mend, step);
                }
                if (Mismatch(Load(conn, LockedSql), run) != null)
                {
                    throw new BridgeException(409, "u8_rejected", "结账标志没有改过来，已回滚");
                }
                Preview(run);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                // 执行过存货核算脚本（PeriodIa）时核对事务层数：U8 过程自行结束了事务就是结果未知（IaRun.AfterRollback）。
                BridgeException broken = IaRun.AfterRollback(ctx.Item, true);
                if (broken != null)
                {
                    throw broken;
                }
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                // 回滚前按已执行的存货核算脚本总时长放宽看门狗；回滚后层数不对时改报 504（没执行过脚本时两者都不起作用）。
                IaRun.BeforeRollback(ctx.Item);
                bool wasOpen = open;
                CoRows.CatchTran(conn, ctx.Item, open);
                BridgeException unknown = IaRun.AfterRollback(ctx.Item, wasOpen);
                if (unknown != null)
                {
                    throw unknown;
                }
                throw;
            }
        }

        // 单个模块：就是请求的那一期。through：各已启用模块（总账最后）从最早的年度到请求的期间为止的每个未结账期间；
        // 要所涉每个模块的结账权限。
        static void Plan(WorkContext ctx, PeriodRun run, PeriodMend mend)
        {
            PeriodAsk ask = run.Ask;
            Must(PeriodGate.Missing(mend, ask.Year, ask.Period), ask.Through ? PeriodModules.Gl : ask.Mod, ask);
            if (!ask.Through)
            {
                run.Steps.Add(new int[] { ask.Mod, ask.Year, ask.Period });
                return;
            }
            PermContext p = PermCheck.Of(ctx);
            for (int m = 0; m < PeriodModules.Count; m++)
            {
                List<int[]> open = run.Started[m] ? mend.OpenUpTo(m, ask.Year, ask.Period) : new List<int[]>();
                if (open.Count > 0)
                {
                    PermCheck.RequireRule(p, PermRegistry.ForKey(PeriodCloseReq.RuleKey(m, true)));
                }
                foreach (int[] yp in open)
                {
                    run.Steps.Add(new int[] { m, yp[0], yp[1] });
                }
            }
        }

        // 一步：顺序闸门、同期其他模块、单据检查，然后改标志（内存里的标志同步改，后面的步骤按改后的看）。
        // 存货核算有数据的月份由脚本改标志，这里不再改。
        static void Apply(object conn, PeriodRun run, PeriodMend mend, int[] step)
        {
            int mod = step[0];
            int year = step[1];
            int period = step[2];
            bool close = run.Ask.Close;
            bool ia = mod == PeriodModules.Ia && PeriodIa.HasData(conn, year, period);
            string refusal = Check(conn, run, mend, step, ia);
            if (refusal != null)
            {
                throw Refuse(PeriodGate.At(mod, year, period) + refusal);
            }
            if (mod == PeriodModules.St)
            {
                run.StockRows[year * 100 + period] = close ? PeriodStock.Close(conn, year, period) : PeriodStock.Reopen(conn, year, period);
            }
            if (ia)
            {
                run.IaCounts[year * 100 + period] = PeriodIa.Run(run, year, period);
            }
            else
            {
                GlSql.Exec(conn, SetSql.Replace("{COL}", PeriodModules.Col(mod)), new object[] { close ? 1 : 0, year, period });
            }
            mend.Set(mod, year, period, close);
        }

        // 一步的全部检查，返回拒绝原因（可以做返回 null）。ia：存货核算走脚本，单据检查换成 PeriodIa.Check。
        static string Check(object conn, PeriodRun run, PeriodMend mend, int[] step, bool ia)
        {
            int mod = step[0];
            int year = step[1];
            int period = step[2];
            bool close = run.Ask.Close;
            string refusal = close ? PeriodGate.Close(mend, mod, year, period) : PeriodGate.Reopen(mend, mod, year, period);
            refusal = refusal ?? PeriodGate.Order(mend, mod, year, period, run.Started, close);
            refusal = refusal ?? PeriodCloseFacts.Step(conn, run, step);
            if (refusal != null || ia)
            {
                return refusal ?? PeriodIa.Check(conn, close, year, period);
            }
            return close ? PeriodCloseChecks.Close(conn, mod, year, period) : PeriodReopenChecks.Reopen(conn, mod, year, period);
        }

        // 各步骤的标志与目标不符时返回第一个不符的步骤，都对时返回 null。纯函数，--selftest 用。
        internal static int[] Mismatch(PeriodMend mend, PeriodRun run)
        {
            foreach (int[] step in run.Steps)
            {
                if (mend.Closed(step[0], step[1], step[2]) != run.Ask.Close)
                {
                    return step;
                }
            }
            return null;
        }

        static PeriodMend Load(object conn, string sql)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[0], MaxRows);
            List<int[]> list = new List<int[]>(rows.Count);
            foreach (Dictionary<string, object> row in rows)
            {
                int[] cells = new int[2 + PeriodModules.Count];
                cells[0] = GlSql.Int(row, "iyear");
                cells[1] = GlSql.Int(row, "iperiod");
                for (int i = 0; i < PeriodModules.Count; i++)
                {
                    cells[2 + i] = GlSql.Int(row, "f" + PeriodGate.N(i));
                }
                list.Add(cells);
            }
            return PeriodMend.From(list);
        }

        // 预演：提交钩子会回滚，这里交出会结账（取消结账）的期间，detail.periods（库存那几步带 stock_rows，
        // 存货核算走脚本的那几步带 ia_counts）；单个库存另给 detail.stock_rows，单个存货核算另给 detail.ia_counts。
        static void Preview(PeriodRun run)
        {
            if (DryRun.Active)
            {
                DryRun.Set("periods", Periods(run));
                Dictionary<string, object> rows = SingleStock(run);
                if (rows != null)
                {
                    DryRun.Set("stock_rows", rows);
                }
                Dictionary<string, object> counts = Single(run, PeriodModules.Ia, run.IaCounts);
                if (counts != null)
                {
                    DryRun.Set("ia_counts", counts);
                }
                if (run.Warnings.Count > 0)
                {
                    DryRun.Set("warnings", new List<string>(run.Warnings));
                }
            }
        }

        // 已提交。新连接回读全部标志；读不出来或不是目标状态都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static void Reread(WorkContext ctx, PeriodRun run)
        {
            object conn = null;
            PeriodMend mend;
            try
            {
                conn = ctx.OpenFresh();
                mend = Load(conn, PlainSql);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "结账标志回读 " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读结账标志，请先用 reports/close_status 核对");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (Mismatch(mend, run) != null)
            {
                throw new BridgeException(504, "outcome_unknown",
                    "已提交，但回读时结账标志不是预期状态；请先用 reports/close_status 核对，不要直接重试");
            }
        }

        static List<object> Periods(PeriodRun run)
        {
            List<object> list = new List<object>(run.Steps.Count);
            foreach (int[] step in run.Steps)
            {
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["module"] = PeriodModules.Code(step[0]);
                item["fiscal_year"] = step[1];
                item["period"] = step[2];
                Dictionary<string, object> rows;
                if (step[0] == PeriodModules.St && run.StockRows.TryGetValue(step[1] * 100 + step[2], out rows))
                {
                    item["stock_rows"] = rows;
                }
                if (step[0] == PeriodModules.Ia && run.IaCounts.TryGetValue(step[1] * 100 + step[2], out rows))
                {
                    item["ia_counts"] = rows;
                }
                list.Add(item);
            }
            return list;
        }

        internal static Dictionary<string, object> Body(PeriodRun run)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["action"] = run.Ask.ActionText();
            if (run.Warnings.Count > 0)
            {
                body["warnings"] = new List<string>(run.Warnings);
            }
            if (run.Ask.Through)
            {
                body["through"] = Periods(run);
                body["count"] = run.Steps.Count;
                return body;
            }
            body["module"] = PeriodModules.Code(run.Ask.Mod);
            body["fiscal_year"] = run.Ask.Year;
            body["period"] = run.Ask.Period;
            body["closed"] = run.Ask.Close;
            Dictionary<string, object> stock = SingleStock(run);
            if (stock != null)
            {
                body["stock_rows"] = stock;
            }
            Dictionary<string, object> counts = Single(run, PeriodModules.Ia, run.IaCounts);
            if (counts != null)
            {
                body["ia_counts"] = counts;
            }
            return body;
        }

        // 单个库存请求那一步的快照行数；不是库存或没走到写快照时为 null。
        static Dictionary<string, object> SingleStock(PeriodRun run)
        {
            return Single(run, PeriodModules.St, run.StockRows);
        }

        // 单个 mod 模块请求那一步在 byStep 里的值；through、别的模块或没有这一步的值时为 null。
        static Dictionary<string, object> Single(PeriodRun run, int mod, Dictionary<int, Dictionary<string, object>> byStep)
        {
            Dictionary<string, object> rows;
            if (run.Ask.Through || run.Ask.Mod != mod
                || !byStep.TryGetValue(run.Ask.Year * 100 + run.Ask.Period, out rows))
            {
                return null;
            }
            return rows;
        }

        static void Must(string refusal, int mod, PeriodAsk ask)
        {
            if (refusal != null)
            {
                throw Refuse(PeriodGate.At(mod, ask.Year, ask.Period) + refusal);
            }
        }

        static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
