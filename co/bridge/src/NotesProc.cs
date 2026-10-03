using System;
using System.Collections.Generic;

namespace U8Co
{
    // 票据处理（notes/process）：托收 / 结算 9A、贴现 9D、背书冲应付 9E，加退回 9C 和应付票据的结算、退回（第二级写入，
    // 只对测试账套开放，NotesProcReq.TestGate）。U8 没有可调用的处理组件：
    // U8 票据管理的结算、贴现、背书、退回由界面直接写表，桥按界面写入的结果写 SQL（实测核对），在请求连接的一个事务里：
    // 带锁读票据、过闸门（NotesProcGate）→ 数据权限 → 取号（Ap_Proc_CancelNo PJJ / PJT / PJB / CL）→ 背书先写应付一侧（NotesProcEndorse）、
    // 退回先经 UFAPBO SaveVouch 生成应收单、补写标记并写它的往来明细行（NotesProcReturn；组件在开事务前打开）→
    // 票据处理表 AP_Note_Sub 一行 → 票据余额 → 往来明细 50 行 → 分包票据的可用区间 → 事务里核对，提交后在新连接上确认行数。
    // U8 界面随后自动制单（bPZMakePZNow），本接口不制单，制单走 arap/process/voucher（处理号）。
    internal static class NotesProc
    {
        const decimal Tolerance = 0.005m;

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            NotesProcAsk ask = NotesProcReq.Parse(ctx.Item.Body);
            NotesProcReq.TestGate(ctx.Item, ask.Flag);
            PermRule rule = PermRegistry.ForKey(PermRegistry.NotesProcKey(ask.Flag, ask.Op));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            // 退回生成应收单（应付单）的 UFAPBO 组件要在开事务之前打开、取模板（同票据登记的收款单）。
            NotesProcReturnBo saver = null;
            try
            {
                saver = ask.Op == "return" ? NotesProcReturnBo.Open(ctx, ask.Flag) : null;
                NotesProcPlan plan = Tran(ctx, ask, rule, saver);
                return Reread(ctx, plan);
            }
            finally
            {
                if (saver != null)
                {
                    saver.Dispose();
                }
            }
        }

