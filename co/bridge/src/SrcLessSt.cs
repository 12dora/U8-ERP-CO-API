using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 无来源材料出库单（SrcLess）：USERPCO.Insert("11")，csource=库存，表体不写 iMPoIds（AllocateId），U8 不回写 IssQty。
    // 表头同参照生产订单的那组（MfgGenDom.StampHead）去掉生产订单号、产品、订单数量；收发类别必须由调用方给（同生单，MfgReq.NeedRd）。
    // 编号、Insert、回读（Confirmed / AfterSaved）同 MfgGen.Insert。删除走 MfgGen.Delete（来源库存另放行，见 MfgGenMo）。
    // 行上可带货位 cposition，核对同生单（MfgGen.CheckPositions）。
    internal static partial class MfgGen
    {
        const string FreeInvSql = "select cInvCode, convert(varchar(5), isnull(bInvBatch,0)) as batch,"
            + " convert(varchar(5), isnull(bInvQuality,0)) as mass from Inventory where cInvCode=?";
        const string FreeSourceTitle = "库存";

        internal static ApiResult MaterialOutFree(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head,
            object[] lines)
        {
            MfgJob job = new MfgJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.Head = head;
            job.Lines = new List<MfgLine>();
            string date = MfgReq.Text(head, "ddate");
            job.Date = date.Length > 0 ? date : (ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim());
            job.RdCode = MfgReq.NeedRd(head, false);
            job.DeptCode = MfgReq.Text(head, "cdepcode");
            CheckArchives(ctx.Conn, job, false);
            for (int i = 0; i < lines.Length; i++)
            {
                job.Lines.Add(FreeLine(ctx.Conn, lines[i] as Dictionary<string, object>));
            }
            return InsertFree(job);
        }

        // 存货存在；批次管理的必须带批号，非批次管理的不能带。保质期管理的另要日期，本期不收，直接拒绝。
        static MfgLine FreeLine(object conn, Dictionary<string, object> raw)
        {
            string inv = MfgReq.Text(raw, "cinvcode");
            Dictionary<string, object> row = Rows.One(conn, FreeInvSql, new object[] { inv });
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "存货不存在：" + inv);
            }
            MfgLine line = new MfgLine();
            line.Qty = SrcLessReq.Qty(raw);
            line.Batch = MfgReq.Text(raw, "cbatch");
            line.Pos = MfgReq.Text(raw, "cposition");
            line.Memo = MfgReq.Text(raw, "cbmemo");
            line.Src = row;
            bool batch = CoRows.Col(row, "batch") == "1";
            if (CoRows.Col(row, "mass") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "存货 " + inv + " 启用保质期管理，本期不支持无来源材料出库");
            }
            if (batch && line.Batch.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "存货 " + inv + " 启用批次管理，必须填批号 cbatch");
            }
            if (!batch && line.Batch.Length > 0)
            {
                throw new BridgeException(400, "bad_request", "存货 " + inv + " 未启用批次管理，不能填批号");
            }
            return line;
        }

        static ApiResult InsertFree(MfgJob job)
        {
            WorkContext ctx = job.Ctx;
            CheckPositions(ctx.Conn, job);
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                domH = Blank(ctx.Conn, job.Kind, true);
                domB = Blank(ctx.Conn, job.Kind, false);
                FreeHead(job, domH);
                FreeBody(job, domB);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, job.Kind, HeadSeeds(domH));
                StockDom.SetHeadValue(domH, "cCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(job.Kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockCall.RunAt(ctx, co, StockCall.AtFor("Insert", args, refs, null));
                int newId = Confirmed(ctx, job.Kind, code, args[6]);
                return AfterSaved(job, newId, code);
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

        static void FreeHead(MfgJob job, object dom)
        {
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                string maker = job.Ctx.Session == null || job.Ctx.Session.OperatorName == null
                    ? "" : job.Ctx.Session.OperatorName.Trim();
                string[] pairs = new string[]
                {
                    "editprop", "A", "id", "", "ccode", "", "cvouchtype", "11", "brdflag", "0", "cbustype", "领料",
                    "csource", FreeSourceTitle, "vt_id", "65", "iverifystate", "0", "iswfcontrolled", "0", "bredvouch", "0"
                };
                for (int k = 0; k < pairs.Length; k += 2)
                {
                    Put(dom, row, schema, pairs[k], pairs[k + 1]);
                }
                Put(dom, row, schema, "cwhcode", MfgReq.Text(job.Head, "cwhcode"));
                Put(dom, row, schema, "ddate", job.Date);
                Put(dom, row, schema, "crdcode", job.RdCode);
                Put(dom, row, schema, "cmaker", maker);
                Copy(dom, row, schema, "cdepcode", job.DeptCode);
                Copy(dom, row, schema, "cpersoncode", MfgReq.Text(job.Head, "cpersoncode"));
                Copy(dom, row, schema, "cmemo", MfgReq.Text(job.Head, "cmemo"));
                for (int n = 1; n <= 16; n++)
                {
                    string key = "cdefine" + n.ToString(CultureInfo.InvariantCulture);
                    Copy(dom, row, schema, key, MfgReq.Text(job.Head, key));
                }
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        static void FreeBody(MfgJob job, object dom)
        {
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    MfgLine line = job.Lines[i];
                    Put(dom, row, schema, "editprop", "A");
                    Put(dom, row, schema, "autoid", "");
                    Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "cInvCode"));
                    Put(dom, row, schema, "iquantity", line.Qty.ToString("0.######", CultureInfo.InvariantCulture));
                    Put(dom, row, schema, "bcosting", "1");
                    Put(dom, row, schema, "irowno", (i + 1).ToString(CultureInfo.InvariantCulture));
                    Copy(dom, row, schema, "cbatch", line.Batch);
                    Copy(dom, row, schema, "cposition", line.Pos);
                    Copy(dom, row, schema, "cbmemo", line.Memo);
                    ApplyQty(job.Ctx.Conn, dom, row, schema);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }
    }
}
