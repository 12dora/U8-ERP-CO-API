using System;
using System.Collections.Generic;

namespace U8Co
{
    // 调拨单参照调拨申请单：USERPCO Insert("12")，表体 itrids = 申请单行 autoID（同采购入库参照订单的 iposid），
    // 不用 MakeTransVouchFromTR（参数未实测）。U8 的 DMO 在保存时按 itrids 累加申请单行 iTvSumQuantity / iTVSumNum（已在测试账套核对），
    // 提交前在同一事务里核对；删除这张调拨单时核对回退（GuardTrUndo）。
    // 可调拨数量 = 核准数量 iTvChkQuantity − 累计调拨 iTvSumQuantity；ST.bOverTransRequestTransfer 为真时不设上限（U8 自己管）。
    internal static partial class StockGen
    {
        // ===== 已在测试账套核对：U8 自己回写 iTvSumQuantity，保持 false；改为 true 时由桥在同一事务里自己加减 =====
        const bool TrBridgeWritesSum = false;
        // ===== 开关结束 =====

        const string TrHeadSql = "select h.cTVCode, h.cVerifyPerson, h.cCloser, convert(varchar(5), isnull(h.iswfcontrolled,0)) as Wf,"
            + " h.cOWhCode, h.cIWhCode, h.cODepCode, h.cIDepCode, h.cPersonCode, h.cORdCode, h.cIRdCode"
            + " from ST_AppTransVouch h where h.ID=?";
        const string TrLineSql = "select convert(varchar(20), b.autoID) as itrids, b.cInvCode as cinvcode, b.cTVBatch as ctvbatch,"
            + " b.cAssUnit as cassunit, convert(varchar(40), b.iinvexchrate) as iinvexchrate,"
            + " convert(varchar(10), b.dMadeDate, 23) as dmadedate, convert(varchar(10), b.dDisDate, 23) as ddisdate,"
            + " convert(varchar(20), b.iMassDate) as imassdate, convert(varchar(20), b.cMassUnit) as cmassunit,"
            + " convert(varchar(20), b.iExpiratDateCalcu) as iexpiratdatecalcu, b.cExpirationdate as cexpirationdate,"
            + " convert(varchar(10), b.dExpirationdate, 23) as dexpirationdate, b.cItemCode as citemcode, b.cItem_class as citem_class,"
            + " b.cFree1 as cfree1, b.cFree2 as cfree2, b.cFree3 as cfree3, b.cFree4 as cfree4, b.cFree5 as cfree5,"
            + " b.cFree6 as cfree6, b.cFree7 as cfree7, b.cFree8 as cfree8, b.cFree9 as cfree9, b.cFree10 as cfree10"
            + " from ST_AppTransVouchs b where b.autoID=? and b.ID=?";
        const string TrLeftSql = "select b.cBCloser as Closer, convert(varchar(40), isnull(b.iTvChkQuantity,0)) as Chk,"
            + " convert(varchar(40), isnull(b.iTvSumQuantity,0)) as Summed from ST_AppTransVouchs b";
        const string TrOverSql = "select cValue from AccInformation where cSysID='ST' and cName='bOverTransRequestTransfer'";

        public static ApiResult TransferFromRequest(WorkContext ctx, VoucherKind kind, int reqId,
            Dictionary<string, object> head, object[] lines)
        {
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            CheckTrKeys(head, true);
            Dictionary<string, object> req = LoadTrHead(ctx.Conn, reqId);
            List<TrLine> want = LoadTrLines(ctx.Conn, reqId, lines, TrOver(ctx.Conn));
            return InsertTr(ctx, kind, reqId, head, req, want);
        }

        // 来源须已审核、未关闭、不受审批流控制。
        static Dictionary<string, object> LoadTrHead(object conn, int reqId)
        {
            Dictionary<string, object> req = Rows.One(conn, TrHeadSql, new object[] { reqId });
            if (req == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(req, "cVerifyPerson").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(req, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (CoRows.Col(req, "Wf") == "1")
            {
                throw new BridgeException(409, "workflow_enabled", "来源单据受审批流控制，本期不支持");
            }
            return req;
        }

        static bool TrOver(object conn)
        {
            string value = Rows.Scalar(conn, TrOverSql, new object[0]);
            return value != null && Values.Flag(value.Trim());
        }

        static List<TrLine> LoadTrLines(object conn, int reqId, object[] lines, bool over)
        {
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            HashSet<int> seen = new HashSet<int>();
            List<TrLine> list = new List<TrLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                TrLine item = TrLineOf(conn, reqId, lines[i]);
                if (!seen.Add(item.LineId))
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".source_line_id", "来源明细重复");
                }
                item.Over = over;
                RequireTrLeft(item, Rows.One(conn, TrLeftSql + " where b.autoID=?", new object[] { item.LineId }));
                list.Add(item);
            }
            return list;
        }

