using System;
using System.Collections.Generic;

namespace U8Co
{
    // 删除票据（notes/delete，应收票据；应付票据，第二级写入、只对测试账套开放）。只删「登记了、还什么都没做」的票据：不是期初、没有处理记录（AP_Note_Sub）、
    // 没换过票、余额等于票面，分包票据的可用子票区间仍是登记时的整段（非分包没有可用区间）；
    // 登记生成的收款单（应付票据是付款单）未审核、未制单、未核销、没有往来明细、不在审批流中。U8 自己登记的分包票据同样适用。
    // 收付款单是票据来源（vouchers/delete 拒绝删除来自票据的收付款单），这里专门删除。不调用 NoteManageAR / AP 的 DeletePJ（自行提交，
    // 无界面时可能弹确认框挂起），照它的做法在请求连接的一个事务里删收付款单（clsCloseBill.DeleteVouch）、保证金、付款申请明细和票据，
    // 核对后提交（NotesRegLink.Drop）；预演在提交钩子回滚。
    internal static class NotesRegDel
    {
        public static ApiResult Delete(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            NoteDelAsk ask = NotesRegReq.ParseDelete(ctx.Item.Body);
            NotesRegReq.TestGate(ctx.Item, ask.Flag);
            PermRule rule = PermRegistry.ForKey(PermRegistry.NotesRegKey(true, ask.Flag));
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, rule);
            NoteRow note = NotesRegSql.Find(ctx.Conn, ask.Flag, ask.NoteNo, ask.NoteId);
            if (note == null)
            {
                throw new BridgeException(404, "not_found", "票据不存在");
            }
            if (!PermCheck.RowAllowed(perm, rule, NotesRegReq.PermRow(note.Partner, note.Dept, note.Person)))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
            RefuseNote(note);
            string range = NoteSplit.RangeRefusal(NotesProcSql.Avail(ctx.Conn, note.Link), note);
            if (range.Length > 0)
            {
                throw State("票据 " + note.Code + " " + range + "，不能删除，请在 U8 客户端核对");
            }
            VoucherKind kind = Kinds.Find(NotesRegReq.CloseKind(ask.Flag));
            ArapSpec spec = ArapReq.Spec(kind);
            ArapDoc receipt = note.CloseId > 0 ? ArapSql.Head(ctx.Conn, kind, spec, note.CloseId) : null;
            if (receipt != null)
            {
                RefuseReceipt(ctx.Conn, kind, spec, receipt, note);
            }
            DryRun.Set(NotesRegReq.DeleteAction, Planned(note, receipt));
            NotesRegLink.Drop(ctx, ask.Flag, spec, receipt, note);
            return Gone(ctx, ask.Flag, note, receipt);
        }

        static void RefuseNote(NoteRow note)
        {
            if (note.Opening)
            {
                throw State("期初票据不能经接口删除，请在 U8 客户端处理");
            }
            if (note.Subs > 0)
            {
                throw State("票据已有处理记录（结算、贴现、背书等），不能删除；请先取消处理");
            }
            if (note.Change != 0)
            {
                throw State("票据已换票，不能删除");
            }
            if (note.Remain != note.Amount)
            {
                throw State("票据余额与票面金额不等（已部分处理），不能删除");
            }
        }

        static void RefuseReceipt(object conn, VoucherKind kind, ArapSpec spec, ArapDoc receipt, NoteRow note)
        {
            string label = "票据生成的" + NotesReg.CloseTitle(spec.Flag) + " " + receipt.Code;
            if (!NoteSplit.Matches(receipt.Col("note_no"), note))
            {
                throw State(label + " 的票据号与本票据不一致，请在 U8 中核对");
            }
            PurchaseCo.RefuseFlow(conn, kind, receipt.Row);
            if (receipt.Col("verifier").Length > 0 || receipt.Col("verify_date").Length > 0)
            {
                throw State(label + " 已审核，不能删除票据（请先弃审：vouchers/verify action=unverify）");
            }
            if (receipt.Col("voucher").Length > 0)
            {
                throw State(label + " 已制单，不能删除票据");
            }
            if (ArapSql.Settled(receipt))
            {
                throw State(label + " 已核销，不能删除票据");
            }
            if (ArapSql.Processed(conn, spec, receipt, false))
            {
                throw State(label + " 已有往来明细，不能删除票据");
            }
        }

        // 已提交。新连接上确认票据和以它为来源的收付款单都不在了；读不到或还在 504。
        static ApiResult Gone(WorkContext ctx, string flag, NoteRow note, ArapDoc receipt)
        {
            int left = NotesRegSql.Left(ctx, note);
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", "票据 " + note.Code + "（主键 " + note.Id + "）的删除已提交，但"
                    + (left < 0 ? "未能回读确认" : "回读时票据或它的" + NotesReg.CloseTitle(flag) + "仍在") + "，请先查询该票据，不要直接重试");
            }
            Dictionary<string, object> body = NotesReg.Out(ctx, flag, note.Id, note.Code, receipt == null ? 0 : receipt.Id,
                receipt == null ? null : receipt.Code);
            NoteSplit.Put(body, note.Split, note.Start, note.End);
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Planned(NoteRow note, ArapDoc receipt)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["note_id"] = note.Id;
            d["note_no"] = note.Code;
            d["amount"] = note.Amount;
            NoteSplit.Put(d, note.Split, note.Start, note.End);
            d["receipt_id"] = receipt == null ? null : (object)receipt.Id;
            d["receipt_code"] = receipt == null ? null : receipt.Code;
            return d;
        }

        static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
