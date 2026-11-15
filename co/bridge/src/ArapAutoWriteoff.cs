using System;
using System.Collections.Generic;

namespace U8Co
{
    // 自动核销（arap/writeoff/auto）：一个往来单位下，有余额的收付款单行按先来先核对有余额的发票、应收应付单行（配对规则在
    // ArapAutoWriteoffPlan）。不调 U8 的 cLsCancel.AutoCancel：测试账套实测直接调用返回 True 但什么都不写、也不报原因。
    // 改为每行收付款单一批，走手工核销的同一个核心（ArapWriteoff.SaveOne：闸门、数据权限、一个 close 下挂若干 vouch 的 Save、
    // 事务里核对），全部批在一个事务里，任何一批失败整体回滚（全有或全无）；提交后在新连接上逐批回读核销行。
    // dry_run 只算计划：候选查询加每一批的闸门（只读），不建组件、不写。计划为空时 200，batches 为空、total 为 0。
    internal static class ArapAutoWriteoff
    {
        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            AutoWriteoffAsk ask = ArapAutoWriteoffReq.Parse(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.WriteoffAutoKey(ask.Flag));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            DateTime day = ArapWriteoff.Day(ctx.Item.Date);
            string option = AutoWriteoffSql.RuleOption(ctx.Conn, ask.Flag);
            if (option != null)
            {
                throw ArapWriteoffGate.State("账套设置了按规则核销（订单 / 合同 / 存货等，" + option + "），自动核销暂不支持");
            }
            List<AutoBatch> batches = ArapAutoWriteoffPlan.Build(ctx.Conn, ask);
            if (ask.DryRun)
            {
                return Dry(ctx, ask, day, rule, batches);
            }
            if (batches.Count == 0)
            {
                return ApiResult.Ok(Body(ctx, ask, new List<object>(), 0m));
            }
            object hx = null;
            List<WriteoffPlan> done;
            try
            {
                hx = ArapWriteoff.Open(ctx, ask.Flag);
                done = Tran(ctx, hx, ask, day, rule, batches);
            }
            finally
            {
                ComUtil.Final(hx);
            }
            return Reread(ctx, ask, done);
        }

        // 只读：每一批照样过闸门和数据权限（同样的 404 / 409 / 403），不开事务、不写。
        static ApiResult Dry(WorkContext ctx, AutoWriteoffAsk ask, DateTime day, PermRule rule, List<AutoBatch> batches)
        {
            List<object> plan = new List<object>();
            decimal total = 0m;
            foreach (AutoBatch b in batches)
            {
                WriteoffPlan checkedPlan = ArapWriteoffGate.Plan(ctx.Conn, b.Ask(ask.Flag), day, ctx.Item.Acc);
                ArapWriteoffCheck.Allowed(ctx, rule, checkedPlan);
                plan.Add(DryBatch(b));
                total += b.Amount;
            }
            Dictionary<string, object> body = Head(ctx, ask);
            body["dry_run"] = true;
            // 与写预演的响应对齐，自动核销的 dry_run 是只读试算（DryRunModes.Plan）。
            body["mode"] = DryRunModes.Plan;
            body["plan"] = plan;
            body["total"] = total;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> DryBatch(AutoBatch b)
        {
            Dictionary<string, object> receipt = new Dictionary<string, object>();
            receipt["type"] = b.ReceiptKind;
            receipt["id"] = b.ReceiptId;
            receipt["line_id"] = b.Line;
            receipt["code"] = b.Code;
            receipt["date"] = b.Date;
            receipt["remaining"] = b.Remain;
            List<object> targets = new List<object>();
            foreach (AutoPiece p in b.Pieces)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = p.Target.Kind;
                one["id"] = p.Target.Id;
                one["line_id"] = ArapWriteoffReq.IsBill(p.Target.Kind) ? null : (object)p.Target.Line;
                one["code"] = p.Target.Code;
                one["date"] = p.Target.Date;
                one["balance"] = p.Before;
                one["amount"] = p.Amount;
                targets.Add(one);
            }
            Dictionary<string, object> batch = new Dictionary<string, object>();
            batch["receipt"] = receipt;
            batch["targets"] = targets;
            batch["amount"] = b.Amount;
            return batch;
        }

        // 一个事务：逐批过闸门（带锁重读，余额是前面各批 Save 之后的）→ SaveOne；任何一批抛错整体回滚。
        static List<WriteoffPlan> Tran(WorkContext ctx, object hx, AutoWriteoffAsk ask, DateTime day, PermRule rule,
            List<AutoBatch> batches)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                List<WriteoffPlan> done = new List<WriteoffPlan>();
                foreach (AutoBatch b in batches)
                {
                    WriteoffPlan plan = ArapWriteoffGate.Plan(conn, b.Ask(ask.Flag), day, ctx.Item.Acc);
                    ArapWriteoff.SaveOne(ctx, hx, plan, rule);
                    done.Add(plan);
                }
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, "自动核销 " + done.Count + " 批，末批核销号 " + done[done.Count - 1].CancelNo);
                return done;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 已提交。新连接（不加 NOLOCK）逐批回读；读不出来或对不上都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, AutoWriteoffAsk ask, List<WriteoffPlan> done)
        {
            object conn = null;
            string problem = null;
            try
            {
                conn = ctx.OpenFresh();
                foreach (WriteoffPlan plan in done)
                {
                    string one = ArapWriteoffCheck.Written(conn, plan);
                    problem = problem ?? (one == null ? null : "核销号 " + plan.CancelNo + "：" + one);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapAutoWriteoff " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读自动核销结果，核销号 " + Nos(done));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (problem != null)
            {
                throw new BridgeException(504, "outcome_unknown", "自动核销已提交但回读不符（" + problem + "），核销号 " + Nos(done)
                    + "；请先查核销记录核对，不要直接重试");
            }
            List<object> batches = new List<object>();
            decimal total = 0m;
            foreach (WriteoffPlan plan in done)
            {
                Dictionary<string, object> batch = new Dictionary<string, object>();
                batch["cancel_no"] = plan.CancelNo;
                batch["receipt"] = ArapWriteoffCheck.ReceiptOut(plan);
                batch["items"] = ArapWriteoffCheck.ItemsOut(plan);
                batch["amount"] = plan.Sum;
                batches.Add(batch);
                total += plan.Sum;
            }
            return ApiResult.Ok(Body(ctx, ask, batches, total));
        }

        static string Nos(List<WriteoffPlan> done)
        {
            List<string> nos = new List<string>();
            foreach (WriteoffPlan plan in done)
            {
                nos.Add(plan.CancelNo);
            }
            return string.Join("、", nos.ToArray());
        }

        static Dictionary<string, object> Body(WorkContext ctx, AutoWriteoffAsk ask, List<object> batches, decimal total)
        {
            Dictionary<string, object> body = Head(ctx, ask);
            body["batches"] = batches;
            body["total"] = total;
            return body;
        }

        static Dictionary<string, object> Head(WorkContext ctx, AutoWriteoffAsk ask)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = ask.Flag;
            body["partner"] = ask.Partner;
            body["date"] = ctx.Item.Date;
            body["date_to"] = ask.Cutoff;
            return body;
        }
    }
}