        static TrLine TrLineOf(object conn, int reqId, object raw)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw BridgeException.BadField("lines", "表体行不是对象");
            }
            CheckTrKeys(line, false);
            TrLine item = new TrLine();
            item.LineId = AsId(Raw(line, "source_line_id"));
            item.Qty = QtyOf(Raw(line, "quantity"));
            StockDom.MiscQtyOk(Values.Text(Raw(line, "quantity")), false);
            item.Src = item.LineId > 0 ? Rows.One(conn, TrLineSql, new object[] { item.LineId, reqId }) : null;
            if (item.Src == null)
            {
                throw BridgeException.BadField("lines.source_line_id", "明细行不存在");
            }
            item.Memo = Text(line, "cbmemo");
            return item;
        }

        // 行未关闭、核准数量大于 0；没开「允许超调拨申请调拨」时，本次不超过核准 − 累计调拨。事务里加锁再查一次（TrGuard.Before）。
        static decimal RequireTrLeft(TrLine item, Dictionary<string, object> left)
        {
            if (left == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (CoRows.Col(left, "Closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "明细行已关闭");
            }
            decimal chk = Num(CoRows.Col(left, "Chk"));
            decimal sum = Num(CoRows.Col(left, "Summed"));
            if (chk <= 0m)
            {
                throw new BridgeException(409, "state_mismatch", "调拨申请行没有核准数量");
            }
            if (!item.Over && item.Qty > chk - sum + 0.000001m)
            {
                throw new BridgeException(409, "state_mismatch", "超过调拨申请行的可调拨数量");
            }
            return sum;
        }

        static ApiResult InsertTr(WorkContext ctx, VoucherKind kind, int reqId,
            Dictionary<string, object> head, Dictionary<string, object> req, List<TrLine> want)
        {
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
                string billDate = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
                domH = StockDom.TrGenHead(ctx.Conn, kind, req, head, maker, billDate);
                domB = StockDom.TrGenBody(ctx.Conn, CellsOf(want));
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadMap(domH));
                StockDom.SetHeadValue(domH, "cTVCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockAt at = StockCall.AtFor("Insert", args, refs, null);
                TrGuard guard = new TrGuard(want, args);
                at.Before = guard.Before;
                at.After = guard.After;
                DryRun.Touched(Kinds.Find("transfer_request"), reqId);
                StockCall.RunAt(ctx, co, at);
                int newId = Confirmed(ctx, kind, code, args[6]);
                return AfterSaved(ctx, kind, newId, code, Kinds.Find("transfer_request"), reqId);
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

        static List<Dictionary<string, string>> CellsOf(List<TrLine> want)
        {
            List<Dictionary<string, string>> rows = new List<Dictionary<string, string>>();
            for (int i = 0; i < want.Count; i++)
            {
                Dictionary<string, string> row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (KeyValuePair<string, object> kv in want[i].Src)
                {
                    string text = Values.Text(kv.Value).Trim();
                    if (text.Length > 0)
                    {
                        row[kv.Key] = text;
                    }
                }
                row["itvquantity"] = StockUnits.Price(want[i].Qty);
                if (want[i].Memo.Length > 0)
                {
                    row["cbmemo"] = want[i].Memo;
                }
                rows.Add(row);
            }
            return rows;
        }

        // 登录前（RequestsP4.CheckGenerate）：表头表体字段、行数、每行的 source_line_id 和数量（大于 0、最多 6 位小数）。
        internal static void CheckTrGenerate(Dictionary<string, object> head, object[] lines)
        {
            if (head != null)
            {
                CheckTrKeys(head, true);
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line == null)
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i), "表体行不是对象");
                }
                CheckTrKeys(line, false);
                QtyOf(Raw(line, "quantity"));
                StockDom.MiscQtyOk(Values.Text(Raw(line, "quantity")), false);
            }
        }

        // 表头只收日期、备注、部门、业务员、收发类别和表头自定义项；仓库取自申请单，不能改。表体只收 source_line_id、quantity、cbmemo。
        static void CheckTrKeys(Dictionary<string, object> map, bool head)
        {
            foreach (KeyValuePair<string, object> kv in map)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (head ? TrHeadKey(low) : TrLineKey(low))
                {
                    continue;
                }
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), kv.Key), "不能设置字段 " + kv.Key);
            }
        }

        internal static bool TrHeadKey(string low)
        {
            switch (low)
            {
                case "dtvdate":
                case "cmemo":
                case "codepcode":
                case "cidepcode":
                case "cpersoncode":
                case "cordcode":
                case "cirdcode":
                    return true;
            }
            return Span(low, "cdefine", 1, 16);
        }

        internal static bool TrLineKey(string low)
        {
            return low == "source_line_id" || low == "quantity" || low == "cbmemo";
        }

        sealed class TrLine
        {
            public int LineId;
            public decimal Qty;
            public bool Over;
            public string Memo;
            public Dictionary<string, object> Src;
        }
    }
}
