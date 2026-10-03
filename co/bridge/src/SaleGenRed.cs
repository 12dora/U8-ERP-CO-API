using System;
using System.Collections.Generic;

namespace U8Co
{
    // 红字销售发票 ← 退货单：vouchers/generate type=sale_invoice、source_type=sale_return，id 是退货单 DLID。
    // 退货单必须 cVouchType 05、bReturnFlag=1、非期初、已审核、未关闭，且 bneedbill=1（已开票退货）：红字发票
    // 来自 bneedbill=1 的退货单；bneedbill=0 的未开票退货冲的是原行未开票数量（fretqtywkp），不开红字发票。
    // lines 可省（全部有剩余的退货行按剩余数量生成）；quantity 正数，DOM 写负数。剩余 = abs(iQuantity) − abs(iSettleQuantity)。
    // 发票类型：表头 cvouchtype 26 / 27；省略时取原蓝字发货行（退货行 iCorID）上蓝字发票的类型（红票冲同类蓝票），
    // 找不到时 26。销售 CO 用 VT 1（红字专票）/ 3（红字普票），卡片同蓝字 07 / 13。
    // DOM 先试 GetNegaVouchData，不合用再按参照视图拼（SaleGenRedDom.cs）；保存与回写核对在 SaleGenRedSave.cs。
    internal static partial class SaleGen
    {
        const string RInvHeadSql = "select cDLCode, cSOCode, cVerifier, cVouchType, bReturnFlag, cCloser, bFirst, bneedbill "
            + "from DispatchList where DLID=?";
        const string RInvLineSql = "select iDLsID, iSOsID, cSCloser, convert(varchar(40), abs(isnull(iQuantity,0))) as q, "
            + "convert(varchar(40), abs(isnull(iSettleQuantity,0))) as s from DispatchLists where DLID=? order by iRowNo, iDLsID";
        // 原蓝字发货行（退货行 iCorID）上最近一张蓝字发票的类型。
        const string RInvBlueSql = "select top 1 h.cVouchType from SaleBillVouchs b inner join SaleBillVouch h on h.SBVID=b.SBVID "
            + "where isnull(h.bReturnFlag,0)=0 and b.iDLsID in (select x.iCorID from DispatchLists x "
            + "where x.DLID=? and isnull(x.iCorID,0)<>0) order by h.SBVID desc";

        // Dispatch.GenerateSa 用：来源类型是退货单时走红字发票。
        internal static bool FromReturn(WorkItem item)
        {
            return item != null && item.Source != null && item.Source.Name == SaleReturn.KindName;
        }

        public static ApiResult RedInvoice(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head,
            object[] lines)
        {
            if (kind == null || kind.Name != "sale_invoice")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            if (lines == null)
            {
                lines = new object[0];
            }
            if (lines.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            CheckKeys(head, true, InvHeadKeys, false);
            RedJob job = new RedJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.SourceId = sourceId;
            job.Head = head;
            job.Want = RedWant(ctx.Conn, head, sourceId);
            PlanRedHead(ctx.Conn, sourceId, job.Want);
            job.Lines = PlanRedLines(ctx.Conn, sourceId, lines);
            return BuildRed(job);
        }

        // 表头 cvouchtype 26 / 27 → VT 1 / 3；省略时按原蓝字发票类型，缺省 26。
        static InvHead RedWant(object conn, Dictionary<string, object> head, int sourceId)
        {
            string text = TextOf(head, "cvouchtype");
            if (text.Length == 0)
            {
                string blue = Rows.Scalar(conn, RInvBlueSql, new object[] { sourceId });
                text = blue == null || blue.Trim().Length == 0 ? "26" : blue.Trim();
            }
            InvHead want = new InvHead();
            want.DlCode = "";
            want.SoCode = "";
            if (SameQty(text, 26m))
            {
                want.Vt = 1;
                want.Card = "07";
                want.Vouch = "26";
                return want;
            }
            if (SameQty(text, 27m))
            {
                want.Vt = 3;
                want.Card = "13";
                want.Vouch = "27";
                return want;
            }
            throw new BridgeException(400, "bad_request", "该发票类型不支持");
        }

