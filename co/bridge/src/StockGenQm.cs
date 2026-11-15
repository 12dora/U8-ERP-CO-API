using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购入库参照来料检验单（QM03）：id 与唯一一行的 source_line_id 都是检验单 ID。表头取到货单，单价取采购订单行，
    // 行上带检验单关联（icheckidbaks、iarrsid、ccheckcode、corufts）。U8 在 Insert("01") 里回写
    // QMCHECKVOUCHER.FsumQuantity、到货行 fValidInQuan、订单行 freceivedqty。属性按测试账套上的实测结果写。
    // 行上可带货位 cposition，生单前核对（CheckGenPositions）：只写表体 cPosition，货位 DOM 传空，
    // InvPosition 由 U8 写（同无来源采购入库，StockPurInPos；写入时点已在测试账套核对）。
    internal static partial class StockGen
    {
        const string QmSql = "select q.CCHECKCODE, q.CVERIFIER, q.CSOURCE, q.CINVCODE, q.CBATCH, q.CCHECKPERSONCODE,"
            + " convert(varchar(10), q.DDATE, 23) as CheckDate, convert(varchar(20), q.SOURCEAUTOID) as ArrLine,"
            + " convert(varchar(40), isnull(q.FREGQUANTITY,0)) as RegQty, convert(varchar(40), isnull(q.FCONQUANTIY,0)) as ConQty,"
            + " convert(varchar(40), isnull(q.FsumQuantity,0)) as SumQty, convert(varchar(5), isnull(q.BMERGECHECKFLAG,0)) as Merged,"
            + " convert(varchar(30), convert(money, q.ufts), 2) as QmUfts"
            + " from QMCHECKVOUCHER q where q.ID=? and q.CVOUCHTYPE='QM03'";
        const string ArrSql = "select h.cCode as ArrCode, convert(varchar(10), h.dDate, 23) as ArrDate, h.cVenCode, h.cDepCode,"
            + " h.cPersonCode, h.cPTCode, h.cBusType, h.cexch_name,"
            + " convert(varchar(40), convert(decimal(28,10), isnull(h.iExchRate,1))) as nflat,"
            + " convert(varchar(40), h.iTaxRate) as iTaxRate, s.cbCloser as ArrCloser,"
            + " convert(varchar(20), s.iPOsID) as PoLine, p.cPOID"
            + " from PU_ArrivalVouchs s join PU_ArrivalVouch h on h.ID=s.ID"
            + " left join PO_Podetails d on d.ID=s.iPOsID left join PO_Pomain p on p.POID=d.POID where s.Autoid=?";
        const string PoLineSql = "select ID, cInvCode, iUnitPrice, iTaxPrice, iPerTaxRate, bTaxCost from PO_Podetails where ID=?";

        public static ApiResult PurchaseInQm(WorkContext ctx, VoucherKind kind, int checkId,
            Dictionary<string, object> head, object[] lines)
        {
            CheckQm(head, lines);
            QmIn job = LoadQm(ctx.Conn, checkId, lines[0]);
            CheckGenPositions(ctx.Conn, Text(head, "cwhcode"), new string[] { job.Pos });
            return InsertQm(ctx, kind, checkId, head, job);
        }

        // 登录前校验：表头同参照采购订单的白名单，必须有仓库；表体恰好 1 行，只收 source_line_id、quantity、cbatch、cbmemo、cposition。
        internal static void CheckQm(Dictionary<string, object> head, object[] lines)
        {
            if (head == null)
            {
                throw new BridgeException(400, "bad_request", "必须指定仓库");
            }
            CheckKeys(head, true);
            if (Text(head, "cwhcode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "必须指定仓库");
            }
            string date = Text(head, "ddate");
            DateTime day;
            if (date.Length > 0 && !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw new BridgeException(400, "bad_request", "单据日期必须是 yyyy-MM-dd");
            }
            if (lines == null || lines.Length != 1)
            {
                throw new BridgeException(400, "bad_request", "参照来料检验单只能有 1 行");
            }
            Dictionary<string, object> line = MfgReq.LineOf(lines[0], true);
            MfgReq.LineId(line);
            MfgReq.LineQty(line);
        }

        // 采购单价公式，给参照采购入库生成采购发票用。src 需有 bTaxCost、iPerTaxRate、iTaxPrice、iUnitPrice。
        internal static Dictionary<string, string> PoAmounts(Dictionary<string, object> src, decimal qty, decimal exch)
        {
            return Amounts(src, qty, exch, "");
        }

        // 同上；行上没有税率时用 headRate（采购订单表头 iTaxRate）。
        internal static Dictionary<string, string> PoAmounts(Dictionary<string, object> src, decimal qty, decimal exch,
            string headRate)
        {
            return Amounts(src, qty, exch, headRate ?? "");
        }

        static QmIn LoadQm(object conn, int checkId, object raw)
        {
            Dictionary<string, object> line = MfgReq.LineOf(raw, true);
            QmIn job = new QmIn();
            job.Id = MfgReq.LineId(line);
            job.Qty = MfgReq.LineQty(line);
            if (job.Id != checkId)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            job.Qm = Rows.One(conn, QmSql, new object[] { checkId });
            if (job.Qm == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RequireQm(job.Qm);
            decimal left = Num(CoRows.Col(job.Qm, "RegQty")) + Num(CoRows.Col(job.Qm, "ConQty"))
                - Num(CoRows.Col(job.Qm, "SumQty"));
            if (job.Qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            job.Arr = Rows.One(conn, ArrSql, new object[] { AsId(CoRows.Col(job.Qm, "ArrLine")) });
            if (job.Arr == null)
            {
                throw new BridgeException(409, "state_mismatch", "检验单对应的到货单不存在");
            }
            if (CoRows.Col(job.Arr, "ArrCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "到货单行已关闭");
            }
            int poLine = AsId(CoRows.Col(job.Arr, "PoLine"));
            job.Po = poLine > 0 ? Rows.One(conn, PoLineSql, new object[] { poLine }) : null;
            if (job.Po == null || CoRows.Col(job.Arr, "cPOID").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验单没有对应的采购订单行");
            }
            job.Batch = Or(Text(line, "cbatch"), CoRows.Col(job.Qm, "CBATCH"));
            job.Memo = Text(line, "cbmemo");
            job.Pos = Text(line, "cposition");
            return job;
        }

        static void RequireQm(Dictionary<string, object> qm)
        {
            if (CoRows.Col(qm, "CVERIFIER").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(qm, "Merged") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "合并检验的检验单暂不支持生单");
            }
            if (CoRows.Col(qm, "CSOURCE") != "到货单" || AsId(CoRows.Col(qm, "ArrLine")) <= 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验单来源不是到货单");
            }
        }

        static ApiResult InsertQm(WorkContext ctx, VoucherKind kind, int checkId, Dictionary<string, object> head, QmIn job)
        {
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                domH = Blank(ctx.Conn, true);
                domB = Blank(ctx.Conn, false);
                StampQmHead(ctx, domH, head, job);
                FillQmBody(ctx.Conn, domB, job);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadMap(domH));
                StockDom.SetHeadValue(domH, "cCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                DryRun.Touched(Kinds.Find("qm_incoming_check"), checkId);
                StockCall.RunCo(ctx, co, "Insert", args, refs, null);
                int newId = Confirmed(ctx, kind, code, args[6]);
                return AfterSaved(ctx, kind, newId, code, Kinds.Find("qm_incoming_check"), checkId);
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

        static void StampQmHead(WorkContext ctx, object dom, Dictionary<string, object> head, QmIn job)
        {
            string date = Or(Text(head, "ddate"), ctx.Item.Date == null ? "" : ctx.Item.Date.Trim());
            string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            Dictionary<string, object> arr = job.Arr;
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                Put(dom, row, schema, "id", "");
                Put(dom, row, schema, "editprop", "A");
                Put(dom, row, schema, "ddate", date);
                Put(dom, row, schema, "cwhcode", Text(head, "cwhcode"));
                Put(dom, row, schema, "crdcode", StockPurIn.GenRd(ctx.Conn, head, CoRows.Col(arr, "cPTCode")));
                Copy(dom, row, schema, "cdepcode", Or(Text(head, "cdepcode"), CoRows.Col(arr, "cDepCode")));
                Copy(dom, row, schema, "cpersoncode", Or(Text(head, "cpersoncode"), CoRows.Col(arr, "cPersonCode")));
                Copy(dom, row, schema, "cvencode", CoRows.Col(arr, "cVenCode"));
                Copy(dom, row, schema, "cptcode", CoRows.Col(arr, "cPTCode"));
                Copy(dom, row, schema, "cbustype", CoRows.Col(arr, "cBusType"));
                Copy(dom, row, schema, "cexch_name", CoRows.Col(arr, "cexch_name"));
                Put(dom, row, schema, "iexchrate", Rate(CoRows.Col(arr, "nflat")).ToString("0.##########", CultureInfo.InvariantCulture));
                Copy(dom, row, schema, "itaxrate", CoRows.Col(arr, "iTaxRate"));
                Put(dom, row, schema, "csource", "来料检验单");
                Put(dom, row, schema, "cordercode", CoRows.Col(arr, "cPOID"));
                Put(dom, row, schema, "carvcode", CoRows.Col(arr, "ArrCode"));
                Copy(dom, row, schema, "darvdate", CoRows.Col(arr, "ArrDate"));
                Put(dom, row, schema, "cchkcode", CoRows.Col(job.Qm, "CCHECKCODE"));
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

        static void FillQmBody(object conn, object dom, QmIn job)
        {
            Dictionary<string, object> qm = job.Qm;
            Dictionary<string, string> amt = Amounts(job.Po, job.Qty, Rate(CoRows.Col(job.Arr, "nflat")),
                CoRows.Col(job.Arr, "iTaxRate"));
            string qty = job.Qty.ToString("0.######", CultureInfo.InvariantCulture);
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                Put(dom, row, schema, "autoid", "");
                Put(dom, row, schema, "id", "");
                Put(dom, row, schema, "editprop", "A");
                Put(dom, row, schema, "cinvcode", CoRows.Col(qm, "CINVCODE"));
                Put(dom, row, schema, "iquantity", qty);
                Put(dom, row, schema, "inquantity", qty);
                Put(dom, row, schema, "iposid", CoRows.Col(job.Arr, "PoLine"));
                Put(dom, row, schema, "cpoid", CoRows.Col(job.Arr, "cPOID"));
                Put(dom, row, schema, "iarrsid", CoRows.Col(qm, "ArrLine"));
                Put(dom, row, schema, "icheckidbaks", job.Id.ToString(CultureInfo.InvariantCulture));
                Put(dom, row, schema, "ccheckcode", CoRows.Col(qm, "CCHECKCODE"));
                Put(dom, row, schema, "corufts", CoRows.Col(qm, "QmUfts"));
                Copy(dom, row, schema, "dcheckdate", CoRows.Col(qm, "CheckDate"));
                Copy(dom, row, schema, "ccheckpersoncode", CoRows.Col(qm, "CCHECKPERSONCODE"));
                Put(dom, row, schema, "imergecheckautoid", "-1");
                Put(dom, row, schema, "irowno", "1");
                PutAmounts(dom, row, schema, amt);
                Copy(dom, row, schema, "cbatch", job.Batch);
                Copy(dom, row, schema, "cposition", job.Pos);
                Copy(dom, row, schema, "cbmemo", job.Memo);
                ApplyQty(conn, dom, row, schema);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        static void PutAmounts(object dom, object row, List<string> schema, Dictionary<string, string> amt)
        {
            string[] names = new string[]
            {
                "itaxrate", "btaxcost", "ioritaxcost", "ioricost", "iorimoney", "ioritaxprice", "iorisum",
                "iunitcost", "iprice", "itaxprice", "isum"
            };
            for (int i = 0; i < names.Length; i++)
            {
                Put(dom, row, schema, names[i], amt[names[i]]);
            }
        }

        // 删除来源为来料检验单的采购入库：U8 回退 FsumQuantity 未经实测，所以在同一事务里核对，没退就回滚。
        internal static void GuardQmUndo(StockAt at, int rdId)
        {
            QmUndo undo = new QmUndo();
            undo.RdId = rdId;
            at.Before = undo.Before;
            at.After = undo.After;
        }

        sealed class QmUndo
        {
            const string UsedSql = "select convert(varchar(20), r.iCheckIdBaks) as CheckId,"
                + " convert(varchar(40), sum(isnull(r.iQuantity,0))) as Qty from rdrecords01 r"
                + " where r.ID=? and isnull(r.iCheckIdBaks,0)<>0 group by r.iCheckIdBaks";
            const string SumSql = "select convert(varchar(40), isnull(FsumQuantity,0)) from QMCHECKVOUCHER where ID=?";

            public int RdId;
            readonly List<int> _ids = new List<int>();
            readonly List<decimal> _expect = new List<decimal>();

            public void Before(object conn)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, UsedSql, new object[] { RdId }, 500);
                for (int i = 0; rows != null && i < rows.Count; i++)
                {
                    int id = AsId(CoRows.Col(rows[i], "CheckId"));
                    decimal sum = Num(Rows.Scalar(conn, SumSql, new object[] { id }));
                    _ids.Add(id);
                    _expect.Add(sum - Num(CoRows.Col(rows[i], "Qty")));
                }
            }

            public void After(object conn)
            {
                for (int i = 0; i < _ids.Count; i++)
                {
                    decimal now = Num(Rows.Scalar(conn, SumSql, new object[] { _ids[i] }));
                    if (Math.Abs(now - _expect[i]) > 0.000001m)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 没有回退来料检验单累计入库数量");
                    }
                }
            }
        }

        sealed class QmIn
        {
            public int Id;
            public decimal Qty;
            public string Batch;
            public string Memo;
            public string Pos;
            public Dictionary<string, object> Qm;
            public Dictionary<string, object> Arr;
            public Dictionary<string, object> Po;
        }
    }
}
