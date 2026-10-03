using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红冲蓝字销售发票（K11）：vouchers/generate type=sale_invoice、source_type=sale_invoice，id 是蓝字发票 SBVID。
    // 蓝字发票必须 cVouchType 26 / 27、非红字、非期初、未作废、已复核、不是现结（bcashsale=1 或有现结单 SalePayVouch）；
    // 红字发票日期不早于蓝字发票日期、所在期间销售管理未结账（GL_mend.bflag_SA），客户未停用（Customer.dEndDate）。
    // 红字发票类型同蓝字（表头 cvouchtype 可省，给了必须相同），销售 CO 用 VT 1（红字专票）/ 3（红字普票）。
    // lines 可省（全部有剩余的蓝字行按剩余数量红冲）；quantity 正数，DOM 写负数。
    // GetNegaVouchData("26", 头, 体, 蓝字 SBVID, "", true) 返回 true，表头 sbvid=0、breturnflag=1、客户同蓝字；
    // 表体是蓝字行的负数，autoid 是蓝字行主键，idlsid 为空；传发货单 DLID 返回 false。
    // U8 数据里红字发票行与蓝字行没有可用的关联列（SaleBillVouchs.iSBVID 通常为 0）。桥在红字行
    // iSBVID 写蓝字行 AutoID（BluePinLink）作关联：剩余 = 蓝字行 iQuantity − 指向它的红字行 |iQuantity| 合计。Save 是否保留未经实测。
    // DOM 与保存在 SaleGenBlueRedSave.cs。
    internal static partial class SaleGen
    {
        internal const string BlueSourceName = "sale_invoice";
        const string BlueHeadSql = "select h.cSBVCode, h.cVouchType, h.bReturnFlag, h.cChecker, h.bFirst, h.bcashsale, "
            + "h.cInvalider, h.cCusCode, convert(varchar(10), h.dDate, 23) as d, "
            + "(select count(*) from SalePayVouch p where p.SBVID=h.SBVID) as paid from SaleBillVouch h where h.SBVID=?";
        // 蓝字行及已红冲数量（红字行 iSBVID 指向蓝字行 AutoID）。相关条件在子查询 WHERE 里，SUM 只读子查询自己的表。
        const string BlueLineCols = "select b.AutoID, b.iDLsID, b.iSOsID, convert(varchar(40), isnull(b.iQuantity,0)) as q, "
            + "convert(varchar(40), isnull((select sum(-r.iQuantity) from SaleBillVouchs r inner join SaleBillVouch rh "
            + "on rh.SBVID=r.SBVID where isnull(rh.bReturnFlag,0)=1 and r.iSBVID=b.AutoID),0)) as back from SaleBillVouchs b";
        internal const string BlueLineSql = BlueLineCols + " where b.SBVID=? order by b.irowno, b.AutoID";
        internal const string BlueLineLockSql = BlueLineCols + " with (updlock, holdlock) where b.SBVID=? order by b.irowno, b.AutoID";
        const string BlueCusSql = "select convert(varchar(10), dEndDate, 23) as ended from Customer where cCusCode=?";
        const string BlueSaClosedSql = "select top 1 convert(varchar(10), iperiod) from GL_mend where iyear=? and iperiod=? "
            + "and isnull(bflag_SA,0)<>0";

        // Dispatch.GenerateSa 用：来源类型是销售发票时走红冲。
        internal static bool FromBlue(WorkItem item)
        {
            return item != null && item.Source != null && item.Source.Name == BlueSourceName;
        }

        // Dispatch.GenerateSa 用：红字销售发票按来源分到退货单（SaleGenRed）或蓝字发票（本文件）。
        internal static ApiResult RedOf(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head,
            object[] lines)
        {
            if (FromBlue(ctx.Item))
            {
                return BlueRedInvoice(ctx, kind, sourceId, head, lines);
            }
            return RedInvoice(ctx, kind, sourceId, head, lines);
        }

        public static ApiResult BlueRedInvoice(WorkContext ctx, VoucherKind kind, int blueId, Dictionary<string, object> head,
            object[] lines)
        {
            if (kind == null || kind.Name != "sale_invoice")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            head = head ?? new Dictionary<string, object>();
            lines = lines ?? new object[0];
            if (lines.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            CheckKeys(head, true, InvHeadKeys, false);
            BlueJob job = new BlueJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.BlueId = blueId;
            job.Head = head;
            job.Date = BlueDate(ctx, head);
            Dictionary<string, object> blue = BlueHeadGate(ctx.Conn, blueId);
            job.Want = BlueWant(TextOf(head, "cvouchtype"), CoRows.Col(blue, "cVouchType"));
            BlueBookGate(ctx, blue, job.Date);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, BlueLineSql, new object[] { blueId }, 5000);
            job.Lines = PlanBlueLines(rows, lines);
            return BuildBlueRed(job);
        }

        static string BlueDate(WorkContext ctx, Dictionary<string, object> head)
        {
            string text = TextOf(head, "ddate");
            if (text.Length == 0)
            {
                return LoginDate(ctx);
            }
            DateTime day;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw BridgeException.BadField("head.ddate", "日期必须是 yyyy-MM-dd");
            }
            return text;
        }

        static Dictionary<string, object> BlueHeadGate(object conn, int blueId)
        {
            Dictionary<string, object> src = Rows.One(conn, BlueHeadSql, new object[] { blueId });
            if (src == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.FlagOf(src, "bReturnFlag"))
            {
                throw new BridgeException(400, "bad_request", "该发票是红字发票，只能红冲蓝字发票");
            }
            if (CoRows.FlagOf(src, "bFirst"))
            {
                throw new BridgeException(400, "bad_request", "不支持期初发票");
            }
            if (CoRows.Col(src, "cInvalider").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "发票已作废");
            }
            if (CoRows.Col(src, "cChecker").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "蓝字发票未复核");
            }
            if (CoRows.FlagOf(src, "bcashsale") || PuInv.Num(CoRows.Col(src, "paid")) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "现结发票不能红冲，请在 U8 客户端处理");
            }
            return src;
        }

        // 表头 cvouchtype 可省；给了必须与蓝字发票相同。26 → VT 1 / 卡片 07，27 → VT 3 / 卡片 13。
        // 自检用：只返回 VT。
        internal static int BlueVt(string asked, string blueType)
        {
            return BlueWant(asked, blueType).Vt;
        }

        static InvHead BlueWant(string asked, string blueType)
        {
            string blue = (blueType ?? "").Trim();
            string text = (asked ?? "").Trim();
            if (text.Length > 0 && !(SameQty(text, 26m) && SameQty(blue, 26m)) && !(SameQty(text, 27m) && SameQty(blue, 27m)))
            {
                throw BridgeException.BadField("head.cvouchtype", "红字发票类型必须与蓝字发票相同");
            }
            InvHead want = new InvHead();
            want.DlCode = "";
            want.SoCode = "";
            if (SameQty(blue, 26m))
            {
                want.Vt = 1;
                want.Card = "07";
                want.Vouch = "26";
                return want;
            }
            if (SameQty(blue, 27m))
            {
                want.Vt = 3;
                want.Card = "13";
                want.Vouch = "27";
                return want;
            }
            throw new BridgeException(400, "bad_request", "该发票类型不支持");
        }

        // 红字发票日期早于蓝字发票日期：400。客户停用日期不晚于红字发票日期、该日期所在期间销售管理已结账：409。
        static void BlueBookGate(WorkContext ctx, Dictionary<string, object> blue, string date)
        {
            string blueDate = CoRows.Col(blue, "d");
            if (blueDate.Length > 0 && string.CompareOrdinal(date, blueDate) < 0)
            {
                throw BridgeException.BadField("head.ddate", "红字发票日期不能早于蓝字发票日期 " + blueDate);
            }
            Dictionary<string, object> cus = Rows.One(ctx.Conn, BlueCusSql, new object[] { CoRows.Col(blue, "cCusCode") });
            if (cus == null)
            {
                throw new BridgeException(409, "state_mismatch", "客户不存在");
            }
            string ended = CoRows.Col(cus, "ended");
            if (ended.Length > 0 && string.CompareOrdinal(ended, date) <= 0)
            {
                throw new BridgeException(409, "state_mismatch", "客户已停用");
            }
            int[] period = WriteoffSql.PeriodOf(ctx.Conn, ctx.Item.Acc, date);
            if (period == null)
            {
                throw BridgeException.BadField("head.ddate", "日期不在会计期间内");
            }
            if (Rows.Scalar(ctx.Conn, BlueSaClosedSql, new object[] { period[0], period[1] }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "销售管理该月已结账");
            }
        }

        // 不带 lines：全部有剩余的蓝字行按剩余数量；带了按请求。都没有剩余时 409「蓝字发票已全部红冲」。不连库，自检可调。
        internal static List<BlueLine> PlanBlueLines(List<Dictionary<string, object>> rows, object[] lines)
        {
            Dictionary<int, Dictionary<string, object>> body = new Dictionary<int, Dictionary<string, object>>();
            decimal room = 0m;
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(rows[i], "AutoID"));
                if (lineId > 0 && !body.ContainsKey(lineId))
                {
                    body[lineId] = rows[i];
                    room = room + BlueLeft(rows[i]);
                }
            }
            if (room <= 0m)
            {
                throw new BridgeException(409, "state_mismatch", "蓝字发票已全部红冲");
            }
            List<BlueLine> plan = new List<BlueLine>();
            if (lines == null || lines.Length == 0)
            {
                return BlueWhole(rows);
            }
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            for (int i = 0; i < lines.Length; i++)
            {
                plan.Add(ReadBlueLine(lines[i], body, seen));
            }
            return plan;
        }

        // 按蓝字行顺序，同一 AutoID 只取一次。
        static List<BlueLine> BlueWhole(List<Dictionary<string, object>> rows)
        {
            List<BlueLine> plan = new List<BlueLine>();
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(rows[i], "AutoID"));
                decimal left = BlueLeft(rows[i]);
                if (lineId <= 0 || left <= 0m || seen.ContainsKey(lineId))
                {
                    continue;
                }
                seen[lineId] = true;
                plan.Add(NewBlueLine(rows[i], left, new Dictionary<string, object>()));
            }
            if (plan.Count > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            return plan;
        }

        static BlueLine ReadBlueLine(object raw, Dictionary<int, Dictionary<string, object>> body, Dictionary<int, bool> seen)
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
            decimal qty = QtyOf(RawKey(map, "quantity"));
            decimal left = BlueLeft(src);
            if (left <= 0m)
            {
                throw new BridgeException(409, "state_mismatch", "该行已全部红冲");
            }
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            return NewBlueLine(src, qty, map);
        }

        static BlueLine NewBlueLine(Dictionary<string, object> src, decimal qty, Dictionary<string, object> fields)
        {
            BlueLine line = new BlueLine();
            line.LineId = CoRows.AsId(CoRows.Col(src, "AutoID"));
            line.DlLine = CoRows.AsId(CoRows.Col(src, "iDLsID"));
            line.SoLine = CoRows.AsId(CoRows.Col(src, "iSOsID"));
            line.Qty = qty;
            line.Fields = fields;
            return line;
        }

        // 剩余 = 蓝字数量 − 已红冲（都按正数）；0 数量的调价行没有剩余。
        internal static decimal BlueLeft(Dictionary<string, object> row)
        {
            decimal left = PuInv.Num(CoRows.Col(row, "q")) - PuInv.Num(CoRows.Col(row, "back"));
            return left < 0m ? 0m : left;
        }

        sealed class BlueJob
        {
            internal WorkContext Ctx;
            internal VoucherKind Kind;
            internal int BlueId;
            internal Dictionary<string, object> Head;
            internal InvHead Want;
            internal string Date;
            internal List<BlueLine> Lines;
            // 新发票号（取号后填）。
            internal string Code;
        }

        internal sealed class BlueLine
        {
            // 蓝字发票行 AutoID。
            internal int LineId;
            internal int DlLine;
            internal int SoLine;
            internal decimal Qty;
            internal Dictionary<string, object> Fields;
        }
    }
}
