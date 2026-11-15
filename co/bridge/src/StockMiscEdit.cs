using System.Collections.Generic;

namespace U8Co
{
    // 形态转换单（15）、调拨申请单（62）的修改（StockMisc 分派；盘点单不开修改）。
    // 同调拨：Load 之后改 editprop，再调 12 参 Update。行操作解析、克隆新行、换算辅数量沿用 StockEdit 的函数；
    // 新行的单号列（cAVCode / cTVCode）取自表头，数量改了且行上有单价时重算金额，形态转换改完按整单复核成组。
    internal static partial class StockEdit
    {
        internal static ApiResult MiscUpdate(WorkContext ctx, VoucherKind kind, int id,
            Dictionary<string, object> head, object[] lines)
        {
            if (!StockMisc.Handles(kind) || !kind.Updatable)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
            Dictionary<string, object> before = StockMisc.Head(ctx.Conn, kind, id);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (StockMsg.Col(before, kind.VerifierColumn).Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            StockMisc.RefuseEdit(kind, before);
            StockCo.Gate(ctx.Conn, kind, before);
            return MiscSave(ctx, kind, id, head ?? new Dictionary<string, object>(), lines ?? new object[0]);
        }

        static ApiResult MiscSave(WorkContext ctx, VoucherKind kind, int id,
            Dictionary<string, object> head, object[] lines)
        {
            object co = null;
            object msg = null;
            StockLoaded loaded = new StockLoaded();
            try
            {
                co = StockCall.OpenCo(ctx);
                loaded.Head = Rows.NewDom();
                loaded.Body = Rows.NewDom();
                loaded.Pos = Rows.NewDom();
                StockCall.CallLoad(ctx, co, kind, id, loaded);
                if (loaded.Pos == null)
                {
                    loaded.Pos = Rows.NewDom();
                }
                msg = Rows.NewDom();
                MiscApply(JobOf(ctx.Conn, kind, loaded.Head, loaded.Body, head, lines));
                int[] refs;
                object[] args = StockCall.UpdateArgs(kind,
                    StockCall.Forms(loaded.Head, loaded.Body, loaded.Pos, ctx.Conn, msg), out refs);
                StockCall.RunCo(ctx, co, "Update", args, refs, null);
                return AfterSaved(ctx, kind, id, loaded.Head);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(loaded.Pos);
                ComUtil.Final(loaded.Body);
                ComUtil.Final(loaded.Head);
                ComUtil.Final(co);
            }
        }

        static void MiscApply(EditJob job)
        {
            List<object> heads = DomRows.RowsOf(job.DomH);
            List<object> rows = null;
            try
            {
                if (heads.Count == 0)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                List<string> headNames = DomRows.Schema(job.DomH);
                StockDom.SetCell(job.DomH, heads[0], "editprop", "M", headNames);
                StockDom.WriteAll(job.DomH, heads[0], job.Kind, true, job.Head, headNames);
                rows = DomRows.RowsOf(job.DomB);
                List<string> bodyNames = DomRows.Schema(job.DomB);
                List<EditOp> ops = Parse(rows, job.Lines, LineCol(job.Kind));
                for (int i = 0; i < rows.Count; i++)
                {
                    StockDom.SetCell(job.DomB, rows[i], "editprop", "", bodyNames);
                }
                OpRun run = OpOf(job.Conn, job.Kind, job.DomB, false, rows.Count == 0 ? null : rows[0], NextRow(rows));
                run.Schema = bodyNames;
                run.TvCode = DomRows.Get(heads[0], job.Kind.CodeColumn).Trim();
                for (int i = 0; i < ops.Count; i++)
                {
                    MiscOp(run, ops[i]);
                }
            }
            finally
            {
                Release(heads);
                Release(rows);
            }
            MiscRecheck(job.Kind, job.DomB);
        }

        static void MiscOp(OpRun run, EditOp op)
        {
            if (op.Op == "delete")
            {
                StockDom.SetCell(run.Dom, op.Target, "editprop", "D", run.Schema);
                return;
            }
            if (op.Op == "update")
            {
                MiscUpdateLine(run, op);
                return;
            }
            MiscAddLine(run, op);
        }

        static void MiscUpdateLine(OpRun run, EditOp op)
        {
            RefuseCode(op.Target, op.Fields);
            string qtyLow = StockDom.MiscQty(run.Kind).ToLowerInvariant();
            if (Sent(op.Fields, qtyLow))
            {
                StockDom.MiscQtyOk(Text(op.Fields, qtyLow), false);
            }
            StockDom.SetCell(run.Dom, op.Target, "editprop", "M", run.Schema);
            bool changed = AuxChanged(op.Target, op.Fields);
            bool qtyChanged = QtyChanged(op.Target, op.Fields, qtyLow);
            MiscClearAux(run, op.Target, changed);
            StockDom.WriteAll(run.Dom, op.Target, run.Kind, false, op.Fields, run.Schema);
            MiscTouch(run, op.Target, op.Fields, changed, qtyChanged);
            MiscChk(run, op.Target, op.Fields, false);
        }

        static void MiscAddLine(OpRun run, EditOp op)
        {
            if (Text(op.Fields, "cinvcode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "表体行缺少存货");
            }
            StockDom.MiscQtyOk(Text(op.Fields, StockDom.MiscQty(run.Kind).ToLowerInvariant()), false);
            if (run.Kind.StType == "15" && Text(op.Fields, "cwhcode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "形态转换单每行都要有仓库 cwhcode");
            }
            if (run.Seed == null)
            {
                throw new BridgeException(400, "bad_request", "没有可复制的明细行");
            }
            object row = AppendClone(run.Dom, run.Seed);
            try
            {
                MiscBlankNew(run, row);
                run.Next = run.Next + 1;
                bool changed = AuxChanged(row, op.Fields);
                MiscClearAux(run, row, changed);
                StockDom.WriteAll(run.Dom, row, run.Kind, false, op.Fields, run.Schema);
                MiscTouch(run, row, op.Fields, changed, false);
                MiscChk(run, row, op.Fields, true);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        // 克隆行只留外键、行号、editprop（形态转换另留 bcosting），再补表头单号；形态转换缺省第 1 组。
        static void MiscBlankNew(OpRun run, object row)
        {
            KeepOnly(row, run.Kind.StType != "15");
            if (run.TvCode != null && run.TvCode.Length > 0)
            {
                StockDom.SetCell(run.Dom, row, run.Kind.CodeColumn, run.TvCode, run.Schema);
            }
            if (run.Kind.StType == "15")
            {
                StockDom.SetCell(run.Dom, row, "bcosting", "1", run.Schema);
                StockDom.SetCell(run.Dom, row, "iGroupNO", "1", run.Schema);
            }
            StockDom.SetCell(run.Dom, row, "irowno", run.Next.ToString(System.Globalization.CultureInfo.InvariantCulture), run.Schema);
            StockDom.SetCell(run.Dom, row, "editprop", "A", run.Schema);
        }

        static void MiscClearAux(OpRun run, object row, bool changed)
        {
            if (!changed)
            {
                return;
            }
            StockDom.SetCell(run.Dom, row, "iInvExchRate", null, run.Schema);
            StockDom.SetCell(run.Dom, row, StockDom.MiscNum(run.Kind), null, run.Schema);
        }

        static void MiscTouch(OpRun run, object row, Dictionary<string, object> fields, bool changed, bool qtyChanged)
        {
            string qty = StockDom.MiscQty(run.Kind);
            string num = StockDom.MiscNum(run.Kind);
            ApplyQty(run, row, qty, num, NeedNum(fields, qty.ToLowerInvariant(), num.ToLowerInvariant(), changed));
            decimal value;
            if (!qtyChanged || !StockUnits.Dec(DomRows.Get(row, qty), out value))
            {
                return;
            }
            if (run.Kind.StType == "15")
            {
                Amount(run, row, value, "iAVACost", "iAVAPrice");
                Amount(run, row, value, "iAVPCost", "iAVPPrice");
                return;
            }
            Amount(run, row, value, "iTVPCost", "iTVPPrice");
            Amount(run, row, value, "iUnitCost", "iPrice");
        }

        // 调拨申请的核准数量：新增行没给时取申请数量；改行只在送了 itvchkquantity 时改（0 或以上），并按换算率补核准件数。
        static void MiscChk(OpRun run, object row, Dictionary<string, object> fields, bool added)
        {
            if (run.Kind.StType != "62")
            {
                return;
            }
            bool sent = Sent(fields, "itvchkquantity");
            if (!sent && !added)
            {
                return;
            }
            if (sent)
            {
                StockDom.MiscQtyOk(Text(fields, "itvchkquantity"), true);
            }
            else
            {
                StockDom.SetCell(run.Dom, row, "iTvChkQuantity", DomRows.Get(row, "iTVQuantity").Trim(), run.Schema);
            }
            ApplyQty(run, row, "iTvChkQuantity", "iTVChkNum", true);
        }

        // 形态转换改完之后，按没有标 D 的行复核成组（StockDom.CheckGroups）。
        // 调拨申请改完之后，每个没标 D 的行核准数量不能大于申请数量（只改了申请数量时也查）。
        static void MiscRecheck(VoucherKind kind, object domB)
        {
            if (kind.StType != "15" && kind.StType != "62")
            {
                return;
            }
            List<object> rows = DomRows.RowsOf(domB);
            try
            {
                List<string[]> pairs = new List<string[]>();
                for (int i = 0; i < rows.Count; i++)
                {
                    if (DomRows.Get(rows[i], "editprop").Trim() == "D")
                    {
                        continue;
                    }
                    if (kind.StType == "62")
                    {
                        StockDom.ChkWithin(DomRows.Get(rows[i], "iTvChkQuantity"), DomRows.Get(rows[i], "iTVQuantity"));
                        continue;
                    }
                    pairs.Add(new string[] { DomRows.Get(rows[i], "bAVType").Trim(), DomRows.Get(rows[i], "iGroupNO").Trim() });
                }
                if (kind.StType == "15")
                {
                    StockDom.CheckGroups(pairs);
                }
            }
            finally
            {
                Release(rows);
            }
        }
    }
}
