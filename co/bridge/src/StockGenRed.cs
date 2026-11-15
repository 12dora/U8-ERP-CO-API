using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红字采购入库单参照采购退货单（红字到货单，PU_ArrivalVouch.iBillType=1）：USERPCO Insert("01")，表头 bredvouch=1、
    // csource=采购到货单（ST.VouchSource 枚举里没有「采购退货单」），行数量为负，行上带 iarrsid = 退货单行 Autoid。
    // U8 的待入库视图（pu_v_preparestockbyarrforst）对退货行按 iQuantity − fValidInQuan − fInValidInQuan（负数）算可入库，
    // 所以保存后退货行 fValidInQuan 应按本次负数量移动（蓝字参照到货单的入库 fValidInQuan = 累计入库，已对照）；
    // 提交前在同一事务里核对。订单行 iReceivedQTY 只记审计。
    internal static partial class StockGen
    {
        // ===== 可调整：参照采购退货单生成红字入库，只改这一块 =====
        const string RedInSource = "采购到货单";
        // 行上 corufts 取退货单表头 ufts（参照来料检验单时取检验单的，参照订单时 U8 不要）。
        const bool RedInCorufts = true;
        // ===== 可调整块结束 =====

        const string RetHeadSql = "select h.cCode as ArrCode, convert(varchar(10), h.dDate, 23) as ArrDate, h.cverifier as Verifier,"
            + " h.ccloser as Closer, convert(varchar(5), isnull(h.iBillType,0)) as BillType, h.cVenCode, h.cDepCode, h.cPersonCode,"
            + " h.cPTCode, h.cBusType, h.cexch_name, convert(varchar(40), convert(decimal(28,10), isnull(h.iExchRate,1))) as nflat,"
            + " convert(varchar(40), h.iTaxRate) as iTaxRate, h.cpocode as PoCode,"
            + " convert(varchar(5), isnull(h.iswfcontrolled,0)) as Wf, convert(varchar(30), convert(money, h.ufts), 2) as ArrUfts"
            + " from PU_ArrivalVouch h where h.ID=?";
        const string RetLeftExpr = "isnull(s.iQuantity,0) - isnull(s.fValidInQuan,0) - isnull(s.fInValidInQuan,0)";
        static readonly string RetLineSql = "select s.cInvCode, convert(varchar(40), isnull(s.iQuantity,0)) as SrcQty,"
            + " convert(varchar(40), " + RetLeftExpr + ") as SrcLeft, s.cbcloser as LineCloser, s.cBatch as SrcBatch,"
            + " convert(varchar(5), isnull(s.bTaxCost,0)) as bTaxCost, convert(varchar(40), s.iOriTaxCost) as iTaxPrice,"
            + " convert(varchar(40), s.iOriCost) as iUnitPrice, convert(varchar(40), s.iTaxRate) as iPerTaxRate,"
            + " convert(varchar(20), s.iPOsID) as PoLine, p.cPOID"
            + " from PU_ArrivalVouchs s left join PO_Podetails d on d.ID=s.iPOsID left join PO_Pomain p on p.POID=d.POID"
            + " where s.Autoid=? and s.ID=?";
        static readonly string RetInLeftSql = "select convert(varchar(40), " + RetLeftExpr
            + ") from PU_ArrivalVouchs s with (updlock, holdlock) where s.Autoid=?";
        const string RetInSql = "select convert(varchar(40), isnull(fValidInQuan,0)) from PU_ArrivalVouchs where Autoid=?";
        const string PoRecvSql = "select convert(varchar(40), isnull(iReceivedQTY,0)) from PO_Podetails where ID=?";

        // 表头同参照采购订单的白名单，必须有仓库；表体 1 到 200 行，source_line_id 是退货单行 Autoid，quantity 填正数（桥写负数）。
        public static ApiResult PurchaseInReturn(WorkContext ctx, VoucherKind kind, int retId,
            Dictionary<string, object> head, object[] lines)
        {
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            CheckKeys(head, true);
            if (Text(head, "cwhcode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "必须指定仓库");
            }
            Dictionary<string, object> ret = LoadRet(ctx.Conn, retId);
            List<RedIn> want = LoadRetLines(ctx.Conn, retId, lines);
            List<string> codes = new List<string>();
            for (int i = 0; i < want.Count; i++)
            {
                codes.Add(want[i].Pos);
            }
            // 红字入库从货位出：货位仓同样每行必须指定货位（不按原蓝字入库行自动带出，调用方给）。
            CheckGenPositions(ctx.Conn, Text(head, "cwhcode"), codes);
            return InsertRed(ctx, kind, retId, head, ret, want);
        }

        // 来源须是采购退货单（iBillType=1）、已审核、未关闭、普通采购、不在审批流里。
        static Dictionary<string, object> LoadRet(object conn, int retId)
        {
            Dictionary<string, object> ret = Rows.One(conn, RetHeadSql, new object[] { retId });
            if (ret == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(ret, "BillType") != "1")
            {
                throw new BridgeException(409, "state_mismatch", "来源须为采购退货单");
            }
            if (CoRows.Col(ret, "Verifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(ret, "Closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (CoRows.Col(ret, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持普通采购的来源单据");
            }
            if (CoRows.Col(ret, "Wf") == "1")
            {
                throw new BridgeException(409, "workflow_enabled", "来源单据受审批流控制，本期不支持");
            }
            return ret;
        }

        static List<RedIn> LoadRetLines(object conn, int retId, object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体须为 1 到 200 行");
            }
            HashSet<int> seen = new HashSet<int>();
            List<RedIn> list = new List<RedIn>();
            for (int i = 0; i < lines.Length; i++)
            {
                RedIn item = RetLine(conn, retId, lines[i]);
                if (!seen.Add(item.LineId))
                {
                    throw new BridgeException(400, "bad_request", "来源明细重复");
                }
                list.Add(item);
            }
            return list;
        }

        static RedIn RetLine(object conn, int retId, object raw)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            CheckKeys(line, false);
            RedIn item = new RedIn();
            item.LineId = AsId(Raw(line, "source_line_id"));
            if (item.LineId <= 0)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            item.Qty = QtyOf(Raw(line, "quantity"));
            item.Src = Rows.One(conn, RetLineSql, new object[] { item.LineId, retId });
            if (item.Src == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (CoRows.Col(item.Src, "LineCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            // 退的是已到货（多半已检验）的货，不再按 bPropertyCheck 拒绝（蓝字参照订单才拒绝）。
            RequireRetLeft(item, Num(CoRows.Col(item.Src, "SrcLeft")));
            item.Batch = Or(Text(line, "cbatch"), CoRows.Col(item.Src, "SrcBatch"));
            item.Memo = Text(line, "cbmemo");
            item.Pos = Text(line, "cposition");
            return item;
        }

        // 可入库数量是负数（退货行 iQuantity − fValidInQuan − fInValidInQuan）；本次（正数）不超过它的绝对值。
        static void RequireRetLeft(RedIn item, decimal left)
        {
            if (item.Qty > -left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
        }

        static ApiResult InsertRed(WorkContext ctx, VoucherKind kind, int retId, Dictionary<string, object> head,
            Dictionary<string, object> ret, List<RedIn> want)
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
                StampRedHead(ctx, domH, head, ret, retId);
                FillRedBody(ctx.Conn, domB, ret, want);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadMap(domH));
                StockDom.SetHeadValue(domH, "cCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockAt at = StockCall.AtFor("Insert", args, refs, null);
                RedGuard guard = new RedGuard(ctx, want);
                at.Before = guard.Before;
                at.After = guard.After;
                DryRun.Touched(Kinds.Find("purchase_return"), retId);
                StockCall.RunAt(ctx, co, at);
                int newId = Confirmed(ctx, kind, code, args[6]);
                return AfterSaved(ctx, kind, newId, code, Kinds.Find("purchase_return"), retId);
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

        static void StampRedHead(WorkContext ctx, object dom, Dictionary<string, object> head,
            Dictionary<string, object> ret, int retId)
        {
            string date = Or(Text(head, "ddate"), ctx.Item.Date == null ? "" : ctx.Item.Date.Trim());
            string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                Put(dom, row, schema, "id", "");
                Put(dom, row, schema, "editprop", "A");
                Put(dom, row, schema, "ddate", date);
                Put(dom, row, schema, "cwhcode", Text(head, "cwhcode"));
                Put(dom, row, schema, "crdcode", StockPurIn.GenRd(ctx.Conn, head, CoRows.Col(ret, "cPTCode")));
                Copy(dom, row, schema, "cdepcode", Or(Text(head, "cdepcode"), CoRows.Col(ret, "cDepCode")));
                Copy(dom, row, schema, "cpersoncode", Or(Text(head, "cpersoncode"), CoRows.Col(ret, "cPersonCode")));
                Copy(dom, row, schema, "cvencode", CoRows.Col(ret, "cVenCode"));
                Copy(dom, row, schema, "cptcode", CoRows.Col(ret, "cPTCode"));
                Copy(dom, row, schema, "cbustype", CoRows.Col(ret, "cBusType"));
                Copy(dom, row, schema, "cexch_name", CoRows.Col(ret, "cexch_name"));
                Put(dom, row, schema, "iexchrate", Rate(CoRows.Col(ret, "nflat")).ToString("0.##########", CultureInfo.InvariantCulture));
                Copy(dom, row, schema, "itaxrate", CoRows.Col(ret, "iTaxRate"));
                Put(dom, row, schema, "csource", RedInSource);
                Copy(dom, row, schema, "cordercode", CoRows.Col(ret, "PoCode"));
                Put(dom, row, schema, "carvcode", CoRows.Col(ret, "ArrCode"));
                Copy(dom, row, schema, "darvdate", CoRows.Col(ret, "ArrDate"));
                // U8 参照到货单生成的入库写 ipurarriveid（=到货单 ID），iarriveid 留空。
                Put(dom, row, schema, "ipurarriveid", retId.ToString(CultureInfo.InvariantCulture));
                Put(dom, row, schema, "bredvouch", "1");
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

        static void FillRedBody(object conn, object dom, Dictionary<string, object> ret, List<RedIn> want)
        {
            decimal exch = Rate(CoRows.Col(ret, "nflat"));
            string headRate = CoRows.Col(ret, "iTaxRate");
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < want.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    StampRedLine(dom, row, schema, ret, want[i]);
                    // 负数量：金额随之为负，单价仍为正（同 PuRet）。
                    PutAmounts(dom, row, schema, Amounts(want[i].Src, -want[i].Qty, exch, headRate));
                    Put(dom, row, schema, "irowno", (i + 1).ToString(CultureInfo.InvariantCulture));
                    ApplyQty(conn, dom, row, schema);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        static void StampRedLine(object dom, object row, List<string> schema, Dictionary<string, object> ret, RedIn line)
        {
            Dictionary<string, object> src = line.Src;
            string qty = (-line.Qty).ToString("0.######", CultureInfo.InvariantCulture);
            Put(dom, row, schema, "autoid", "");
            Put(dom, row, schema, "id", "");
            Put(dom, row, schema, "editprop", "A");
            Put(dom, row, schema, "cinvcode", CoRows.Col(src, "cInvCode"));
            Put(dom, row, schema, "iquantity", qty);
            Put(dom, row, schema, "inquantity", qty);
            Put(dom, row, schema, "iarrsid", line.LineId.ToString(CultureInfo.InvariantCulture));
            Put(dom, row, schema, "cbarvcode", CoRows.Col(ret, "ArrCode"));
            Copy(dom, row, schema, "dbarvdate", CoRows.Col(ret, "ArrDate"));
            Copy(dom, row, schema, "iposid", CoRows.Col(src, "PoLine"));
            Copy(dom, row, schema, "cpoid", CoRows.Col(src, "cPOID"));
            if (RedInCorufts)
            {
                Copy(dom, row, schema, "corufts", CoRows.Col(ret, "ArrUfts"));
            }
            Copy(dom, row, schema, "cbatch", line.Batch);
            Copy(dom, row, schema, "cposition", line.Pos);
            Copy(dom, row, schema, "cbmemo", line.Memo);
        }

        // Insert 前在同一事务里加锁重读可入库数量并记下 fValidInQuan、订单行 iReceivedQTY；提交前核对 fValidInQuan 移动了 −数量。
        sealed class RedGuard
        {
            readonly WorkContext _ctx;
            readonly List<RedIn> _lines;
            readonly List<decimal> _valid = new List<decimal>();
            readonly List<decimal> _recv = new List<decimal>();

            public RedGuard(WorkContext ctx, List<RedIn> lines)
            {
                _ctx = ctx;
                _lines = lines;
            }

            public void Before(object conn)
            {
                for (int i = 0; i < _lines.Count; i++)
                {
                    RedIn line = _lines[i];
                    RequireRetLeft(line, Num(Rows.Scalar(conn, RetInLeftSql, new object[] { line.LineId })));
                    _valid.Add(Num(Rows.Scalar(conn, RetInSql, new object[] { line.LineId })));
                    _recv.Add(PoRecv(conn, line));
                }
            }

            public void After(object conn)
            {
                for (int i = 0; i < _lines.Count; i++)
                {
                    RedIn line = _lines[i];
                    decimal now = Num(Rows.Scalar(conn, RetInSql, new object[] { line.LineId }));
                    // 未覆盖：订单行累计入库是否也减去本次数量，先只记审计。
                    CoRows.Note(_ctx.Item, "退货行 " + line.LineId.ToString(CultureInfo.InvariantCulture)
                        + " iReceivedQTY " + _recv[i].ToString(CultureInfo.InvariantCulture)
                        + "→" + PoRecv(conn, line).ToString(CultureInfo.InvariantCulture));
                    if (Math.Abs(now - _valid[i] + line.Qty) > 0.000001m)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 没有回写采购退货单累计入库数量");
                    }
                }
            }

            static decimal PoRecv(object conn, RedIn line)
            {
                int po = AsId(CoRows.Col(line.Src, "PoLine"));
                return po > 0 ? Num(Rows.Scalar(conn, PoRecvSql, new object[] { po })) : 0m;
            }
        }

        // 删除来源为采购到货单的采购入库（参照到货单生成的蓝字、参照采购退货单生成的红字，StockCo.RefuseBuy 都放行）：
        // 在同一事务里核对到货 / 退货行 fValidInQuan（删后 = 删前 − 本单该行入库数量，红字为负所以是加回），没退就回滚。
        // 红字删除回写已在测试账套核对；蓝字路径尚未核对。由 StockCo.Delete 接入。
        internal static void GuardArrUndo(StockAt at, int rdId)
        {
            ArrUndo undo = new ArrUndo();
            undo.RdId = rdId;
            at.Before = undo.Before;
            at.After = undo.After;
        }

        sealed class ArrUndo
        {
            const string UsedSql = "select convert(varchar(20), r.iArrsId) as ArrLine,"
                + " convert(varchar(40), sum(isnull(r.iQuantity,0))) as Qty from rdrecords01 r"
                + " where r.ID=? and isnull(r.iArrsId,0)<>0 group by r.iArrsId";

            public int RdId;
            readonly List<int> _ids = new List<int>();
            readonly List<decimal> _expect = new List<decimal>();

            public void Before(object conn)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, UsedSql, new object[] { RdId }, 500);
                for (int i = 0; rows != null && i < rows.Count; i++)
                {
                    int id = AsId(CoRows.Col(rows[i], "ArrLine"));
                    decimal valid = Num(Rows.Scalar(conn, RetInSql, new object[] { id }));
                    _ids.Add(id);
                    _expect.Add(valid - Num(CoRows.Col(rows[i], "Qty")));
                }
            }

            public void After(object conn)
            {
                for (int i = 0; i < _ids.Count; i++)
                {
                    decimal now = Num(Rows.Scalar(conn, RetInSql, new object[] { _ids[i] }));
                    if (Math.Abs(now - _expect[i]) > 0.000001m)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 没有回退到货单累计入库数量");
                    }
                }
            }
        }

        sealed class RedIn
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
