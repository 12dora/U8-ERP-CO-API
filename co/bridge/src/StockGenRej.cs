using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购入库参照来料不良品处理单（QM05）：id 是不良品处理单 ID，lines 的 source_line_id 是它的表体 AUTOID（QMREJECTVOUCHERS），
    // 同一张不良品处理单的 1 到 200 行生成一张入库单。只接处理流程为 2（降级 / 让步接收，见 RejSrc）的行，数量须等于该行剩余的
    // 处理后数量（FDIMQUANTITY，U8 不接受部分入库），入库存货取处理后存货。USERPCO VoucherCO.Insert("01")，csource=来料不良品处理单，
    // 表体 irejectids / crejectcode / icheckidbaks / iarrsid / iposid 挂不良品处理单、检验单、到货行和订单行，单价取采购订单行。
    // 来源规则与产成品入库参照产品不良品处理单共用（RejSrc）。
    internal static partial class StockGen
    {
        public static ApiResult PurchaseInRej(WorkContext ctx, VoucherKind kind, int rejectId,
            Dictionary<string, object> head, object[] lines)
        {
            CheckRej(head, lines);
            RejInJob job = LoadRej(ctx.Conn, rejectId, head, lines);
            List<string> codes = new List<string>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                codes.Add(job.Lines[i].Pos);
            }
            CheckGenPositions(ctx.Conn, job.Wh, codes);
            return InsertRej(ctx, kind, job);
        }

        // 登录前校验：表头同参照来料检验单（必须有仓库），表体 1 到 200 行，只收 source_line_id、quantity、cbatch、cbmemo、cposition。
        internal static void CheckRej(Dictionary<string, object> head, object[] lines)
        {
            CheckInHead(head);
            MfgReq.CheckLines(lines, 200);
        }

        static RejInJob LoadRej(object conn, int rejectId, Dictionary<string, object> head, object[] lines)
        {
            RejInJob job = new RejInJob();
            job.RejectId = rejectId;
            job.Head = head;
            job.Wh = Text(head, "cwhcode");
            job.Rej = RejSrc.Head(conn, RejSrc.Incoming, rejectId);
            if (CoRows.Col(job.Rej, "CSOURCE") != "到货单" || AsId(CoRows.Col(job.Rej, "ArrLine")) <= 0)
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单来源不是到货单");
            }
            job.Arr = Rows.One(conn, ArrSql, new object[] { AsId(CoRows.Col(job.Rej, "ArrLine")) });
            if (job.Arr == null)
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单对应的到货单不存在");
            }
            if (CoRows.Col(job.Arr, "ArrCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "到货单行已关闭");
            }
            int poLine = AsId(CoRows.Col(job.Arr, "PoLine"));
            job.Po = poLine > 0 ? Rows.One(conn, PoLineSql, new object[] { poLine }) : null;
            if (job.Po == null || CoRows.Col(job.Arr, "cPOID").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单没有对应的采购订单行");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    job.Lines.Add(RejInLineOf(conn, job.Rej, rejectId, lines[i]));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return job;
        }

        static RejInLine RejInLineOf(object conn, Dictionary<string, object> rej, int rejectId, object raw)
        {
            Dictionary<string, object> line = MfgReq.LineOf(raw, true);
            RejInLine item = new RejInLine();
            item.Id = MfgReq.LineId(line);
            item.Qty = MfgReq.LineQty(line);
            item.Src = RejSrc.Line(conn, rej, rejectId, item.Id);
            RejSrc.CheckQty(conn, RejSrc.Incoming, item.Src, item.Id, item.Qty);
            item.Batch = Or(Text(line, "cbatch"), Or(CoRows.Col(item.Src, "DimBatch"), RejHeadBatch(rej, item.Src)));
            item.Memo = Text(line, "cbmemo");
            item.Pos = Text(line, "cposition");
            return item;
        }

        // 处理单表头批号只属于检验时的存货；降级到别的存货时不沿用。
        static string RejHeadBatch(Dictionary<string, object> rej, Dictionary<string, object> src)
        {
            bool same = string.Compare(CoRows.Col(src, "InvCode"), CoRows.Col(rej, "CINVCODE"), true, CultureInfo.InvariantCulture) == 0;
            return same ? CoRows.Col(rej, "CBATCH") : "";
        }

        static ApiResult InsertRej(WorkContext ctx, VoucherKind kind, RejInJob job)
        {
            VoucherKind source = Kinds.Find(RejSrc.Incoming.Source);
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                domH = Blank(ctx.Conn, true);
                domB = Blank(ctx.Conn, false);
                StampRejHead(ctx, domH, job);
                FillRejBody(ctx.Conn, domB, job);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadMap(domH));
                StockDom.SetHeadValue(domH, "cCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockAt at = StockCall.AtFor("Insert", args, refs, null);
                RejInGuard guard = new RejInGuard(job.Lines);
                at.Before = guard.Before;
                at.After = guard.After;
                DryRun.Touched(source, job.RejectId);
                StockCall.RunAt(ctx, co, at);
                int newId = Confirmed(ctx, kind, code, args[6]);
                return AfterSaved(ctx, kind, newId, code, source, job.RejectId);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(pos);
                ComUtil.Final(domB);
                ComUtil.Final(domH);
                ComUtil.Final(co);
            }
        }

        // 表头：供应商取不良品处理单，部门、业务类型、币种、税率取到货单，订单号、到货单号取来源链。
        static void StampRejHead(WorkContext ctx, object dom, RejInJob job)
        {
            Dictionary<string, object> head = job.Head;
            Dictionary<string, object> arr = job.Arr;
            string date = Or(Text(head, "ddate"), ctx.Item.Date == null ? "" : ctx.Item.Date.Trim());
            string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                Put(dom, row, schema, "id", "");
                Put(dom, row, schema, "editprop", "A");
                Put(dom, row, schema, "ddate", date);
                Put(dom, row, schema, "cwhcode", job.Wh);
                Put(dom, row, schema, "crdcode", StockPurIn.GenRd(ctx.Conn, head, CoRows.Col(arr, "cPTCode")));
                Copy(dom, row, schema, "cdepcode", Or(Text(head, "cdepcode"), CoRows.Col(arr, "cDepCode")));
                Copy(dom, row, schema, "cpersoncode", Or(Text(head, "cpersoncode"), CoRows.Col(arr, "cPersonCode")));
                Copy(dom, row, schema, "cvencode", Or(CoRows.Col(job.Rej, "CVENCODE"), CoRows.Col(arr, "cVenCode")));
                Copy(dom, row, schema, "cptcode", CoRows.Col(arr, "cPTCode"));
                Copy(dom, row, schema, "cbustype", CoRows.Col(arr, "cBusType"));
                Copy(dom, row, schema, "cexch_name", CoRows.Col(arr, "cexch_name"));
                Put(dom, row, schema, "iexchrate", Rate(CoRows.Col(arr, "nflat")).ToString("0.##########", CultureInfo.InvariantCulture));
                Copy(dom, row, schema, "itaxrate", CoRows.Col(arr, "iTaxRate"));
                Put(dom, row, schema, "csource", RejSrc.Incoming.Title);
                Put(dom, row, schema, "cordercode", CoRows.Col(arr, "cPOID"));
                Put(dom, row, schema, "carvcode", CoRows.Col(arr, "ArrCode"));
                Copy(dom, row, schema, "darvdate", CoRows.Col(arr, "ArrDate"));
                Copy(dom, row, schema, "cchkcode", CoRows.Col(job.Rej, "CCHECKCODE"));
                Put(dom, row, schema, "vt_id", "27");
                Put(dom, row, schema, "brdflag", "1");
                Put(dom, row, schema, "cvouchtype", "01");
                Put(dom, row, schema, "cmaker", maker);
                Copy(dom, row, schema, "cmemo", Text(head, "cmemo"));
                CopyDefines(dom, row, head, schema);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        static void FillRejBody(object conn, object dom, RejInJob job)
        {
            decimal exch = Rate(CoRows.Col(job.Arr, "nflat"));
            string headRate = CoRows.Col(job.Arr, "iTaxRate");
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    StampRejLine(dom, row, schema, job, job.Lines[i]);
                    PutAmounts(dom, row, schema, Amounts(job.Po, job.Lines[i].Qty, exch, headRate));
                    Put(dom, row, schema, "irowno", (i + 1).ToString(CultureInfo.InvariantCulture));
                    ApplyQty(conn, dom, row, schema);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        // 行上照 U8 自己生成的入库行写 irejectids（表体 AUTOID）、crejectcode、检验单、到货行、订单行。
        static void StampRejLine(object dom, object row, List<string> schema, RejInJob job, RejInLine line)
        {
            Dictionary<string, object> rej = job.Rej;
            string qty = line.Qty.ToString("0.######", CultureInfo.InvariantCulture);
            Put(dom, row, schema, "autoid", "");
            Put(dom, row, schema, "id", "");
            Put(dom, row, schema, "editprop", "A");
            Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "InvCode"));
            Put(dom, row, schema, "iquantity", qty);
            Put(dom, row, schema, "inquantity", qty);
            Put(dom, row, schema, "iposid", CoRows.Col(job.Arr, "PoLine"));
            Put(dom, row, schema, "cpoid", CoRows.Col(job.Arr, "cPOID"));
            Put(dom, row, schema, "iarrsid", CoRows.Col(rej, "ArrLine"));
            Put(dom, row, schema, "irejectids", line.Id.ToString(CultureInfo.InvariantCulture));
            Put(dom, row, schema, "crejectcode", CoRows.Col(rej, "CREJECTCODE"));
            Copy(dom, row, schema, "icheckidbaks", CoRows.Col(rej, "CheckId"));
            Copy(dom, row, schema, "ccheckcode", CoRows.Col(rej, "CCHECKCODE"));
            Copy(dom, row, schema, "ccheckpersoncode", CoRows.Col(rej, "CCHECKPERSON"));
            Copy(dom, row, schema, "dcheckdate", CoRows.Col(rej, "CheckDate"));
            Copy(dom, row, schema, "cbatch", line.Batch);
            Copy(dom, row, schema, "cposition", line.Pos);
            Copy(dom, row, schema, "cbmemo", line.Memo);
        }

        // 删除来源为来料不良品处理单的采购入库：U8 回退 BFLAG 未经实测，所以在同一事务里核对，没退就回滚（RejReverse）。
        internal static void GuardRejUndo(StockAt at, int rdId)
        {
            RejReverse undo = new RejReverse(RejSrc.Incoming, rdId);
            at.Before = undo.Before;
            at.After = undo.After;
        }

        // Insert 前后在同一事务里逐行核对（RejLineGuard）：已入库数量加了本次数量，入库满额时不良品处理单行 BFLAG 为 1。
        sealed class RejInGuard
        {
            readonly List<RejLineGuard> _lines = new List<RejLineGuard>();

            public RejInGuard(List<RejInLine> lines)
            {
                for (int i = 0; i < lines.Count; i++)
                {
                    _lines.Add(new RejLineGuard(RejSrc.Incoming, lines[i].Id, lines[i].Qty, Num(CoRows.Col(lines[i].Src, "BaseQty"))));
                }
            }

            public void Before(object conn)
            {
                for (int i = 0; i < _lines.Count; i++)
                {
                    _lines[i].Before(conn);
                }
            }

            public void After(object conn)
            {
                for (int i = 0; i < _lines.Count; i++)
                {
                    _lines[i].After(conn);
                }
            }
        }

        sealed class RejInJob
        {
            public int RejectId;
            public string Wh;
            public Dictionary<string, object> Head;
            public Dictionary<string, object> Rej;
            public Dictionary<string, object> Arr;
            public Dictionary<string, object> Po;
            public readonly List<RejInLine> Lines = new List<RejInLine>();
        }

        sealed class RejInLine
        {
            public int Id;
            public decimal Qty;
            public string Batch;
            public string Memo;
            public string Pos;
            public Dictionary<string, object> Src;
        }
    }
}
