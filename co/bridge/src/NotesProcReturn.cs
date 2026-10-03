using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 票据退回（9C）：U8 的 Bill_Return_Ticket 把票据（分包票据是它剩下的全部可用子票区间）退还客户，另生成一张应收单（R0）重新挂上应收款。
    // 写法照 U8：取号 CLAR → 应收单经 UFAPBO clsAPVouch.SaveVouch 生成（同 U8 的 Bill_Return_Ticket，主键和单号由 U8 分配）
    // → 补写处理标记和审核列（NotesProcReturnSql.Stamp）→ 往来明细的应收单借方行（本类）→ 票据处理行、票据余额、票据 50 贷方行、
    // 可用区间（与其他处理同一套，NotesProc）。登记时的收款单（48）不动、不红冲；应收单不走审核（没有审核产生的往来明细行），iRAmount 为 0。
    // 制单走 arap/process/voucher，取消走 arap/process/cancel（删掉这张应收单）。
    // 应付票据（CLAP）同一流程推断：退还供应商，生成应付单 P0（模板卡片 AP04），往来控制科目取登记付款单（49）的应付款行。
    internal static class NotesProcReturn
    {
        const int CodeMax = 30;

        // 闸门（事务里、带锁读票据之后）：之后没有别的处理、往来控制科目、应收（应付）单模板、补写要用的列。
        public static void Plan(object conn, NotesProcPlan plan)
        {
            NoteHead note = plan.Note;
            string bill = BillTitle(plan.Flag);
            string side = plan.Flag == "AP" ? "应付" : "应收";
            string later = NotesProcReturnSql.Later(conn, note.Link, plan.Date);
            if (later != null)
            {
                throw NotesProcGate.State("票据 " + note.Code + " 在 " + later.Trim() + " 还有处理，退回日期不能早于它");
            }
            plan.CtrlKm = NotesProcReturnSql.ReceiptKm(conn, note.CloseId);
            if (plan.CtrlKm.Length == 0)
            {
                plan.CtrlKm = NotesRegSql.Km(conn, plan.Flag, "kzkm", plan.Year, note.Currency);
            }
            if (plan.CtrlKm.Length == 0)
            {
                throw NotesProcGate.State("取不到票据 " + note.Code + " 的" + side + "控制科目（登记" + NotesReg.CloseTitle(plan.Flag) + "的"
                    + side + "款行和" + side + "基本科目都没有），请在 U8 客户端处理");
            }
            plan.VtId = NotesProcReturnSql.VtId(conn, plan.Flag);
            if (plan.VtId <= 0)
            {
                throw NotesProcGate.State("本账套没有" + bill + "的单据模板（卡片 " + NotesProcReturnSql.Card(plan.Flag) + "），请在 U8 客户端处理");
            }
            if (!NotesProcReturnSql.StampReady(conn))
            {
                throw NotesProcGate.State("本账套的" + bill + "表结构与 U8 标准表结构不同（缺少处理标记或审核列），请在 U8 客户端退回");
            }
        }

        // 分包票据的退回要退回全部可用子票区间：U8 退回之后票据就不能再做别的处理（NotesProcSql.Returned），部分退回会让剩下的区间无法处理。
        internal static void WholeRemainder(NotesProcPlan plan)
        {
            if (plan.Range != null && plan.Left != null && plan.Left.Count > 0)
            {
                throw NotesProcGate.State("分包票据退回须退回全部可用区间（票据 " + plan.Note.Code + " 可用 "
                    + NotesProcRule.Money(plan.Note.Remain) + "，本次 " + NotesProcRule.Money(plan.Amount) + "）；可用区间不止一段时请在 U8 客户端处理");
            }
        }

        // 退回生成的单据名称：应收单（R0）/ 应付单（P0）。
        internal static string BillTitle(string flag)
        {
            return flag == "AP" ? "应付单" : "应收单";
        }

        // 在取了处理号之后、票据处理行之前调用：SaveVouch 生成应收（应付）单，找到它、补写处理标记和审核列、写它的往来明细行，登记预演新单。
        // 不符返回原因。
        public static string Write(WorkContext ctx, NotesProcPlan plan)
        {
            object conn = ctx.Conn;
            string bill = BillTitle(plan.Flag);
            NotesProcReturnBo saver = plan.Saver;
            if (saver == null)
            {
                throw new BridgeException(500, "internal", "票据退回缺少" + bill + "组件");
            }
            int before = NotesProcReturnSql.MaxId(conn);
            saver.Save(ctx, plan);
            Locate(ctx, plan, saver, before);
            if (plan.BillId <= 0 || plan.BillCode.Length == 0)
            {
                return "U8 保存了退回生成的" + bill + "，但按主键、单号都找不到唯一的一张";
            }
            if (plan.BillCode.Length > CodeMax)
            {
                return "U8 给出的" + bill + "号 " + plan.BillCode + " 超长";
            }
            DocMark.Created(conn, saver.Kind, plan.BillId, plan.BillCode);
            if (!NotesProcReturnSql.Stamp(conn, plan))
            {
                return bill + " " + plan.BillCode + " 的处理标记、审核列没有写上";
            }
            NotesProcReturnSql.InsertDetail(conn, plan);
            return null;
        }

        // 新单的主键和单号：先取 SaveVouch 写回表头 DOM 的（同 ArapCo.Created），缺了按单号找，再缺按保存前的最大主键兜底（事务内）。
        static void Locate(WorkContext ctx, NotesProcPlan plan, NotesProcReturnBo saver, int before)
        {
            int id = 0;
            string code = "";
            List<Dictionary<string, object>> rows = Rows.FromDom(saver.Head, 1);
            if (rows != null && rows.Count > 0)
            {
                id = CoRows.AsId(CoRows.Col(rows[0], saver.Kind.IdColumn));
                code = CoRows.Col(rows[0], saver.Kind.CodeColumn).Trim();
            }
            if (id <= 0 && code.Length > 0)
            {
                id = ArapSql.IdByCode(ctx.Conn, saver.Kind, saver.Spec, code);
            }
            if (id <= 0)
            {
                id = NotesProcReturnSql.Fresh(ctx.Conn, plan, before);
            }
            plan.BillId = id;
            plan.BillCode = id > 0 ? NotesProcReturnSql.CodeOf(ctx.Conn, plan.Flag, id) : "";
            CoRows.Note(ctx.Item, NotesProcReturnSql.BillType(plan.Flag) + " id=" + id.ToString(CultureInfo.InvariantCulture)
                + " code=" + plan.BillCode);
        }
    }
}
