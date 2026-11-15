using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 票据与它的收款单（48；应付票据是付款单 49）：登记、删除都在请求连接的一个事务里（票据行是桥的 SQL，收付款单是 UFAPBO，
    // 同收付款单新增、删除，可回滚）。
    internal static class NotesRegLink
    {
        // 登记，顺序同 U8：加锁复查票据号 → 写票据行（NoteInsert）→ 收付款单模板、表头、表体同 vouchers/create（ArapCo.Create）SaveVouch
        // → 收付款单标成票据来源、票据回写 iCloseID → 核对（NotesRegSql.Tie）。不符回滚（409 u8_rejected），票据号被占 409 state_mismatch。
        // 返回新收付款单主键，noteId 是新票据主键。
        public static int Create(WorkContext ctx, NoteRegPlan plan, out int noteId)
        {
            VoucherKind kind = Kinds.Find(NotesRegReq.CloseKind(plan.Ask.Flag));
            ArapSpec spec = ArapReq.Spec(kind);
            ArapBo bo = null;
            object[] doms = new object[2];
            int receipt = 0;
            int note = 0;
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                bo = ArapBo.Open(ctx, spec);
                ArapCo.Template(ctx, bo, spec, doms);
                // 票据科目是受控科目，组件保存时表头带它 U8 会拒（「此处不应为受控科目」）；DOM 里不写，保存后由 MarkSql 补。
                plan.Receipt.Head.Remove("ccode");
                ArapDom.FillHead(doms[0], spec, plan.Receipt, ctx.Session.OperatorName);
                ArapDom.FillBody(doms[1], spec, plan.Receipt);
                ArapBo saver = bo;
                ArapCo.InTrans(ctx, "NoteInsert+SaveVouch", delegate(out string m)
                {
                    note = AddNote(ctx, plan);
                    if (!saver.Save(doms, out m))
                    {
                        return false;
                    }
                    receipt = NewId(ctx, kind, spec, doms[0]);
                    DocMark.Created(ctx.Conn, kind, receipt, "");
                    m = NotesRegSql.Tie(ctx.Conn, plan, note, receipt);
                    DryRun.Set("note_id", note);
                    return m.Length == 0;
                });
                noteId = note;
                return receipt;
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ArapCo.Close(bo);
            }
        }

        // 事务里写票据行。写之前带更新锁复查票据号（检查之后 U8 客户端可能登记了同号票据）。
        static int AddNote(WorkContext ctx, NoteRegPlan plan)
        {
            string taken = NotesRegSql.Taken(ctx.Conn, plan.Ask.NoteNo, true);
            if (taken != null)
            {
                throw new BridgeException(409, "state_mismatch", NotesRegGate.TakenMessage(taken, plan.Ask.NoteNo));
            }
            int id = NotesRegSql.Insert(ctx.Conn, plan);
            if (id <= 0)
            {
                throw new BridgeException(409, "u8_rejected", "写入票据后没有按票据号读到唯一的一张，已回滚");
            }
            CoRows.Note(ctx.Item, "AP_Note " + id.ToString(CultureInfo.InvariantCulture));
            return id;
        }

        // SaveVouch 把新 iID、单号写回表头 DOM；没有主键时按单号在本连接（事务内）找。
        static int NewId(WorkContext ctx, VoucherKind kind, ArapSpec spec, object headDom)
        {
            List<Dictionary<string, object>> rows = Rows.FromDom(headDom, 1);
            if (rows == null || rows.Count == 0)
            {
                return 0;
            }
            int id = CoRows.AsId(CoRows.Col(rows[0], kind.IdColumn));
            string code = CoRows.Col(rows[0], kind.CodeColumn);
            if (id <= 0 && code.Length > 0)
            {
                id = ArapSql.IdByCode(ctx.Conn, kind, spec, code);
            }
            return id;
        }

        // 删除，同 U8 的 DeletePJ：锁住票据并确认没被改过 → 删收付款单（receipt 为 null 表示 iCloseID 指向的单据已不存在）
        // → 删保证金、付款申请明细和票据 → 核对（NotesRegSql.Remove）。不符回滚。
        public static void Drop(WorkContext ctx, string flag, ArapSpec spec, ArapDoc receipt, NoteRow note)
        {
            ArapBo bo = null;
            try
            {
                bo = receipt == null ? null : ArapBo.Open(ctx, spec);
                ArapBo runner = bo;
                ArapCo.InTrans(ctx, receipt == null ? "NoteDelete" : "DeleteVouch+NoteDelete", delegate(out string m)
                {
                    if (!NotesRegSql.Hold(ctx.Conn, note))
                    {
                        throw new BridgeException(409, "state_mismatch", "票据 " + note.Code + " 已被修改或删除，请重新读取后再删除");
                    }
                    m = "";
                    // U8 的收付款单组件不删票据生成的单据（「票据生成的收款单不能删除」）：同一事务里先去掉来源标记，删不成整体回滚。
                    if (runner != null)
                    {
                        NotesRegSql.Untie(ctx.Conn, flag, receipt.Id);
                    }
                    if (runner != null && !runner.Run("DeleteVouch", ArapCond.Delete(spec, receipt), out m))
                    {
                        return false;
                    }
                    m = NotesRegSql.Remove(ctx.Conn, note);
                    return m.Length == 0;
                });
            }
            finally
            {
                ArapCo.Close(bo);
            }
        }
    }
}
