using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 应收 / 应付并账（arap/merge，U8 的「并账」BZ）：把若干张发票、应收单（应付单）在往来单位 from 名下的余额并到 to 名下。
    // U8 没有可调用的并账组件：界面自己取号、写往来明细。桥按界面执行的 SQL 写（ArapMergeSql，实测核对），
    // 在请求连接的一个事务里：闸门（ArapMergeGate）→ 数据权限 → Ap_Proc_CancelNo 取 BZAR… / BZAP… 号 → 每行一对 ± 往来明细，
    // 提交前核对 from、to 两边每行的余额都精确变动了并走的数、行数对得上；提交后在新连接上确认这些行都在。
    // 不制单（arap/process/voucher）、不改单据表头和发票累计；取消并账是 arap/process/cancel。
    internal static class ArapMerge
    {
        const decimal Tolerance = 0.005m;

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            MergeAsk ask = ArapMergeReq.Parse(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.MergeKey(ask.Flag));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            MergePlan plan = Tran(ctx, ask, rule);
            return Reread(ctx, plan);
        }

        static MergePlan Tran(WorkContext ctx, MergeAsk ask, PermRule rule)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                MergePlan plan = ArapMergeGate.Plan(conn, ask, ctx.Item.Date, ctx.Item.Acc, ctx.OperatorName);
                Allowed(ctx, rule, plan);
                ArapWriteoffCheck.Guard(conn, delegate { Write(conn, plan); });
                if (DryRun.Active)
                {
                    Dry(plan, Body(ctx, plan));
                }
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, "并账号 " + plan.CancelNo);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 数据权限：各单据表头，以及并出、并入两个往来单位（部门、业务员不控）。一张不在权限内就 403。
        static void Allowed(WorkContext ctx, PermRule rule, MergePlan plan)
        {
            List<Dictionary<string, object>> heads = new List<Dictionary<string, object>>(plan.Heads);
            heads.Add(PartnerRow(plan.To));
            ArapWriteoffCheck.Allowed(ctx, rule, PartnerRow(plan.From), heads);
        }

        static Dictionary<string, object> PartnerRow(string code)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["cDwCode"] = code;
            return row;
        }

        // U8 并账的写入顺序：取号 → 逐行插出方、入方。然后在事务里核对。
        static void Write(object conn, MergePlan plan)
        {
            plan.CancelNo = ArapMergeSql.Number(conn, plan.Flag);
            foreach (MergeLine line in plan.Lines)
            {
                ArapMergeSql.Insert(conn, plan, line, false);
                ArapMergeSql.Insert(conn, plan, line, true);
            }
            string problem = Mismatch(conn, plan);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "并账后核对不符，已回滚：" + problem);
            }
        }

        // 该号下正好 2 × 行数条；每行 from 余额 = 原值 - 并走数、to 余额 = 原值 + 并走数（原币）。
        static string Mismatch(object conn, MergePlan plan)
        {
            if (ArapMergeSql.Count(conn, plan.Flag, plan.CancelNo) != plan.Lines.Count * 2)
            {
                return "并账行数不对（单据的审核行可能缺失）";
            }
            foreach (MergeLine line in plan.Lines)
            {
                decimal from = ArapMergeSql.Balance(conn, plan.Flag, line, plan.From);
                decimal to = ArapMergeSql.Balance(conn, plan.Flag, line, plan.To);
                if (Math.Abs(from - (line.BalF - line.MoveF)) > Tolerance || Math.Abs(to - (line.ToBefore + line.MoveF)) > Tolerance)
                {
                    return line.Kind.Title + " " + line.Code + " 余额 " + ArapWriteoffGate.Money(from) + " / " + ArapWriteoffGate.Money(to);
                }
            }
            return null;
        }

        // 已提交。新连接（不加 NOLOCK）确认该号的行都在；读不出来或行数不对都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, MergePlan plan)
        {
            object conn = null;
            int rows;
            try
            {
                conn = ctx.OpenFresh();
                rows = ArapMergeSql.Count(conn, plan.Flag, plan.CancelNo);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapMerge " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，并账号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (rows != plan.Lines.Count * 2)
            {
                throw new BridgeException(504, "outcome_unknown", "并账已提交，但回读时并账号 " + plan.CancelNo
                    + " 的明细行数不符；请先查往来明细核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan));
        }

        static Dictionary<string, object> Body(WorkContext ctx, MergePlan plan)
        {
            List<object> rows = new List<object>();
            foreach (MergeLine line in plan.Lines)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = line.Type;
                one["id"] = line.Code;
                one["line_id"] = line.Line > 0 ? (object)line.Line : null;
                one["amount"] = line.MoveF;
                one["amount_native"] = line.MoveN;
                one["from_remaining"] = line.BalF - line.MoveF;
                one["to_remaining"] = line.ToBefore + line.MoveF;
                rows.Add(one);
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = plan.Flag;
            body["cancel_no"] = plan.CancelNo;
            body["date"] = plan.Date;
            body["from"] = plan.From;
            body["to"] = plan.To;
            body["currency"] = plan.Currency;
            body["digest"] = plan.Digest;
            body["amount"] = plan.Total;
            body["rows"] = rows;
            return body;
        }

        // 预演（rollback）：事务内核对通过之后把响应体交给 detail（键 merge），登记涉及的单据；提交钩子随后回滚，号一并撤销。
        static void Dry(MergePlan plan, Dictionary<string, object> body)
        {
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (MergeLine line in plan.Lines)
            {
                VoucherKind kind = Kinds.Find(line.Kind.Name);
                if (kind != null && line.DocId > 0 && seen.Add(line.Kind.Name + ":" + line.DocId.ToString(CultureInfo.InvariantCulture)))
                {
                    DryRun.Touched(kind, line.DocId);
                }
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set("merge", copy);
        }
    }
}
