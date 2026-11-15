using System;
using System.Collections.Generic;

namespace U8Co
{
    // 应收冲应付（arap/transfer，flag AR，处理方式 9I，号 YCFAP…）/ 应付冲应收（flag AP，9J，号 FCYAR…）。
    // U8 没有可调用的转账组件：界面自己跑 SQL。桥按界面执行的 SQL 写（实测核对），
    // 在请求连接的一个事务里：带锁读单据、过闸门（ArapTransferGate）→ 数据权限 → 取号、写两张往来明细、扣余额、核对（ArapTransferWrite），
    // 提交后在新连接上确认两张往来明细上该号的行数。U8 界面随后自动制单（bPZMakePZNow），本接口不制单，制单走 arap/process/voucher。
    internal static class ArapTransfer
    {
        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            TransferAsk ask = ArapTransferReq.Parse(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.TransferKey(ask.Flag));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            TransferPlan plan = Tran(ctx, ask);
            return Reread(ctx, plan);
        }

        static TransferPlan Tran(WorkContext ctx, TransferAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                TransferPlan plan = ArapTransferGate.Plan(ctx, ask);
                Allowed(ctx, plan);
                ArapWriteoffCheck.Guard(conn, delegate { ArapTransferWrite.Write(ctx, plan); });
                if (DryRun.Active)
                {
                    Dry(plan, Body(ctx, plan));
                }
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, ArapTransferRule.Title(plan.Flag) + " 处理号 " + plan.CancelNo);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 数据权限：应收一侧的单据按客户规则（往来单位、部门、业务员），应付一侧按供应商规则；一张不在权限内就 403。
        static void Allowed(WorkContext ctx, TransferPlan plan)
        {
            PermContext p = PermCheck.Of(ctx);
            foreach (TransferDoc d in plan.Docs)
            {
                if (!PermCheck.RowAllowed(p, PermRegistry.TransferRowRule(d.Side), d.Head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        // 已提交。新连接（不加 NOLOCK）确认两张往来明细上该号的行数；读不出来或对不上都是 504 outcome_unknown（先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, TransferPlan plan)
        {
            object conn = null;
            string problem = null;
            try
            {
                conn = ctx.OpenFresh();
                foreach (string side in new string[] { "AR", "AP" })
                {
                    int want = ArapTransferWrite.Expected(side == "AP" ? plan.Ap : plan.Ar);
                    if (problem == null && TransferSql.CountRows(conn, side, plan.Style, plan.CancelNo) != want)
                    {
                        problem = (side == "AP" ? "应付" : "应收") + "往来明细的处理行数不对";
                    }
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapTransfer " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，处理号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (problem != null)
            {
                throw new BridgeException(504, "outcome_unknown", ArapTransferRule.Title(plan.Flag) + "已提交但回读不符（处理号 "
                    + plan.CancelNo + "）：" + problem + "；请先查往来明细核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan));
        }

        internal static Dictionary<string, object> Body(WorkContext ctx, TransferPlan plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = plan.Flag;
            body["style"] = plan.Style;
            body["cancel_no"] = plan.CancelNo;
            body["date"] = plan.Date;
            body["customer"] = plan.Customer;
            body["vendor"] = plan.Vendor;
            body["currency"] = plan.Currency;
            body["amount"] = plan.Sum;
            body["digest"] = plan.Digest;
            body["ar_rows"] = RowsOut(plan.Ar);
            body["ap_rows"] = RowsOut(plan.Ap);
            return body;
        }

        // 每张单据的每一行：本次金额（原币、本币）和转账后的余额（原币）。
        static List<object> RowsOut(List<TransferDoc> docs)
        {
            List<object> rows = new List<object>();
            foreach (TransferDoc d in docs)
            {
                foreach (TransferPiece p in d.Pieces)
                {
                    Dictionary<string, object> one = new Dictionary<string, object>();
                    one["type"] = d.Ask.Type;
                    one["id"] = d.Ask.Code;
                    one["doc_id"] = d.Id;
                    one["line_id"] = p.Line > 0 ? (object)p.Line : null;
                    one["amount"] = p.F;
                    one["amount_native"] = p.N;
                    one["remaining"] = p.BeforeF - p.F;
                    rows.Add(one);
                }
            }
            return rows;
        }

        // 预演（rollback）：写和核对都在事务里跑过之后，把处理号、金额和涉及的单据交给 detail，提交钩子随后回滚。
        static void Dry(TransferPlan plan, Dictionary<string, object> body)
        {
            foreach (TransferDoc d in plan.Docs)
            {
                VoucherKind kind = Kinds.Find(d.Kind);
                if (kind != null && d.Id > 0)
                {
                    DryRun.Touched(kind, d.Id);
                }
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set("transfer", copy);
        }
    }
}
