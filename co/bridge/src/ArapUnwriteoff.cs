using System;
using System.Collections.Generic;

namespace U8Co
{
    // 取消核销（arap/writeoff/cancel）：按核销号整批撤销一次 9P 核销。
    // U8 没有可调用的取消组件：界面「取消操作」自己跑 SQL，U8ApCancel.cLsCancel.ACCJZ_Cancel
    // 实测什么都不写。桥按 U8 取消核销时执行的 SQL 写（实测核对），在请求连接的一个事务里：带锁快照整批核销行 → 闸门 →
    // 收付款单行余额 → 应收应付单余额 → 发票累计核销（销售发票经 U8 的 clsWrite2Bill.UpdateBillForAR）→ 核销人 → 删核销行，
    // 提交前核对每个余额都精确回到核销前，提交后在新连接上确认核销行已不在。不做「保留线索」的反向插入（bAR2Cancel / bAP2Cancel 为 0 的账套 U8 也不插）。
    internal static class ArapUnwriteoff
    {
        const decimal Tolerance = 0.005m;

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            UnwriteoffAsk ask = ArapUnwriteoffReq.Parse(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.WriteoffCancelKey(ask.Flag));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            UnwriteoffPlan plan = Tran(ctx, ask, rule);
            return Reread(ctx, plan);
        }

        static UnwriteoffPlan Tran(WorkContext ctx, UnwriteoffAsk ask, PermRule rule)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                UnwriteoffPlan plan = ArapUnwriteoffGate.Plan(conn, ask, ctx.Item.Acc);
                List<Dictionary<string, object>> heads = new List<Dictionary<string, object>>();
                foreach (UnwriteoffTarget t in plan.Targets)
                {
                    heads.Add(t.Head);
                }
                ArapWriteoffCheck.Allowed(ctx, rule, plan.Head, heads);
                ArapWriteoffCheck.Guard(conn, delegate { Write(ctx, plan); });
                if (DryRun.Active)
                {
                    ArapWriteoffDry.Cancel(plan, Body(ctx, plan));
                }
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, "已取消核销 " + plan.CancelNo);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // U8 取消操作的顺序：C 余额 → D / E 发票 → F 核销人 → B 删行；然后核对。
        static void Write(WorkContext ctx, UnwriteoffPlan plan)
        {
            object conn = ctx.Conn;
            UnwriteoffSql.CloseBills(conn, plan);
            if (plan.Has("ar_bill") || plan.Has("ap_bill"))
            {
                UnwriteoffSql.Bills(conn, plan);
            }
            if (plan.Has("sale_invoice"))
            {
                ArapUnwriteoffBill.Sale(ctx, plan);
            }
            if (plan.Has("purchase_invoice"))
            {
                ArapUnwriteoffBill.Purchase(conn, plan);
            }
            UnwriteoffSql.CancelMan(conn, plan);
            UnwriteoffSql.Delete(conn, plan);
            string problem = Mismatch(conn, plan);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "取消核销后核对不符，已回滚：" + problem);
            }
        }

        // 核销行删光；收付款单每行余额 = 原值 + 冲减合计；每个单据行余额 = 原值 + 本批金额；应收应付单 iRAmount_f = 原值 + 借+贷。
        static string Mismatch(object conn, UnwriteoffPlan plan)
        {
            if (UnwriteoffSql.CountRows(conn, plan.Flag, plan.CancelNo) != 0)
            {
                return "核销行没有删干净";
            }
            foreach (UnwriteoffLine line in plan.Lines)
            {
                decimal now = WriteoffSql.ReceiptRemain(conn, line.Line);
                if (Math.Abs(now - (line.Before + line.Back)) > Tolerance)
                {
                    return "收付款单 " + plan.ReceiptCode + " 余额 " + ArapWriteoffGate.Money(now);
                }
            }
            foreach (UnwriteoffTarget t in plan.Targets)
            {
                decimal now = WriteoffSql.Balance(conn, plan.Flag, t.VType, t.Code, t.Dw, t.Line);
                bool bill = t.Kind.LineSql == null;
                decimal head = bill ? UnwriteoffSql.BillRemain(conn, plan.Flag, t.VType, t.Code) : 0m;
                if (Math.Abs(now - (t.Before + t.Amount)) > Tolerance
                    || (bill && Math.Abs(head - (t.BillBefore + t.Raise)) > Tolerance))
                {
                    return t.Kind.Title + " " + t.Code + " 余额 " + ArapWriteoffGate.Money(now);
                }
            }
            return null;
        }

        // 已提交。新连接（不加 NOLOCK）确认该核销号的行已不在；读不出来或还在都是 504 outcome_unknown（已提交，先核对，不要重投）。
        // 余额不再逐项比：提交后 U8 客户端可能已在同一张单据上又做了处理。
        static ApiResult Reread(WorkContext ctx, UnwriteoffPlan plan)
        {
            object conn = null;
            int left;
            try
            {
                conn = ctx.OpenFresh();
                left = UnwriteoffSql.CountRows(conn, plan.Flag, plan.CancelNo);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapUnwriteoff " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，核销号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", "取消核销已提交，但回读时核销号 " + plan.CancelNo
                    + " 的明细行仍在；请先查核销记录核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan));
        }

        static Dictionary<string, object> Body(WorkContext ctx, UnwriteoffPlan plan)
        {
            List<object> lines = new List<object>();
            foreach (UnwriteoffLine line in plan.Lines)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["line_id"] = line.Line;
                one["amount"] = line.Back;
                one["remaining"] = line.Before + line.Back;
                lines.Add(one);
            }
            Dictionary<string, object> receipt = new Dictionary<string, object>();
            receipt["type"] = plan.ReceiptKind;
            receipt["id"] = plan.ReceiptId;
            receipt["line_id"] = plan.Lines.Count == 1 ? (object)plan.Lines[0].Line : null;
            receipt["code"] = plan.ReceiptCode;
            receipt["remaining"] = plan.Lines.Count == 1 ? (object)(plan.Lines[0].Before + plan.Lines[0].Back) : null;
            receipt["lines"] = lines;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["cancel_no"] = plan.CancelNo;
            body["flag"] = plan.Flag;
            body["receipt"] = receipt;
            body["items"] = Items(plan);
            return body;
        }

        static List<object> Items(UnwriteoffPlan plan)
        {
            List<object> items = new List<object>();
            foreach (UnwriteoffTarget t in plan.Targets)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = t.Kind.Name;
                one["id"] = t.Id;
                one["line_id"] = t.Line > 0 ? (object)t.Line : null;
                one["code"] = t.Code;
                one["amount"] = t.Amount;
                one["remaining"] = t.Before + t.Amount;
                items.Add(one);
            }
            return items;
        }
    }
}
