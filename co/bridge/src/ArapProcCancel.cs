using System;
using System.Collections.Generic;

namespace U8Co
{
    // 取消应收冲应付 / 应付冲应收 / 并账（arap/process/cancel）：按处理号整批撤销一次 9I / 9J / BZ。
    // U8 没有可调用的取消组件：界面「取消操作」自己跑 SQL。桥按它取消这几种处理时执行的 SQL 写（实测核对），
    // 在请求连接的一个事务里：带锁快照整批处理行 → 闸门 → 余额加回（9I / 9J：收付款单行、应收应付单、发票累计核销）→ 删处理行，
    // 提交前核对处理行已删光、每处余额精确回到原值加本批金额，提交后在新连接上确认处理行已不在。
    // 不做「保留线索」的反向插入（bAR2Cancel / bAP2Cancel 为 0 的账套 U8 也不插）。已制单的先走 arap/voucher/delete。
    // 票据处理（9A / 9D / 9E / 9C，PJJ / PJT / PJB / CL）转给 NotesUndo。
    // 红票对冲（9N，HRAR / HPAP）同一流程：应收应付单加回 -(借+贷)（ProcCancelSql.BillRedSql）、发票累计按处理行原样加回
    // （ArapUnwriteoffBill 按处理方式选 ArapRedSql 的取数，核对 累计 = 原值 + 处理行借+贷），收付款单的 9N 拒绝。
    // 坏账处理（HZAR：计提 9F、发生 9G、收回 9H）转给 ArapProcCancelBad。
    internal static class ArapProcCancel
    {
        const decimal Tolerance = 0.005m;

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            ProcCancelAsk ask = ArapProcCancelReq.Parse(ctx.Item.Body);
            // 坏账处理（HZAR）：第二级写入，测试账套名单、权限、整段流程都在 ArapProcCancelBad。
            if (ask.Kind.Bad)
            {
                return ArapProcCancelBad.Run(ctx, ask);
            }
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(PermRegistry.ProcCancelKey(ask.Flag)));
            if (ask.Kind.Notes)
            {
                return NotesUndo.Run(ctx, ask);
            }
            ProcCancelPlan plan = Tran(ctx, ask);
            return Reread(ctx, plan);
        }

        static ProcCancelPlan Tran(WorkContext ctx, ProcCancelAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ProcCancelPlan plan = ArapProcCancelGate.Plan(conn, ask, ctx.Item.Acc);
                Allowed(ctx, plan);
                ArapWriteoffCheck.Guard(conn, delegate { Write(ctx, plan); });
                if (DryRun.Active)
                {
                    Dry(plan, Body(ctx, plan));
                }
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, "已取消" + plan.Kind.Title + " " + plan.CancelNo);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 数据权限：每条处理行的往来单位、部门、业务员，应收一侧按应收的规则（客户）、应付一侧按应付的规则（供应商）。
        static void Allowed(WorkContext ctx, ProcCancelPlan plan)
        {
            PermContext p = PermCheck.Of(ctx);
            foreach (ProcRow row in plan.Rows)
            {
                PermRule rule = PermRegistry.ForKey(PermRegistry.ProcCancelKey(row.Ledger));
                if (!PermCheck.RowAllowed(p, rule, row.Head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        // U8 取消操作的顺序：余额加回（读的是还没删的处理行）→ 删两边；然后核对。票据背书（NotesUndo）对应付一侧也用它。
        internal static void Write(WorkContext ctx, ProcCancelPlan plan)
        {
            object conn = ctx.Conn;
            if (plan.Kind.Restore)
            {
                Restore(ctx, plan);
            }
            foreach (string ledger in plan.Kind.Ledgers)
            {
                ProcCancelSql.Delete(conn, plan, ledger);
            }
            string problem = Mismatch(conn, plan);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "取消" + plan.Kind.Title + "后核对不符，已回滚：" + problem);
            }
        }

        static void Restore(WorkContext ctx, ProcCancelPlan plan)
        {
            object conn = ctx.Conn;
            foreach (string ledger in plan.Kind.Ledgers)
            {
                if (plan.HasRemain(plan.Lines, ledger))
                {
                    ProcCancelSql.CloseBills(conn, plan, ledger);
                }
                if (plan.HasRemain(plan.Bills, ledger))
                {
                    ProcCancelSql.Bills(conn, plan, ledger);
                }
            }
            string label = "取消" + plan.Kind.Title;
            if (plan.HasCo("AR", "2"))
            {
                ArapUnwriteoffBill.Sale(ctx, plan.Kind.Style, plan.CancelNo, label);
            }
            if (plan.HasCo("AP", "0"))
            {
                ArapUnwriteoffBill.Purchase(conn, plan.Kind.Style, plan.CancelNo, label);
            }
        }

        // 处理行删光；收付款单行余额 = 原值 + 加回，且在 0 和金额之间；应收应付单 iRAmount_f = 原值 + 加回。
        static string Mismatch(object conn, ProcCancelPlan plan)
        {
            foreach (string ledger in plan.Kind.Ledgers)
            {
                if (ProcCancelSql.CountRows(conn, ledger, plan.Kind.Style, plan.CancelNo) != 0)
                {
                    return "处理行没有删干净";
                }
            }
            foreach (ProcRemain line in plan.Lines)
            {
                decimal[] now = ProcCancelSql.Line(conn, line);
                if (now == null)
                {
                    return "收付款单 " + line.Code + " 的行读不到";
                }
                if (Math.Abs(now[0] - (line.Before + line.Back)) > Tolerance || !ArapProcCancelGate.Within(now[0], line.Amount, Tolerance))
                {
                    return "收付款单 " + line.Code + " 余额 " + ArapWriteoffGate.Money(now[0]);
                }
            }
            foreach (ProcRemain bill in plan.Bills)
            {
                decimal now = UnwriteoffSql.BillRemain(conn, bill.Ledger, bill.VType, bill.Code);
                if (Math.Abs(now - (bill.Before + bill.Back)) > Tolerance)
                {
                    return "单据 " + bill.Code + " 余额 " + ArapWriteoffGate.Money(now);
                }
            }
            return null;
        }

        // 已提交。新连接（不加 NOLOCK）确认该处理号的行已不在；读不出来或还在都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, ProcCancelPlan plan)
        {
            object conn = null;
            int left = 0;
            try
            {
                conn = ctx.OpenFresh();
                foreach (string ledger in plan.Kind.Ledgers)
                {
                    left += ProcCancelSql.CountRows(conn, ledger, plan.Kind.Style, plan.CancelNo);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapProcCancel " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，处理号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", "取消" + plan.Kind.Title + "已提交，但回读时处理号 " + plan.CancelNo
                    + " 的明细行仍在；请先查往来明细核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan));
        }

        // 预演（rollback）：事务内核对通过后登记涉及的单据，响应体交给 detail，提交钩子随后回滚。
        static void Dry(ProcCancelPlan plan, Dictionary<string, object> body)
        {
            foreach (ProcDoc doc in plan.Docs)
            {
                VoucherKind kind = Kinds.Find(doc.Name);
                if (kind != null && doc.Id > 0)
                {
                    DryRun.Touched(kind, doc.Id);
                }
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set(ArapProcCancelReq.Action, copy);
        }

        internal static Dictionary<string, object> Body(WorkContext ctx, ProcCancelPlan plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx == null ? null : ctx.Item.Acc;
            body["flag"] = plan.Flag;
            body["cancel_no"] = plan.CancelNo;
            body["style"] = plan.Kind.Style;
            body["kind"] = plan.Kind.Name;
            body["ar_rows"] = plan.Count("AR");
            body["ap_rows"] = plan.Count("AP");
            body["items"] = ArapProcCancelBody.Items(plan);
            body["restored"] = ArapProcCancelBody.Restored(plan);
            return body;
        }
    }
}