        static void PlanRedHead(object conn, int sourceId, InvHead want)
        {
            Dictionary<string, object> src = Rows.One(conn, RInvHeadSql, new object[] { sourceId });
            if (src == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (!CoRows.FlagOf(src, "bReturnFlag") || CoRows.Col(src, "cVouchType") != "05")
            {
                throw new BridgeException(400, "bad_request", "该单据不是退货单（红字发货单）");
            }
            if (CoRows.FlagOf(src, "bFirst"))
            {
                throw new BridgeException(400, "bad_request", "不支持期初退货单");
            }
            if (CoRows.Col(src, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(src, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (!CoRows.FlagOf(src, "bneedbill"))
            {
                throw new BridgeException(409, "state_mismatch", "退货单是未开票退货（bneedbill=0），不开红字发票");
            }
            want.DlCode = CoRows.Col(src, "cDLCode");
            want.SoCode = CoRows.Col(src, "cSOCode");
        }

        static List<RedLine> PlanRedLines(object conn, int sourceId, object[] lines)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, RInvLineSql, new object[] { sourceId }, 5000);
            Dictionary<int, Dictionary<string, object>> body = new Dictionary<int, Dictionary<string, object>>();
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(rows[i], "iDLsID"));
                if (lineId > 0 && !body.ContainsKey(lineId))
                {
                    body[lineId] = rows[i];
                }
            }
            if (lines.Length == 0)
            {
                return RedWhole(rows);
            }
            List<RedLine> plan = new List<RedLine>();
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            for (int i = 0; i < lines.Length; i++)
            {
                plan.Add(ReadRedLine(lines[i], body, seen));
            }
            return plan;
        }

        // 不带 lines：全部未关闭、有剩余的退货行，按剩余数量。
        static List<RedLine> RedWhole(List<Dictionary<string, object>> rows)
        {
            List<RedLine> plan = new List<RedLine>();
            for (int i = 0; i < rows.Count; i++)
            {
                decimal left = RedLeft(rows[i]);
                if (left <= 0m || CoRows.Col(rows[i], "cSCloser").Length > 0)
                {
                    continue;
                }
                plan.Add(NewRedLine(rows[i], left, new Dictionary<string, object>()));
            }
            if (plan.Count == 0)
            {
                throw new BridgeException(409, "state_mismatch", "退货单没有可开票数量");
            }
            if (plan.Count > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            return plan;
        }

        static RedLine ReadRedLine(object raw, Dictionary<int, Dictionary<string, object>> body, Dictionary<int, bool> seen)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", "表体行必须是对象");
            }
            CheckKeys(map, false, InvLineKeys, false);
            int lineId = CoRows.AsId(RawKey(map, "source_line_id"));
            Dictionary<string, object> src;
            if (lineId <= 0 || !body.TryGetValue(lineId, out src))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (seen.ContainsKey(lineId))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            seen[lineId] = true;
            if (CoRows.Col(src, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            decimal qty = QtyOf(RawKey(map, "quantity"));
            if (qty > RedLeft(src))
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            return NewRedLine(src, qty, map);
        }

        static RedLine NewRedLine(Dictionary<string, object> src, decimal qty, Dictionary<string, object> fields)
        {
            RedLine line = new RedLine();
            line.LineId = CoRows.AsId(CoRows.Col(src, "iDLsID"));
            line.SoLine = CoRows.AsId(CoRows.Col(src, "iSOsID"));
            line.Qty = qty;
            line.Fields = fields;
            return line;
        }

        static decimal RedLeft(Dictionary<string, object> row)
        {
            decimal left = PuInv.Num(CoRows.Col(row, "q")) - PuInv.Num(CoRows.Col(row, "s"));
            return left < 0m ? 0m : left;
        }

        // 表头同蓝字发票参照发货单（cvouchtype、ddate、cmemo、cdefine1–16）；表体只收 cmemo、cdefine22–37，lines 可为 0 行。
        internal static Dictionary<string, object> MetaRedInvoice()
        {
            Dictionary<string, object> spec = MetaWritable.Spec(
                delegate(string low) { return MetaInvoice(low, true); },
                delegate(string low) { return MetaInvoice(low, false); }, 0, 200, MetaWritable.Control());
            spec["required"] = MetaWritable.Required(new string[0], MetaWritable.Control());
            return spec;
        }

        sealed class RedJob
        {
            internal WorkContext Ctx;
            internal VoucherKind Kind;
            internal int SourceId;
            internal Dictionary<string, object> Head;
            internal InvHead Want;
            internal List<RedLine> Lines;
            // 新发票号（取号后填）。
            internal string Code;
        }

        sealed class RedLine
        {
            internal int LineId;
            internal int SoLine;
            internal decimal Qty;
            internal Dictionary<string, object> Fields;
        }
    }
}
