using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 红票对冲（arap/red_offset，处理方式 9N，号 HRAR… / HPAP…）：同一往来单位的红字单据（行）对冲蓝字单据（行）。
    // 走 U8 自己的组件（测试账套实测可无界面调用）：U8ApCancel.cLsCancel.Init(login, "AR"|"AP", 请求连接) 引用 {0,2}，
    // 然后 AP_JZ_Red(xmlData, ByRef cErrMsg) 返回新处理号；组件在请求连接的事务里写（回滚能撤销），桥照常包 CoTrans。
    // 顺序：Init → 事务：带锁读单据、过闸门（ArapRedGate）、数据权限、记下余额 → AP_JZ_Red → 事务里核对
    // （9N 行逐单据逐行等于本次金额、往来余额、发票累计、应收应付单余额都变了恰好本次金额）→ 提交 → 新连接回读。不符回滚 409。
    // 不制单（arap/process/voucher），取消走 arap/process/cancel。
    internal static class ArapRed
    {
        const decimal Tolerance = 0.005m;

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            RedAsk ask = ArapRedReq.Parse(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.RedOffsetKey(ask.Flag));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            object hx = null;
            RedPlan plan;
            try
            {
                hx = ArapWriteoff.Open(ctx, ask.Flag);
                plan = Tran(ctx, hx, ask, rule);
            }
            finally
            {
                ComUtil.Final(hx);
            }
            return Reread(ctx, plan);
        }

        static RedPlan Tran(WorkContext ctx, object hx, RedAsk ask, PermRule rule)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                RedPlan plan = ArapRedGate.Plan(ctx, ask);
                Allowed(ctx, rule, plan);
                plan.CancelNo = Call(ctx, hx, plan);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                ArapWriteoffCheck.Guard(conn, delegate { Verify(conn, plan); });
                if (DryRun.Active)
                {
                    Dry(plan, Body(ctx, plan));
                }
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, ArapRedRule.Title + " 处理号 " + plan.CancelNo);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 数据权限：每张单据的表头（往来单位、部门、业务员），应收按客户规则、应付按供应商规则；一张不在权限内就 403。
        static void Allowed(WorkContext ctx, PermRule rule, RedPlan plan)
        {
            PermContext p = PermCheck.Of(ctx);
            foreach (RedDoc d in plan.Docs)
            {
                if (!PermCheck.RowAllowed(p, rule, d.Core.Head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        // AP_JZ_Red：引用 {1}（cErrMsg）。返回的号不是 HRAR / HPAP 加数字时 409 u8_rejected，带回 cErrMsg 原文。
        static string Call(WorkContext ctx, object hx, RedPlan plan)
        {
            object[] args = new object[] { ArapRedRule.Xml(plan), "" };
            object ret;
            try
            {
                ret = ComUtil.CallRef(hx, "AP_JZ_Red", args, new int[] { 1 });
            }
            catch (COMException ex)
            {
                CoRows.Note(ctx.Item, "cLsCancel.AP_JZ_Red " + ex.Message);
                throw new BridgeException(409, "u8_rejected", ArapCo.Said(ex.Message, "U8 拒绝了红票对冲"));
            }
            string no = Values.Text(ret).Trim();
            string err = Values.Text(args[1]).Trim();
            CoRows.Note(ctx.Item, "cLsCancel.AP_JZ_Red " + no + (err.Length > 0 ? " / " + err : string.Empty));
            if (!ArapRedRule.NoValid(plan.Flag, no))
            {
                throw new BridgeException(409, "u8_rejected", err.Length > 0 ? "U8 拒绝了红票对冲：" + err
                    : "U8 没有给出有效的处理号（AP_JZ_Red 返回 " + (no.Length > 0 ? no : "空") + "）");
            }
            return no;
        }

        static void Verify(object conn, RedPlan plan)
        {
            string problem = Rows9N(conn, plan) ?? Balances(conn, plan);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", ArapRedRule.Title + "后核对不符，已回滚：" + problem);
            }
        }

        // 9N 行：只在本侧的往来明细，都是单据自身，按（单据、行）汇总的带符号金额等于本次金额（红 +、蓝 -）。不符返回原因。
        internal static string Rows9N(object conn, RedPlan plan)
        {
            string other = plan.Flag == "AP" ? "AR" : "AP";
            if (ProcCancelSql.CountRows(conn, other, ArapRedRule.Style, plan.CancelNo) != 0)
            {
                return "处理号 " + plan.CancelNo + " 在另一方的往来明细里也有行";
            }
            int odd;
            Dictionary<string, decimal[]> got = ArapRedSql.Written(conn, plan.Flag, plan.CancelNo, out odd);
            if (got.Count == 0)
            {
                return "没有处理号 " + plan.CancelNo + " 的往来明细";
            }
            if (odd != 0)
            {
                return "处理行的对方单据不是单据自身";
            }
            string key = ArapRedRule.Diff(ArapRedRule.Expected(plan), got, Tolerance);
            return key == null ? null : "处理行金额与请求不符（" + key + "）";
        }

        // 往来余额（带符号）= 写前 + 变化；发票累计 = 写前 + 变化；应收应付单表头余额的绝对值减少了本次合计。
        static string Balances(object conn, RedPlan plan)
        {
            foreach (RedDoc d in plan.Docs)
            {
                decimal sum = 0m;
                foreach (RedPiece p in d.Pieces)
                {
                    sum += p.F;
                    decimal now = TransferSql.Remain(conn, d.Core, p.Line, plan.Partner).RemainF;
                    if (Math.Abs(now - (p.BeforeF + ArapRedRule.Delta(d.Red, p.F))) > Tolerance)
                    {
                        return d.Label + " 余额 " + ArapTransferRule.Money(now);
                    }
                    if (p.Line > 0 && !AccOk(conn, d, p))
                    {
                        return d.Label + " 行 " + p.Line + " 的累计核销不符";
                    }
                }
                if (ArapMergeReq.IsBill(d.Core.Ask.Type))
                {
                    decimal head = UnwriteoffSql.BillRemain(conn, d.Core.Side, d.Core.Ask.Type, d.Core.Ask.Code);
                    if (Math.Abs(Math.Abs(head) - (Math.Abs(d.HeadBefore) - sum)) > Tolerance)
                    {
                        return d.Label + " 表头余额 " + ArapTransferRule.Money(head);
                    }
                }
            }
            return null;
        }

        static bool AccOk(object conn, RedDoc d, RedPiece p)
        {
            decimal[] now = TransferSql.InvoiceAcc(conn, d.Core.Ask.Type, p.Line);
            return Math.Abs(now[0] - (p.AccA + ArapRedRule.AccDelta(d.Red, p.F))) <= Tolerance
                && Math.Abs(now[1] - (p.AccB + ArapRedRule.AccDelta(d.Red, p.N))) <= Tolerance;
        }

        // 已提交。新连接（不加 NOLOCK）确认该号的 9N 行与本次一致；读不出来或对不上都是 504 outcome_unknown（先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, RedPlan plan)
        {
            object conn = null;
            string problem;
            try
            {
                conn = ctx.OpenFresh();
                problem = Rows9N(conn, plan);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapRed " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，处理号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (problem != null)
            {
                throw new BridgeException(504, "outcome_unknown", ArapRedRule.Title + "已提交但回读不符（处理号 " + plan.CancelNo
                    + "）：" + problem + "；请先查往来明细核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan));
        }

        internal static Dictionary<string, object> Body(WorkContext ctx, RedPlan plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx == null ? null : ctx.Item.Acc;
            body["flag"] = plan.Flag;
            body["style"] = ArapRedRule.Style;
            body["cancel_no"] = plan.CancelNo;
            body["date"] = plan.Date;
            body["partner"] = plan.Partner;
            body["currency"] = plan.Currency;
            body["amount"] = plan.Sum;
            body["red_rows"] = RowsOut(plan.Red);
            body["blue_rows"] = RowsOut(plan.Blue);
            return body;
        }

        // 每张单据的每一行：本次金额（原币、本币）和对冲后的余额（原币，带符号：红字为负、蓝字为正）。
        static List<object> RowsOut(List<RedDoc> docs)
        {
            List<object> rows = new List<object>();
            foreach (RedDoc d in docs)
            {
                foreach (RedPiece p in d.Pieces)
                {
                    Dictionary<string, object> one = new Dictionary<string, object>();
                    one["type"] = d.Core.Ask.Type;
                    one["id"] = d.Core.Ask.Code;
                    one["doc_id"] = d.Core.Id;
                    one["line_id"] = p.Line > 0 ? (object)p.Line : null;
                    one["amount"] = p.F;
                    one["amount_native"] = p.N;
                    one["remaining"] = p.BeforeF + ArapRedRule.Delta(d.Red, p.F);
                    rows.Add(one);
                }
            }
            return rows;
        }

        // 预演（rollback）：U8 写过、核对过之后，把处理号、金额和涉及的单据交给 detail，提交钩子随后回滚。
        static void Dry(RedPlan plan, Dictionary<string, object> body)
        {
            foreach (RedDoc d in plan.Docs)
            {
                VoucherKind kind = Kinds.Find(d.Core.Kind);
                if (kind != null && d.Core.Id > 0)
                {
                    DryRun.Touched(kind, d.Core.Id);
                }
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set(ArapRedReq.Action, copy);
        }
    }
}