        static NotesProcPlan Tran(WorkContext ctx, NotesProcAsk ask, PermRule rule, NotesProcReturnBo saver)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                NotesProcPlan plan = NotesProcGate.Plan(ctx, ask);
                plan.Saver = saver;
                if (!PermCheck.RowAllowed(PermCheck.Of(ctx), rule, NotesProcSql.PermRow(plan.Note)))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
                NotesProcEndorse.Allowed(ctx, plan);
                ArapWriteoffCheck.Guard(conn, delegate { Write(ctx, plan); });
                if (DryRun.Active)
                {
                    Dry(plan, Body(ctx, plan));
                }
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, NotesProcRule.Title(plan.Op) + " 处理号 " + plan.CancelNo);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static void Write(WorkContext ctx, NotesProcPlan plan)
        {
            object conn = ctx.Conn;
            Number(conn, plan);
            string problem = First(ctx, plan);
            if (problem == null)
            {
                NotesProcSql.InsertSub(conn, plan);
                NotesProcSql.UpdateHead(conn, plan);
                NotesProcSql.InsertDetail(conn, plan);
                if (plan.Range != null)
                {
                    NotesProcSql.RewriteAvail(conn, plan);
                }
                problem = Mismatch(conn, plan);
            }
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", NotesProcRule.Title(plan.Op) + "后核对不符，已回滚：" + problem);
            }
        }

        // 票据处理行之前要先写的另一侧：背书的应付单据、退回的应收单。不符返回原因。
        static string First(WorkContext ctx, NotesProcPlan plan)
        {
            if (plan.Endorse)
            {
                return NotesProcEndorse.Write(ctx, plan);
            }
            return plan.Return ? NotesProcReturn.Write(ctx, plan) : null;
        }

        static void Number(object conn, NotesProcPlan plan)
        {
            string no = NotesProcSql.Number(conn, plan.Op, plan.Flag);
            if (!NotesProcRule.NoValid(plan.Op, plan.Flag, no))
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有给出有效的处理号（Ap_Proc_CancelNo 返回 " + (no ?? "空") + "）");
            }
            if (NotesProcSql.Used(conn, plan.Style, no))
            {
                throw new BridgeException(409, "u8_rejected", "处理号 " + no + " 已被占用，请检查 Ap_CancelNo");
            }
            plan.CancelNo = no;
        }

        // 事务里的核对：票据处理表一行、往来明细的处理行数（背书另有应付各行，退回另有应收（应付）单一行和单据表头表体）、票据余额 = 写前 - 本次、分包票据的可用区间合计 = 新余额。
        static string Mismatch(object conn, NotesProcPlan plan)
        {
            string problem = Counts(conn, plan);
            if (problem != null)
            {
                return problem;
            }
            decimal remain = NotesProcSql.Remain(conn, plan.Note.Id);
            if (Math.Abs(remain - (plan.Note.Remain - plan.Amount)) > Tolerance)
            {
                return "票据余额 " + NotesProcRule.Money(remain);
            }
            if (plan.Range != null)
            {
                decimal[] avail = NotesProcSql.AvailTotal(conn, plan.Note.Link);
                if (Math.Abs(avail[0] - remain) > Tolerance || (int)avail[1] != plan.Left.Count)
                {
                    return "可用子票区间合计 " + NotesProcRule.Money(avail[0]);
                }
            }
            return null;
        }

        // 不符返回原因。提交后的回读也用它。
        static string Counts(object conn, NotesProcPlan plan)
        {
            if (NotesProcSql.SubRows(conn, plan.Style, plan.CancelNo) != 1)
            {
                return "票据处理记录数不对";
            }
            if (TransferSql.CountRows(conn, plan.Flag, plan.Style, plan.CancelNo) != (plan.Return ? 2 : 1))
            {
                return (plan.Flag == "AP" ? "应付" : "应收") + "往来明细的处理行数不对";
            }
            if (plan.Return && NotesProcReturnSql.BillRows(conn, plan) != 2)
            {
                return "退回生成的" + NotesProcReturn.BillTitle(plan.Flag) + " " + plan.BillCode + " 不完整";
            }
            if (plan.Endorse && TransferSql.CountRows(conn, "AP", plan.Style, plan.CancelNo) != NotesProcEndorse.ApRows(plan))
            {
                return "应付往来明细的处理行数不对";
            }
            return null;
        }

        // 已提交。新连接（不加 NOLOCK）确认处理表和往来明细上该号的行数；读不出来或对不上都是 504 outcome_unknown（先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, NotesProcPlan plan)
        {
            object conn = null;
            string problem;
            try
            {
                conn = ctx.OpenFresh();
                problem = Counts(conn, plan);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "NotesProc " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，处理号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (problem != null)
            {
                throw new BridgeException(504, "outcome_unknown", NotesProcRule.Title(plan.Op) + "已提交但回读不符（处理号 "
                    + plan.CancelNo + "）：" + problem + "；请先查票据处理记录核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan));
        }

        internal static Dictionary<string, object> Body(WorkContext ctx, NotesProcPlan plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = plan.Flag;
            body["op"] = plan.Op;
            body["style"] = plan.Style;
            body["cancel_no"] = plan.CancelNo;
            body["date"] = plan.Date;
            body["note"] = NoteOut(plan);
            body["amount"] = plan.Amount;
            body["remaining"] = plan.Note.Remain - plan.Amount;
            body["sub_start"] = plan.Range == null ? null : (object)plan.Range.Start;
            body["sub_end"] = plan.Range == null ? null : (object)plan.Range.End;
            body["digest"] = plan.Digest;
            Extra(body, plan);
            return body;
        }

        static Dictionary<string, object> NoteOut(NotesProcPlan plan)
        {
            Dictionary<string, object> note = new Dictionary<string, object>();
            note["id"] = plan.Note.Id;
            note["code"] = plan.Note.Code;
            note["partner"] = plan.Note.Partner;
            note["partner_name"] = plan.PartnerName;
            return note;
        }

        // 按 op 的字段：结算、贴现的银行科目和名称；贴现的净额、费用、利息、贴现率；背书的供应商和应付各行；
        // 退回生成的应收单 r0_id / r0_code（应付票据是应付单 p0_id / p0_code）。
        static void Extra(Dictionary<string, object> body, NotesProcPlan plan)
        {
            if (plan.Return)
            {
                string key = NotesProcReturnSql.BillType(plan.Flag).ToLowerInvariant();
                body[key + "_id"] = plan.BillId > 0 ? (object)plan.BillId : null;
                body[key + "_code"] = plan.BillCode;
                return;
            }
            if (plan.Endorse)
            {
                body["vendor"] = plan.Vendor;
                body["ap_rows"] = NotesProcEndorse.RowsOut(plan);
                return;
            }
            body["bank_code"] = plan.BankCode;
            body["bank_name"] = plan.BankName;
            if (plan.Op == "discount")
            {
                body["net"] = plan.Net;
                body["expense"] = plan.Expense;
                body["interest"] = plan.Interest;
                body["rate"] = plan.Rate;
            }
        }

        // 预演（rollback）：写和核对都在事务里跑过之后，把处理号、金额交给 detail，提交钩子随后回滚。背书涉及的应付单据登记为 Touched。
        static void Dry(NotesProcPlan plan, Dictionary<string, object> body)
        {
            if (plan.Ap != null)
            {
                foreach (TransferDoc d in plan.Ap.Ap)
                {
                    VoucherKind kind = Kinds.Find(d.Kind);
                    if (kind != null && d.Id > 0)
                    {
                        DryRun.Touched(kind, d.Id);
                    }
                }
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set(NotesProcReq.Action, copy);
        }
    }
}
