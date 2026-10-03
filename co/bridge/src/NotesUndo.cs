using System;
using System.Collections.Generic;

namespace U8Co
{
    // 取消票据处理（arap/process/cancel 的票据分支）：按处理号整批撤销一次票据结算 9A（PJJ）、贴现 9D（PJT）、背书 9E（PJB）、
    // 退回 9C（CL）。U8 在票据管理里撤销，背书另走「取消操作」的 9E 分支；都没有可调用的组件，桥按界面执行的 SQL 写（实测核对），
    // 在请求连接的一个事务里：带锁读处理行、票据、往来明细 → 闸门（未制单、期间未结账、之后没有别的处理、没开保留线索；
    // 退回生成的单据未制单、未被引用）→ 背书先把被背书单位的应付余额加回并删应付明细（同应收冲应付，ArapProcCancel.Write）→
    // 删票据所在账的往来明细 → 退回删生成的单据 → 删票据处理行 → 票据余额加回、清 lAcctID → 分包票据重写可用区间 → 核对；
    // 提交后在新连接上确认处理行、往来明细都已不在。已制单的先走 arap/voucher/delete。
    internal static class NotesUndo
    {
        const decimal Tolerance = 0.005m;

        // 应付票据（PJJAP / CLAP）是第二级写入：入队后再查一次测试账套名单（登录前在 ArapProcCancelReq 已查）。
        public static ApiResult Run(WorkContext ctx, ProcCancelAsk ask)
        {
            NotesProcReq.TestGate(ctx.Item, ask.Kind.Flag);
            NoteUndoPlan plan = Tran(ctx, ask);
            return Reread(ctx, plan);
        }

