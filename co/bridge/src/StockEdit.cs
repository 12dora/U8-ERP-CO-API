using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 08/09/12 修改，以及无来源的采购入库单（01，按 08/09 的行口径，另补税价）、来源库存的销售出库单（32，同 08/09）。Load 之后改 editprop，再调 12 个参数的 Update。
    internal static partial class StockEdit
    {
        public static ApiResult Update(WorkContext ctx, VoucherKind kind, int id,
            Dictionary<string, object> head, object[] lines)
        {
            Require(kind);
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            if (lines == null)
            {
                lines = new object[0];
            }
            bool eight = EightLike(kind);
            bool pur = StockDom.PurType(kind);
            Dictionary<string, object> before = StockCo.ReadHead(ctx.Conn, kind, id, eight || pur, eight || pur);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (StockMsg.Col(before, kind.VerifierColumn).Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (eight)
            {
                RefuseSource(before);
            }
            PurCheck(ctx, kind, id, before, head, lines);
            StockCo.Gate(ctx.Conn, kind, before);
            return Save(ctx, kind, id, head, lines, eight || pur);
        }

        // 无来源采购入库单：来源、红字、期初、记账、下游，以及请求里的供应商、新增行。
        static void PurCheck(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> before,
            Dictionary<string, object> head, object[] lines)
        {
            if (!StockDom.PurType(kind))
            {
                return;
            }
            StockPurIn.RefuseEdit(ctx, kind, id, before);
            StockPurIn.CheckEdit(ctx, head, lines);
            StockPurInPos.CheckEdit(ctx.Conn, id, head, lines);
        }

        static ApiResult Save(WorkContext ctx, VoucherKind kind, int id,
            Dictionary<string, object> head, object[] lines, bool eight)
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
                EditJob job = JobOf(ctx.Conn, kind, loaded.Head, loaded.Body, head, lines);
                job.Eight = eight;
                job.Bins = StockPosGuard.BinnedLines(ctx.Conn, kind, id);
                Apply(job);
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

        static void Apply(EditJob job)
        {
            object conn = job.Conn;
            VoucherKind kind = job.Kind;
            object domH = job.DomH;
            object domB = job.DomB;
            Dictionary<string, object> head = job.Head;
            object[] lines = job.Lines;
            bool eight = job.Eight;
            List<object> heads = DomRows.RowsOf(domH);
            List<object> rows = null;
            try
            {
                if (heads.Count == 0)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                List<string> headNames = DomRows.Schema(domH);
                StockPosGuard.RefuseWhChange(job.Bins, heads[0], head);
                StockDom.SetCell(domH, heads[0], "editprop", "M", headNames);
                StockDom.WriteAll(domH, heads[0], kind, true, head, headNames);
                rows = DomRows.RowsOf(domB);
                List<string> bodyNames = DomRows.Schema(domB);
                List<EditOp> ops = Parse(rows, lines, LineCol(kind));
                for (int i = 0; i < rows.Count; i++)
                {
                    StockDom.SetCell(domB, rows[i], "editprop", "", bodyNames);
                }
                object seed = rows.Count == 0 ? null : rows[0];
                OpRun run = OpOf(conn, kind, domB, eight, seed, NextRow(rows));
                run.Schema = bodyNames;
                run.Bins = job.Bins;
                StampTv(run, heads);
                for (int i = 0; i < ops.Count; i++)
                {
                    ApplyOp(run, ops[i]);
                }
            }
            finally
            {
                Release(heads);
                Release(rows);
            }
        }

        static void ApplyOp(OpRun run, EditOp op)
        {
            StockPosGuard.RefuseBinnedEdit(run.Bins, op.Target, LineCol(run.Kind), op.Op, op.Fields);
            if (op.Op == "delete")
            {
                StockDom.SetCell(run.Dom, op.Target, "editprop", "D", run.Schema);
                return;
            }
            if (op.Op == "update")
            {
                UpdateLine(run, op);
                return;
            }
            AddLine(run, op);
        }

        static void UpdateLine(OpRun run, EditOp op)
        {
            RefuseCode(op.Target, op.Fields);
            StockPosGuard.RefuseLineChange(op.Target, op.Fields);
            if (StockDom.PurType(run.Kind))
            {
                StockPurIn.CheckChangedLine(run.Conn, DomRows.Get(op.Target, "cInvCode"), op.Fields);
            }
            CheckUpdate(op.Fields, run.Eight);
            StockDom.SetCell(run.Dom, op.Target, "editprop", "M", run.Schema);
            bool changed = AuxChanged(op.Target, op.Fields);
            bool qtyChanged = QtyChanged(op.Target, op.Fields, QtyName(run));
            ClearAux(run, op.Target, op.Fields, changed);
            StockDom.WriteAll(run.Dom, op.Target, run.Kind, false, op.Fields, run.Schema);
            Touch(run, op.Target, op.Fields, changed, qtyChanged);
        }

        static void AddLine(OpRun run, EditOp op)
        {
            CheckAdd(op.Fields, run.Eight);
            if (run.Seed == null)
            {
                throw new BridgeException(400, "bad_request", "没有可复制的明细行");
            }
            object row = AppendClone(run.Dom, run.Seed);
            try
            {
                BlankNew(run, row);
                run.Next = run.Next + 1;
                bool changed = AuxChanged(row, op.Fields);
                ClearAux(run, row, op.Fields, changed);
                StockDom.WriteAll(run.Dom, row, run.Kind, false, op.Fields, run.Schema);
                Touch(run, row, op.Fields, changed, false);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        // 实测只有克隆已装入的表体行才能新增。新行只留表头外键、行号和 editprop。
        static object AppendClone(object dom, object seed)
        {
            object parent = null;
            object copy = null;
            try
            {
                parent = ComUtil.Get(seed, "parentNode");
                if (parent == null || parent is DBNull)
                {
                    throw new BridgeException(500, "internal", "模板没有行");
                }
                copy = ComUtil.Call(seed, "cloneNode", new object[] { true });
                if (copy == null || copy is DBNull)
                {
                    throw new BridgeException(500, "internal", "模板没有行");
                }
                ComUtil.Call(parent, "appendChild", new object[] { copy });
            }
            finally
            {
                ComUtil.Final(copy);
                ComUtil.ReleaseOne(parent);
            }
            return LastRow(dom);
        }

        static object LastRow(object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows.Count == 0)
                {
                    throw new BridgeException(500, "internal", "模板没有行");
                }
                object keep = rows[rows.Count - 1];
                rows[rows.Count - 1] = null;
                return keep;
            }
            finally
            {
                Release(rows);
            }
        }

        static void BlankNew(OpRun run, object row)
        {
            KeepOnly(row, run.Eight);
            CopyTv(run, row);
            StockDom.SetCell(run.Dom, row, "bcosting", "1", run.Schema);
            StockDom.SetCell(run.Dom, row, "irowno", run.Next.ToString(CultureInfo.InvariantCulture), run.Schema);
            StockDom.SetCell(run.Dom, row, "editprop", "A", run.Schema);
        }

        static void ClearAux(OpRun run, object row, Dictionary<string, object> fields, bool changed)
        {
            if (!changed)
            {
                return;
            }
            if (Differs(row, fields, "cinvcode"))
            {
                StockDom.SetCell(run.Dom, row, "cAssUnit", null, run.Schema);
            }
            StockDom.SetCell(run.Dom, row, "iInvExchRate", null, run.Schema);
            StockDom.SetCell(run.Dom, row, run.Eight ? "iNum" : "iTVNum", null, run.Schema);
        }

        static bool AuxChanged(object row, Dictionary<string, object> fields)
        {
            return Differs(row, fields, "cinvcode") || Differs(row, fields, "cassunit");
        }

        static bool Differs(object row, Dictionary<string, object> fields, string name)
        {
            string next = Text(fields, name);
            if (next.Length == 0)
            {
                return false;
            }
            string prev = DomRows.Get(row, name);
            if (prev == null)
            {
                prev = "";
            }
            return !string.Equals(next, prev.Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static void RefuseCode(object row, Dictionary<string, object> fields)
        {
            if (Differs(row, fields, "cinvcode"))
            {
                throw new BridgeException(400, "bad_request", "不能修改存货编码，请删除该行后新增");
            }
        }

        // 调拨新行的单据号取自表头，KeepOnly 会清掉克隆行上的 cTVCode。
        static void StampTv(OpRun run, List<object> heads)
        {
            if (run.Eight || heads == null || heads.Count == 0)
            {
                return;
            }
            run.TvCode = DomRows.Get(heads[0], "cTVCode").Trim();
        }

        static void CopyTv(OpRun run, object row)
        {
            if (run.TvCode == null || run.TvCode.Length == 0)
            {
                return;
            }
            StockDom.SetCell(run.Dom, row, "cTVCode", run.TvCode, run.Schema);
        }

        static void Touch(OpRun run, object row, Dictionary<string, object> fields, bool changed, bool qtyChanged)
        {
            if (run.Eight)
            {
                StockUnits.FixPriceSent(run.Dom, row, Sent(fields, "iprice"), Sent(fields, "iunitcost"),
                    Sent(fields, "iquantity"), run.Schema);
                ApplyQty(run, row, "iQuantity", "iNum", NeedNum(fields, "iquantity", "inum", changed));
                if (StockDom.PurType(run.Kind))
                {
                    StockDom.PurTouch(run.Conn, run.Dom, row, fields, run.Schema);
                }
                return;
            }
            ApplyQty(run, row, "iTVQuantity", "iTVNum", NeedNum(fields, "itvquantity", "itvnum", changed));
            if (qtyChanged)
            {
                Reprice(run, row);
            }
        }

        static string QtyName(OpRun run)
        {
            return run.Eight ? "iquantity" : "itvquantity";
        }

        static bool QtyChanged(object row, Dictionary<string, object> fields, string name)
        {
            if (!Sent(fields, name))
            {
                return false;
            }
            decimal next;
            if (!StockUnits.Dec(Text(fields, name), out next))
            {
                return false;
            }
            decimal prev;
            if (!StockUnits.Dec(DomRows.Get(row, name), out prev))
            {
                return true;
            }
            return next != prev;
        }

        static void ApplyQty(OpRun run, object row, string qtyName, string numName, bool force)
        {
            StockUnits.UnitJob job = new StockUnits.UnitJob();
            job.QtyName = qtyName;
            job.NumName = numName;
            job.Force = force;
            job.Schema = run.Schema;
            StockUnits.ApplyDom(run.Conn, run.Dom, row, job);
        }

        static void Reprice(OpRun run, object row)
        {
            decimal qty;
            if (!StockUnits.Dec(DomRows.Get(row, "iTVQuantity"), out qty))
            {
                return;
            }
            Amount(run, row, qty, "iTVACost", "iTVAPrice");
            Amount(run, row, qty, "iTVPCost", "iTVPPrice");
        }

        static void Amount(OpRun run, object row, decimal qty, string costName, string priceName)
        {
            decimal cost;
            if (!StockUnits.Dec(DomRows.Get(row, costName), out cost))
            {
                return;
            }
            StockDom.SetCell(run.Dom, row, priceName, StockUnits.Money(qty * cost), run.Schema);
        }

        static bool NeedNum(Dictionary<string, object> fields, string qtyName, string numName, bool changed)
        {
            if (Sent(fields, numName))
            {
                return false;
            }
            return Sent(fields, qtyName) || changed;
        }

        static void CheckAdd(Dictionary<string, object> fields, bool eight)
        {
            if (Text(fields, "cinvcode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "表体行缺少存货");
            }
            RequirePositive(fields, eight ? "iquantity" : "itvquantity", true);
        }

        static void CheckUpdate(Dictionary<string, object> fields, bool eight)
        {
            RequirePositive(fields, eight ? "iquantity" : "itvquantity", false);
        }

        static void RequirePositive(Dictionary<string, object> fields, string name, bool required)
        {
            if (!Sent(fields, name))
            {
                if (required)
                {
                    throw new BridgeException(400, "bad_request", "数量必须大于 0");
                }
                return;
            }
            decimal qty;
            if (!StockUnits.Dec(Text(fields, name), out qty) || qty <= 0m)
            {
                throw new BridgeException(400, "bad_request", "数量必须大于 0");
            }
        }

        static ApiResult AfterSaved(WorkContext ctx, VoucherKind kind, int id, object headDom)
        {
            string code = "";
            try
            {
                code = LoadedCode(headDom, kind.CodeColumn);
                return EditMsg.Saved(ctx, kind, id, null, 0);
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(id, code));
            }
        }

        static string LoadedCode(object dom, string column)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows == null || rows.Count == 0 || column == null)
                {
                    return "";
                }
                return DomRows.Get(rows[0], column).Trim();
            }
            finally
            {
                Release(rows);
            }
        }

        static string Unknown(int id, string code)
        {
            string msg = "已保存但未能确定单据标识";
            string no = code == null ? "" : code.Trim();
            if (no.Length > 0)
            {
                msg = msg + "，单号 " + no;
            }
            if (id > 0)
            {
                msg = msg + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            return msg;
        }

        static EditJob JobOf(object conn, VoucherKind kind, object domH, object domB,
            Dictionary<string, object> head, object[] lines)
        {
            EditJob job = new EditJob();
            job.Conn = conn;
            job.Kind = kind;
            job.DomH = domH;
            job.DomB = domB;
            job.Head = head;
            job.Lines = lines;
            return job;
        }

        static OpRun OpOf(object conn, VoucherKind kind, object dom, bool eight, object seed, int next)
        {
            OpRun run = new OpRun();
            run.Conn = conn;
            run.Kind = kind;
            run.Dom = dom;
            run.Eight = eight;
            run.Seed = seed;
            run.Next = next;
            return run;
        }

        sealed class EditJob
        {
            public object Conn;
            public VoucherKind Kind;
            public object DomH;
            public object DomB;
            public Dictionary<string, object> Head;
            public object[] Lines;
            public bool Eight;
            public HashSet<int> Bins;
        }

        sealed class OpRun
        {
            public object Conn;
            public VoucherKind Kind;
            public object Dom;
            public bool Eight;
            public object Seed;
            public int Next;
            public List<string> Schema;
            public string TvCode;
            public HashSet<int> Bins;
        }
    }
}
