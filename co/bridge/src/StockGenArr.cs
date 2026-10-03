using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购入库参照到货单（蓝字，PU_ArrivalVouch.iBillType=0）：USERPCO Insert("01")，表头 csource=采购到货单、
    // ipurarriveid=到货单 ID、carvcode / darvdate，行上 iarrsid=到货行 Autoid、cbarvcode / dbarvdate、订单关联 iposid / cpoid
    // （到货行 iPOsID）。字段按 U8 界面参照到货单生成的入库写：表头 cordercode 只在各行同一订单时写，
    // 行上多数没有 corufts。单价、税率取到货行。U8 在 Insert 里回写到货行 fValidInQuan（累计合格入库）、订单行 freceivedqty；
    // 提交前在同一事务里核对 fValidInQuan 加了本次数量（ArrGuard）。删除走 StockCo.Delete + GuardArrUndo。
    // 需要来料检验的（到货行 bGsp=1 或存货 bPropertyCheck=1）拒绝：先报检、检验，再参照来料检验单入库（StockGenQm）。
    // lines 可省：省略时按到货单各行的剩余可入库数量整单生成。仓库取表头 cwhcode，表头没给时取各行一致的 cwhcode。
    internal static partial class StockGen
    {
        // ===== 可调整：参照到货单生成蓝字入库（Z4），只改这一块 =====
        // 行上 corufts 取到货单表头 ufts；U8 界面生成的入库多数不带，缺省不写。
        static readonly bool ArrInCorufts = false;
        // ===== 可调整块结束 =====

        internal const string ArrSourceName = "arrival";
        const string ArrInSource = "采购到货单";
        const string ArrQmMessage = "到货单的存货需要来料检验，请先报检、检验后参照检验单入库";
        // 剩余可入库数量同 U8 到货单表体视图 pu_arrbody 的 fininquantity。
        const string ArrLeftExpr = "isnull(s.iQuantity,0) - isnull(s.fRefuseQuantity,0) - isnull(s.fValidInQuan,0) - isnull(s.fInValidInQuan,0)";
        const string ArrHeadSql = "select h.cCode as ArrCode, convert(varchar(10), h.dDate, 23) as ArrDate, h.cverifier as Verifier,"
            + " h.ccloser as Closer, convert(varchar(5), isnull(h.iBillType,0)) as BillType, h.cVenCode, h.cDepCode, h.cPersonCode,"
            + " h.cPTCode, h.cBusType, h.cexch_name, convert(varchar(40), convert(decimal(28,10), isnull(h.iExchRate,1))) as nflat,"
            + " convert(varchar(40), h.iTaxRate) as iTaxRate, convert(varchar(30), convert(money, h.ufts), 2) as ArrUfts"
            + " from PU_ArrivalVouch h where h.ID=?";
        const string ArrLineCols = "select convert(varchar(20), s.Autoid) as LineId, s.cInvCode,"
            + " convert(varchar(40), " + ArrLeftExpr + ") as SrcLeft, s.cbcloser as LineCloser, s.cBatch as SrcBatch,"
            + " convert(varchar(5), isnull(s.bGsp,0)) as bGsp, convert(varchar(5), isnull(i.bPropertyCheck,0)) as PropCheck,"
            + " convert(varchar(5), isnull(s.bTaxCost,0)) as bTaxCost, convert(varchar(40), s.iOriTaxCost) as iTaxPrice,"
            + " convert(varchar(40), s.iOriCost) as iUnitPrice, convert(varchar(40), s.iTaxRate) as iPerTaxRate,"
            + " convert(varchar(20), s.iPOsID) as PoLine, p.cPOID,"
            + " convert(varchar(10), s.dPDate, 23) as dmadedate, convert(varchar(10), s.dVDate, 23) as dvdate,"
            + " convert(varchar(20), s.imassdate) as imassdate, convert(varchar(20), s.cmassunit) as cmassunit,"
            + " s.cExpirationdate as cexpirationdate, convert(varchar(10), s.dExpirationdate, 23) as dexpirationdate,"
            + " convert(varchar(20), s.iExpiratDateCalcu) as iexpiratdatecalcu,"
            + " s.cFree1 as cfree1, s.cFree2 as cfree2, s.cFree3 as cfree3, s.cFree4 as cfree4, s.cFree5 as cfree5,"
            + " s.cFree6 as cfree6, s.cFree7 as cfree7, s.cFree8 as cfree8, s.cFree9 as cfree9, s.cFree10 as cfree10"
            + " from PU_ArrivalVouchs s left join Inventory i on i.cInvCode=s.cInvCode"
            + " left join PO_Podetails d on d.ID=s.iPOsID left join PO_Pomain p on p.POID=d.POID";
        const string ArrLineSql = ArrLineCols + " where s.Autoid=? and s.ID=?";
        const string ArrAllSql = ArrLineCols + " where s.ID=? order by s.Autoid";
        const string ArrInLeftSql = "select convert(varchar(40), " + ArrLeftExpr
            + ") from PU_ArrivalVouchs s with (updlock, holdlock) where s.Autoid=?";
        // 沿用到货行、U8 参照到货单时带入入库行的保质期与自由项（到货行有值才写）。
        static readonly string[] ArrCopyCols = new string[]
        {
            "dmadedate", "dvdate", "imassdate", "cmassunit", "cexpirationdate", "dexpirationdate", "iexpiratdatecalcu",
            "cfree1", "cfree2", "cfree3", "cfree4", "cfree5", "cfree6", "cfree7", "cfree8", "cfree9", "cfree10"
        };

        // 采购入库生单按来源分派（从 Dispatch.GeneratePu 移来，圈复杂度上限）：来料检验单（StockGenQm）、
        // 采购退货单生成红字（StockGenRed）、到货单（本文件），其余参照采购订单（StockGen）。
        internal static ApiResult PurchaseInFrom(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, object[] lines)
        {
            string source = ctx.Item.Source == null ? "" : ctx.Item.Source.Name ?? "";
            if (source == "qm_incoming_check")
            {
                return PurchaseInQm(ctx, kind, sourceId, head, lines);
            }
            if (source == "purchase_return")
            {
                return PurchaseInReturn(ctx, kind, sourceId, head, lines);
            }
            if (source == ArrSourceName)
            {
                return PurchaseInArrival(ctx, kind, sourceId, head, lines);
            }
            return PurchaseIn(ctx, kind, sourceId, head, lines);
        }

        // 登录前校验采购入库生单：参照来料检验单（CheckQm）、参照到货单（CheckArr）；其余来源在登录后校验。
        internal static void CheckGenerateIn(WorkItem item)
        {
            string source = item.Source == null ? "" : item.Source.Name ?? "";
            if (source == "qm_incoming_check")
            {
                CheckQm(item.Head, item.Lines);
            }
            else if (source == ArrSourceName)
            {
                CheckArr(item.Head, item.Lines);
            }
        }

        internal static bool FromArrival(WorkItem item)
        {
            return item != null && item.Type != null && item.Type.Name == "purchase_in"
                && item.Source != null && item.Source.Name == ArrSourceName;
        }

        // 登录前校验：表头同参照采购订单的白名单，单据日期 yyyy-MM-dd；表体 0 到 200 行（重复、数量由 Json.OneGenerate 核过）；
        // 必须能定出唯一的仓库。
        internal static void CheckArr(Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, object> map = head ?? new Dictionary<string, object>();
            CheckKeys(map, true);
            string date = Text(map, "ddate");
            DateTime day;
            if (date.Length > 0 && !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw BridgeException.BadField("head.ddate", "单据日期必须是 yyyy-MM-dd");
            }
            if (lines != null && lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 0 到 200 行");
            }
            for (int i = 0; lines != null && i < lines.Length; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line == null)
                {
                    throw FieldPath.Index(BridgeException.BadField("", "表体行不是对象"), "lines", i);
                }
                try
                {
                    CheckKeys(line, false);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            ArrWarehouse(map, lines);
        }

        // 一张采购入库单只有一个仓库：表头 cwhcode 优先，否则取各行的 cwhcode；行上给的必须与之相同。
        static string ArrWarehouse(Dictionary<string, object> head, object[] lines)
        {
            string wh = head == null ? "" : Text(head, "cwhcode");
            for (int i = 0; lines != null && i < lines.Length; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                string own = line == null ? "" : Text(line, "cwhcode");
                if (own.Length == 0)
                {
                    continue;
                }
                if (wh.Length == 0)
                {
                    wh = own;
                }
                else if (!string.Equals(wh, own, StringComparison.OrdinalIgnoreCase))
                {
                    throw FieldPath.Index(BridgeException.BadField("cwhcode", "一张采购入库单只能有一个仓库"), "lines", i);
                }
            }
            if (wh.Length == 0)
            {
                throw BridgeException.BadField("head.cwhcode", "必须指定仓库");
            }
            return wh;
        }

        public static ApiResult PurchaseInArrival(WorkContext ctx, VoucherKind kind, int arrId,
            Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, object> map = head ?? new Dictionary<string, object>();
            CheckArr(map, lines);
            ArrJob job = new ArrJob();
            job.ArrId = arrId;
            job.Head = map;
            job.Wh = ArrWarehouse(map, lines);
            job.Arr = LoadArr(ctx.Conn, arrId);
            job.Lines = lines == null || lines.Length == 0 ? ArrAll(ctx.Conn, arrId) : ArrPicked(ctx.Conn, arrId, lines);
            List<string> codes = new List<string>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                codes.Add(job.Lines[i].Pos);
            }
            CheckGenPositions(ctx.Conn, job.Wh, codes);
            return InsertArr(ctx, kind, job);
        }

        // 来源须是蓝字到货单（iBillType=0）、已审核、未关闭、普通采购。
        static Dictionary<string, object> LoadArr(object conn, int arrId)
        {
            Dictionary<string, object> arr = Rows.One(conn, ArrHeadSql, new object[] { arrId });
            if (arr == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(arr, "BillType") != "0")
            {
                throw new BridgeException(409, "state_mismatch", "来源须为蓝字到货单");
            }
            if (CoRows.Col(arr, "Verifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(arr, "Closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (CoRows.Col(arr, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持普通采购的来源单据");
            }
            return arr;
        }

        static List<ArrIn> ArrPicked(object conn, int arrId, object[] lines)
        {
            List<ArrIn> list = new List<ArrIn>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    list.Add(ArrPickedLine(conn, arrId, (Dictionary<string, object>)lines[i]));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return list;
        }

        static ArrIn ArrPickedLine(object conn, int arrId, Dictionary<string, object> line)
        {
            ArrIn item = new ArrIn();
            item.LineId = AsId(Raw(line, "source_line_id"));
            if (item.LineId <= 0)
            {
                throw BridgeException.BadField("source_line_id", "明细行不存在");
            }
            item.Qty = QtyOf(Raw(line, "quantity"));
            item.Src = Rows.One(conn, ArrLineSql, new object[] { item.LineId, arrId });
            if (item.Src == null)
            {
                throw BridgeException.BadField("source_line_id", "明细行不存在");
            }
            RequireArrLine(item.Src);
            RequireArrLeft(item.Qty, Num(CoRows.Col(item.Src, "SrcLeft")));
            item.Batch = Or(Text(line, "cbatch"), CoRows.Col(item.Src, "SrcBatch"));
            item.Memo = Text(line, "cbmemo");
            item.Pos = Text(line, "cposition");
            return item;
        }

        // 不带 lines：到货单上未关闭、剩余可入库数量大于 0 的行全部按剩余数量生成。
        static List<ArrIn> ArrAll(object conn, int arrId)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, ArrAllSql, new object[] { arrId }, 500);
            List<ArrIn> list = new List<ArrIn>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                decimal left = Num(CoRows.Col(rows[i], "SrcLeft"));
                if (CoRows.Col(rows[i], "LineCloser").Length > 0 || left <= 0m)
                {
                    continue;
                }
                RequireArrLine(rows[i]);
                ArrIn item = new ArrIn();
                item.LineId = AsId(CoRows.Col(rows[i], "LineId"));
                item.Qty = left;
                item.Src = rows[i];
                item.Batch = CoRows.Col(rows[i], "SrcBatch");
                item.Memo = "";
                item.Pos = "";
                list.Add(item);
            }
            if (list.Count == 0)
            {
                throw new BridgeException(409, "state_mismatch", "到货单没有可入库的数量");
            }
            if (list.Count > 200)
            {
                throw new BridgeException(409, "state_mismatch", "到货单可入库的行超过 200 行，请用 lines 分次生成");
            }
            return list;
        }

        static void RequireArrLine(Dictionary<string, object> src)
        {
            if (CoRows.Col(src, "LineCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "到货单行已关闭");
            }
            if (CoRows.Col(src, "bGsp") == "1" || CoRows.Col(src, "PropCheck") == "1")
            {
                throw new BridgeException(409, "state_mismatch", ArrQmMessage);
            }
        }

        static void RequireArrLeft(decimal qty, decimal left)
        {
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
        }

        static ApiResult InsertArr(WorkContext ctx, VoucherKind kind, ArrJob job)
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
                StampArrHead(ctx, domH, job);
                FillArrBody(ctx.Conn, domB, job);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadMap(domH));
                StockDom.SetHeadValue(domH, "cCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockAt at = StockCall.AtFor("Insert", args, refs, null);
                ArrGuard guard = new ArrGuard(ctx, job.Lines);
                at.Before = guard.Before;
                at.After = guard.After;
                DryRun.Touched(Kinds.Find(ArrSourceName), job.ArrId);
                StockCall.RunAt(ctx, co, at);
                int newId = Confirmed(ctx, kind, code, args[6]);
                return AfterSaved(ctx, kind, newId, code, Kinds.Find(ArrSourceName), job.ArrId);
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

        static void StampArrHead(WorkContext ctx, object dom, ArrJob job)
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
                Copy(dom, row, schema, "cvencode", CoRows.Col(arr, "cVenCode"));
                Copy(dom, row, schema, "cptcode", CoRows.Col(arr, "cPTCode"));
                Copy(dom, row, schema, "cbustype", CoRows.Col(arr, "cBusType"));
                Copy(dom, row, schema, "cexch_name", CoRows.Col(arr, "cexch_name"));
                Put(dom, row, schema, "iexchrate", Rate(CoRows.Col(arr, "nflat")).ToString("0.##########", CultureInfo.InvariantCulture));
                Copy(dom, row, schema, "itaxrate", CoRows.Col(arr, "iTaxRate"));
                Put(dom, row, schema, "csource", ArrInSource);
                Copy(dom, row, schema, "cordercode", OnePo(job.Lines));
                Put(dom, row, schema, "carvcode", CoRows.Col(arr, "ArrCode"));
                Copy(dom, row, schema, "darvdate", CoRows.Col(arr, "ArrDate"));
                Put(dom, row, schema, "ipurarriveid", job.ArrId.ToString(CultureInfo.InvariantCulture));
                Put(dom, row, schema, "bredvouch", "0");
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

        // 各行同一张采购订单时返回订单号，否则空（U8 界面跨订单参照时表头 cOrderCode 为空）。
        static string OnePo(List<ArrIn> lines)
        {
            string po = "";
            for (int i = 0; i < lines.Count; i++)
            {
                string own = CoRows.Col(lines[i].Src, "cPOID");
                if (own.Length == 0 || (po.Length > 0 && po != own))
                {
                    return "";
                }
                po = own;
            }
            return po;
        }

        static void FillArrBody(object conn, object dom, ArrJob job)
        {
            decimal exch = Rate(CoRows.Col(job.Arr, "nflat"));
            string headRate = CoRows.Col(job.Arr, "iTaxRate");
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    StampArrLine(dom, row, schema, job, job.Lines[i]);
                    PutAmounts(dom, row, schema, Amounts(job.Lines[i].Src, job.Lines[i].Qty, exch, headRate));
                    Put(dom, row, schema, "irowno", (i + 1).ToString(CultureInfo.InvariantCulture));
                    ApplyQty(conn, dom, row, schema);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        static void StampArrLine(object dom, object row, List<string> schema, ArrJob job, ArrIn line)
        {
            Dictionary<string, object> src = line.Src;
            string qty = line.Qty.ToString("0.######", CultureInfo.InvariantCulture);
            Put(dom, row, schema, "autoid", "");
            Put(dom, row, schema, "id", "");
            Put(dom, row, schema, "editprop", "A");
            Put(dom, row, schema, "cinvcode", CoRows.Col(src, "cInvCode"));
            Put(dom, row, schema, "iquantity", qty);
            Put(dom, row, schema, "inquantity", qty);
            Put(dom, row, schema, "iarrsid", line.LineId.ToString(CultureInfo.InvariantCulture));
            Put(dom, row, schema, "cbarvcode", CoRows.Col(job.Arr, "ArrCode"));
            Copy(dom, row, schema, "dbarvdate", CoRows.Col(job.Arr, "ArrDate"));
            Copy(dom, row, schema, "iposid", CoRows.Col(src, "PoLine"));
            Copy(dom, row, schema, "cpoid", CoRows.Col(src, "cPOID"));
            if (ArrInCorufts)
            {
                Copy(dom, row, schema, "corufts", CoRows.Col(job.Arr, "ArrUfts"));
            }
            for (int i = 0; i < ArrCopyCols.Length; i++)
            {
                Copy(dom, row, schema, ArrCopyCols[i], CoRows.Col(src, ArrCopyCols[i]));
            }
            Copy(dom, row, schema, "cbatch", line.Batch);
            Copy(dom, row, schema, "cposition", line.Pos);
            Copy(dom, row, schema, "cbmemo", line.Memo);
        }

        // Insert 前在同一事务里加锁重读剩余可入库数量并记下 fValidInQuan、订单行 iReceivedQTY；提交前核对 fValidInQuan 加了本次数量。
        sealed class ArrGuard
        {
            readonly WorkContext _ctx;
            readonly List<ArrIn> _lines;
            readonly List<decimal> _valid = new List<decimal>();
            readonly List<decimal> _recv = new List<decimal>();

            public ArrGuard(WorkContext ctx, List<ArrIn> lines)
            {
                _ctx = ctx;
                _lines = lines;
            }

            public void Before(object conn)
            {
                for (int i = 0; i < _lines.Count; i++)
                {
                    ArrIn line = _lines[i];
                    RequireArrLeft(line.Qty, Num(Rows.Scalar(conn, ArrInLeftSql, new object[] { line.LineId })));
                    _valid.Add(Num(Rows.Scalar(conn, RetInSql, new object[] { line.LineId })));
                    _recv.Add(PoRecvOf(conn, line.Src));
                }
            }

            public void After(object conn)
            {
                for (int i = 0; i < _lines.Count; i++)
                {
                    ArrIn line = _lines[i];
                    decimal now = Num(Rows.Scalar(conn, RetInSql, new object[] { line.LineId }));
                    // 订单行累计入库只记审计（U8 界面生成的入库里订单行 freceivedqty 有值、iReceivedQTY 可能为空）。
                    CoRows.Note(_ctx.Item, "到货行 " + line.LineId.ToString(CultureInfo.InvariantCulture)
                        + " iReceivedQTY " + _recv[i].ToString(CultureInfo.InvariantCulture)
                        + "→" + PoRecvOf(conn, line.Src).ToString(CultureInfo.InvariantCulture));
                    if (Math.Abs(now - _valid[i] - line.Qty) > 0.000001m)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 没有回写到货单累计入库数量");
                    }
                }
            }

            static decimal PoRecvOf(object conn, Dictionary<string, object> src)
            {
                int po = AsId(CoRows.Col(src, "PoLine"));
                return po > 0 ? Num(Rows.Scalar(conn, PoRecvSql, new object[] { po })) : 0m;
            }
        }

        sealed class ArrJob
        {
            public int ArrId;
            public string Wh;
            public Dictionary<string, object> Head;
            public Dictionary<string, object> Arr;
            public List<ArrIn> Lines;
        }

        sealed class ArrIn
        {
            public int LineId;
            public decimal Qty;
            public string Batch;
            public string Memo;
            public string Pos;
            public Dictionary<string, object> Src;
        }
    }
}