        static NoteUndoPlan Tran(WorkContext ctx, ProcCancelAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                NoteUndoPlan plan = NotesUndoGate.Plan(conn, ask, ctx.Item.Acc);
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

        // 数据权限：票据所在账的往来明细按该账的规则；背书对方账的明细按对方账的规则。
        static void Allowed(WorkContext ctx, NoteUndoPlan plan)
        {
            PermContext p = PermCheck.Of(ctx);
            List<ProcRow> rows = new List<ProcRow>(plan.Rows);
            if (plan.Side != null)
            {
                rows.AddRange(plan.Side.Rows);
            }
            foreach (ProcRow row in rows)
            {
                PermRule rule = PermRegistry.ForKey(PermRegistry.ProcCancelKey(row.Ledger));
                if (!PermCheck.RowAllowed(p, rule, row.Head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        static void Write(WorkContext ctx, NoteUndoPlan plan)
        {
            object conn = ctx.Conn;
            string style = plan.Kind.Style;
            if (plan.Side != null)
            {
                ArapProcCancel.Write(ctx, plan.Side);
            }
            NotesUndoSql.DeleteDetail(conn, plan.Ledger, style, plan.CancelNo);
            if (plan.Bill != null)
            {
                NotesUndoSql.DeleteBill(conn, plan.Bill, plan.Flag);
            }
            NotesUndoSql.DeleteSub(conn, style, plan.CancelNo);
            NotesUndoSql.RestoreHead(conn, plan.Head.Link, plan.Back, plan.BackLocal);
            if (plan.Ranges != null)
            {
                NotesUndoSql.WriteRanges(conn, plan.Head.Link, plan.Ranges);
            }
            string problem = Mismatch(conn, plan);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "取消" + plan.Kind.Title + "后核对不符，已回滚：" + problem);
            }
        }

        // 处理行、往来明细删光；票据余额 = 原值 + 加回；退回生成的单据已删；分包票据可用区间合计 = 新余额。
        static string Mismatch(object conn, NoteUndoPlan plan)
        {
            string style = plan.Kind.Style;
            if (NotesUndoSql.CountSubs(conn, style, plan.CancelNo) != 0
                || ProcCancelSql.CountRows(conn, plan.Ledger, style, plan.CancelNo) != 0)
            {
                return "处理记录没有删干净";
            }
            NoteUndoHead now = NotesUndoSql.Head(conn, plan.Head.Link);
            decimal want = plan.Head.Remain + plan.Back;
            if (now == null || Math.Abs(now.Remain - want) > Tolerance
                || Math.Abs(now.RemainLocal - (plan.Head.RemainLocal + plan.BackLocal)) > Tolerance)
            {
                return "票据 " + plan.Head.Code + " 余额 " + (now == null ? "读不到" : ArapWriteoffGate.Money(now.Remain));
            }
            if (plan.Bill != null && NotesUndoSql.CountBill(conn, plan.Bill) != 0)
            {
                return "退回生成的单据 " + plan.Bill.Code + " 没有删掉";
            }
            return plan.Ranges == null ? null : RangeMismatch(conn, plan, want);
        }

        static string RangeMismatch(object conn, NoteUndoPlan plan, decimal want)
        {
            decimal sum = 0m;
            foreach (NoteAvailRange r in NotesUndoSql.Ranges(conn, plan.Head.Link))
            {
                sum += r.Amount;
            }
            return Math.Abs(sum - want) > Tolerance ? "票据 " + plan.Head.Code + " 可用区间合计 " + ArapWriteoffGate.Money(sum) : null;
        }

        // 已提交。新连接（不加 NOLOCK）确认处理行和往来明细已不在；读不出来或还在都是 504 outcome_unknown。
        static ApiResult Reread(WorkContext ctx, NoteUndoPlan plan)
        {
            object conn = null;
            int left = 0;
            string style = plan.Kind.Style;
            try
            {
                conn = ctx.OpenFresh();
                left += NotesUndoSql.CountSubs(conn, style, plan.CancelNo);
                foreach (string ledger in plan.Kind.Ledgers)
                {
                    left += ProcCancelSql.CountRows(conn, ledger, style, plan.CancelNo);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "NotesUndo " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，处理号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", "取消" + plan.Kind.Title + "已提交，但回读时处理号 " + plan.CancelNo
                    + " 的记录仍在；请先查票据和往来明细核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan));
        }

        static void Dry(NoteUndoPlan plan, Dictionary<string, object> body)
        {
            if (plan.Side != null)
            {
                foreach (ProcDoc doc in plan.Side.Docs)
                {
                    Touch(doc.Name, doc.Id);
                }
            }
            if (plan.Bill != null)
            {
                Touch(ArapProcCancelGate.DocName(plan.Ledger, plan.Bill.VType), plan.Bill.Id);
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set(ArapProcCancelReq.Action, copy);
        }

        static void Touch(string name, int id)
        {
            VoucherKind kind = name == null ? null : Kinds.Find(name);
            if (kind != null && id > 0)
            {
                DryRun.Touched(kind, id);
            }
        }

        // 响应：同取消操作（cancel_no、style、kind、ar_rows、ap_rows、items、restored），另加 note（票据、加回金额、取消后余额）
        // 和 deleted_bill（退回生成、随取消删除的单据，其余为 null）。
        internal static Dictionary<string, object> Body(WorkContext ctx, NoteUndoPlan plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx == null ? null : ctx.Item.Acc;
            body["flag"] = plan.Flag;
            body["cancel_no"] = plan.CancelNo;
            body["style"] = plan.Kind.Style;
            body["kind"] = plan.Kind.Name;
            body["ar_rows"] = Count(plan, "AR");
            body["ap_rows"] = Count(plan, "AP");
            List<object> items = NoteItems(plan);
            List<object> restored = new List<object>();
            if (plan.Side != null)
            {
                items.AddRange(ArapProcCancelBody.Items(plan.Side));
                restored = ArapProcCancelBody.Restored(plan.Side);
            }
            body["items"] = items;
            body["restored"] = restored;
            Dictionary<string, object> note = new Dictionary<string, object>();
            note["id"] = plan.Head.Id;
            note["code"] = plan.Head.Code;
            note["amount"] = plan.Back;
            note["remaining"] = plan.Head.Remain + plan.Back;
            body["note"] = note;
            body["deleted_bill"] = plan.Bill == null ? null : DeletedBill(plan);
            return body;
        }

        static int Count(NoteUndoPlan plan, string ledger)
        {
            int n = plan.Ledger == ledger ? plan.Rows.Count : 0;
            return n + (plan.Side == null ? 0 : plan.Side.Count(ledger));
        }

        // 票据所在账的明细：票据行（type note）和退回生成的单据行。
        static List<object> NoteItems(NoteUndoPlan plan)
        {
            List<object> items = new List<object>();
            foreach (ProcRow row in plan.Rows)
            {
                bool note = row.VType == "50";
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["ledger"] = row.Ledger;
                one["type"] = note ? "note" : ArapProcCancelGate.DocName(row.Ledger, row.VType) ?? row.VType;
                one["id"] = note ? (object)plan.Head.Id : (plan.Bill == null ? null : (object)plan.Bill.Id);
                one["code"] = note ? plan.Head.Code : row.VCode;
                one["line_id"] = null;
                one["partner"] = CoRows.Col(row.Head, "cDwCode");
                one["debit"] = row.DF;
                one["credit"] = row.CF;
                items.Add(one);
            }
            return items;
        }

        static Dictionary<string, object> DeletedBill(NoteUndoPlan plan)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["type"] = ArapProcCancelGate.DocName(plan.Ledger, plan.Bill.VType) ?? plan.Bill.VType;
            one["id"] = plan.Bill.Id;
            one["code"] = plan.Bill.Code;
            return one;
        }
    }
}
