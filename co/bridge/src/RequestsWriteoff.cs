using System.Collections.Generic;

namespace U8Co
{
    // arap/writeoff（应收 / 应付核销）在登录前的校验：不带 type、id；字段 receipt、items（ArapWriteoffReq）。
    // 登录子系统跟收付款单走（收款单 AR、付款单 AP）；核销日期就是登录日期 date。处理在 ArapWriteoff。
    // arap/writeoff/cancel（取消核销）：字段 flag、cancel_no（ArapUnwriteoffReq），登录子系统就是 flag。处理在 ArapUnwriteoff。
    internal static partial class Requests
    {
        internal const string WriteoffPath = "/u8co/v1/arap/writeoff";
        internal const string WriteoffCancelPath = "/u8co/v1/arap/writeoff/cancel";
        internal const string WriteoffAutoPath = "/u8co/v1/arap/writeoff/auto";

        static bool IsWriteoff(string path)
        {
            // 并账（arap/merge，ArapMergeReq）同样不带 type、id，归在这一组。
            // 应收冲应付 / 应付冲应收（arap/transfer，RequestsTransfer）同样。
            return path == WriteoffPath || path == WriteoffCancelPath || path == WriteoffAutoPath || path == ArapMergeReq.Path
                || IsTransfer(path) || IsRedOffset(path) || IsNotesProc(path) || IsNotesReg(path);
        }

        // 审计 action：writeoff / writeoff_cancel / writeoff_auto（RequestsP4.ActionP4）。
        static string WriteoffAction(string path)
        {
            if (path == WriteoffAutoPath)
            {
                return ArapAutoWriteoffReq.Action;
            }
            // 并账：merge。
            if (path == ArapMergeReq.Path)
            {
                return ArapMergeReq.Action;
            }
            // 应收冲应付 / 应付冲应收：transfer。
            if (IsTransfer(path))
            {
                return ArapTransferReq.Action;
            }
            // 红票对冲：red_offset。
            if (IsRedOffset(path))
            {
                return ArapRedReq.Action;
            }
            // 票据处理：notes_process。
            if (IsNotesProc(path))
            {
                return NotesProcReq.Action;
            }
            // 票据登记、删除：notes_create / notes_delete。
            if (IsNotesReg(path))
            {
                return NotesRegReq.ActionOf(path);
            }
            return path == WriteoffCancelPath ? ArapUnwriteoffReq.Action : ArapWriteoffReq.Action;
        }

        static void ApplyWriteoff(Dictionary<string, object> body, WorkItem item)
        {
            if (item.Path == WriteoffCancelPath)
            {
                UnwriteoffAsk ask = ArapUnwriteoffReq.Parse(body);
                item.SubId = ask.Flag;
                CoRows.Note(item, "取消核销 " + ask.CancelNo);
                return;
            }
            // arap/writeoff/auto（自动核销）：字段见 ArapAutoWriteoffReq，登录子系统就是 flag。处理在 ArapAutoWriteoff。
            if (item.Path == WriteoffAutoPath)
            {
                AutoWriteoffAsk auto = ArapAutoWriteoffReq.Parse(body);
                item.SubId = auto.Flag;
                CoRows.Note(item, (auto.DryRun ? "自动核销试算 " : "自动核销 ") + auto.Partner);
                return;
            }
            // arap/merge（并账）：字段见 ArapMergeReq，登录子系统就是 flag。处理在 ArapMerge。
            if (item.Path == ArapMergeReq.Path)
            {
                MergeAsk merge = ArapMergeReq.Parse(body);
                item.SubId = merge.Flag;
                CoRows.Note(item, "并账 " + merge.From + " → " + merge.To);
                return;
            }
            // arap/transfer（应收冲应付 / 应付冲应收）：RequestsTransfer.ApplyTransfer。
            if (ApplyTransfer(body, item))
            {
                return;
            }
            // arap/red_offset（红票对冲）：ArapRedReq.cs 的 ApplyRedOffset。
            if (ApplyRedOffset(body, item))
            {
                return;
            }
            // notes/process（票据处理）：NotesProcReq.cs 的 ApplyNotesProc。
            if (ApplyNotesProc(body, item))
            {
                return;
            }
            // notes/create、notes/delete（票据登记、删除）：NotesRegReq.cs 的 ApplyNotesReg。
            if (ApplyNotesReg(body, item))
            {
                return;
            }
            item.SubId = ArapWriteoffReq.Check(body);
            CoRows.Note(item, "核销 " + KeyPart(Field(Field(body, "receipt") as Dictionary<string, object>, "type"))
                + " " + KeyPart(Field(Field(body, "receipt") as Dictionary<string, object>, "id")));
        }
    }
}
