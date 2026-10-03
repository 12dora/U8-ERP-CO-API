using System;
using System.Collections.Generic;

namespace U8Co
{
    // 应收坏账发生（arap/bad_debt action=occur，U8 处理方式 9G，处理号 HZAR…）。只对测试账套开放（分派 ArapBad 已查）。
    // U8 没有可调用的组件：「坏账发生」界面自己写库。桥按界面执行的 SQL 写（实测核对），在请求连接的一个事务里：
    // 公共闸门（ArapBad.Open：期间、启用日期、结账、坏账准备参数）→ 带锁读单据、过闸门、按行分摊（ArapBadOccurPlan）→ 数据权限 →
    // 脚本 sql/arap/bad_occur.sql（取号、每个分摊行一条 9G 贷方处理行、应收单余额、坏账准备余额减本币合计）→
    // 销售发票经 U8 的 clsWrite2Bill.UpdateBillForAR 加累计核销 → 核对（处理行数、各行余额、发票累计、坏账准备余额）→ 提交。
    // 提交后在新连接上确认处理行数。U8 界面可以随后制单，本接口不制单，制单走 arap/process/voucher。
    internal static class ArapBadOccur
    {
        const string Title = "坏账发生";

        public static ApiResult Run(WorkContext ctx, ArapBadAsk ask)
        {
            BadOccurPlan plan = Tran(ctx, ask);
            Reread(ctx, plan);
            return ApiResult.Ok(Body(ctx, ask, plan, false));
        }

        static BadOccurPlan Tran(WorkContext ctx, ArapBadAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                BadOccurPlan plan = ArapBadOccurPlan.Plan(ctx, ask);
                Allowed(ctx, plan);
                ArapWriteoffCheck.Guard(conn, delegate { Write(ctx, ask, plan); });
                ArapBadOccurSql.Drop(conn);
                if (DryRun.Active)
                {
                    Dry(plan, Body(ctx, ask, plan, true));
                }
                ArapBad.Finish(ctx, Title);
                open = false;
                CoRows.Note(ctx.Item, Title + " 处理号 " + plan.CancelNo + " 本币 " + ArapBadRule.Money(plan.TotalN));
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 数据权限：每张单据的表头（客户、部门、业务员），一张不在权限内就 403。
        static void Allowed(WorkContext ctx, BadOccurPlan plan)
        {
            PermContext p = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(PermRegistry.BadDebtKey(ArapBadReq.Occur));
            foreach (BadDoc d in plan.Docs)
            {
                if (!PermCheck.RowAllowed(p, rule, d.Head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        static void Write(WorkContext ctx, ArapBadAsk ask, BadOccurPlan plan)
        {
            object conn = ctx.Conn;
            ArapBadOccurSql.Prepare(conn, plan, ask);
            ScriptResult script = ArapBad.Script(ctx, ArapBadOccurSql.Script, "cancel_no", Title, ArapBadOccurSql.Texts);
            plan.CancelNo = Count(script, "cancel_no");
            if (!ArapBadRule.NoValid(plan.CancelNo))
            {
                throw new BridgeException(409, "u8_rejected", "坏账发生没有得到有效的处理号（" + plan.CancelNo + "），已回滚");
            }
            plan.RemainAfter = WriteoffSql.Num(Count(script, "remain_after"));
            if (CoRows.AsId(Count(script, "com_ar")) > 0)
            {
                ArapBadOccurSql.SaleBill(ctx, plan);
            }
            string problem = ArapBadOccurSql.Mismatch(conn, plan, ask.Customer);
            if (problem == null)
            {
                problem = RemainProblem(ArapBad.ParaRemain(conn, plan.Open.ParaId), plan.Open.RemainBefore - plan.TotalN);
            }
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "坏账发生后核对不符，已回滚：" + problem);
            }
        }

        // 坏账准备余额核对（发生减、收回加本币金额）。符合返回 null。纯函数，--selftest 用；坏账收回也用。
        internal static string RemainProblem(decimal now, decimal want)
        {
            return now == want ? null : "坏账准备余额是 " + ArapBadRule.Money(now) + "，应为 " + ArapBadRule.Money(want);
        }

        internal static string Count(ScriptResult script, string key)
        {
            string text;
            return script != null && script.Counts.TryGetValue(key, out text) ? (text ?? "").Trim() : "";
        }

        // 已提交。新连接（不加 NOLOCK）确认该处理号的 9G 行数；读不出来或不符都是 504 outcome_unknown（先核对，不要重投）。
        static void Reread(WorkContext ctx, BadOccurPlan plan)
        {
            object conn = null;
            int found;
            try
            {
                conn = ctx.OpenFresh();
                found = ArapBad.CountRows(conn, ArapBadRule.OccurStyle, plan.CancelNo);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapBadOccur " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "坏账发生已提交但未能回读，处理号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            int want = 0;
            foreach (BadPiece p in plan.Pieces)
            {
                want++;
            }
            if (found != want)
            {
                throw new BridgeException(504, "outcome_unknown", "坏账发生已提交，但回读时处理号 " + plan.CancelNo
                    + " 的往来明细行数不符；请先在 U8 里核对，不要直接重试");
            }
        }

        // 响应：amount 是原币合计，amount_native 是本币合计（坏账准备余额按本币减）；rows 每个分摊行一项。
        internal static Dictionary<string, object> Body(WorkContext ctx, ArapBadAsk ask, BadOccurPlan plan, bool dry)
        {
            Dictionary<string, object> body = ArapBad.Body(ctx, ask, plan.Open, ArapBadRule.OccurStyle, dry);
            body["cancel_no"] = plan.CancelNo;
            body["currency"] = plan.Currency;
            body["rate"] = plan.Rate;
            body["digest"] = plan.Digest;
            body["amount"] = plan.TotalF;
            body["amount_native"] = plan.TotalN;
            body["remain_before"] = plan.Open.RemainBefore;
            body["remain_after"] = plan.RemainAfter;
            body["rows"] = RowsOut(plan);
            return body;
        }

        // 每张单据的每一行：本次金额（原币、本币）和处理后的余额（原币）。
        static List<object> RowsOut(BadOccurPlan plan)
        {
            List<object> rows = new List<object>();
            foreach (BadPiece p in plan.Pieces)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = p.Doc.Ask.Type;
                one["id"] = p.Doc.Ask.Id;
                one["doc_id"] = p.Doc.Id;
                one["line_id"] = p.Line > 0 ? (object)p.Line : null;
                one["amount"] = p.F;
                one["amount_native"] = p.N;
                one["remaining"] = p.BeforeF - p.F;
                rows.Add(one);
            }
            return rows;
        }

        // 预演（rollback）：写和核对都在事务里跑过之后，把处理号、金额和涉及的单据交给 detail，提交钩子随后回滚。
        static void Dry(BadOccurPlan plan, Dictionary<string, object> body)
        {
            foreach (BadDoc d in plan.Docs)
            {
                VoucherKind kind = Kinds.Find(ArapBadRule.KindOf(d.Ask.Type));
                if (kind != null && d.Id > 0)
                {
                    DryRun.Touched(kind, d.Id);
                }
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set(ArapBad.DryKey, copy);
        }
    }
}
