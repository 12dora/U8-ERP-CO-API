using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 退货单 ← 蓝字发货单（参照发货单退货）。实测：VT 10 的 GetNegaVouchData("05", 头, 体, 蓝字 DLID, err, true)
    // 由 U8 自己做出整张红字 DOM（负数量、负金额，表体不挂订单行）；桥只留请求的行、改成新增、按数量比例缩放后
    // BodyCheck 重算金额（SaleReturnLine.cs），卡片 03 重取模板，取号保存。API 的 quantity 仍是正数。
    internal static partial class SaleGen
    {
        // 原发货行的可退数量要素（SaleReturnRoom）与订单关联。
        const string RetLineSql = "select d.iDLsID, d.iSOsID, d.cSCloser, " + RetRoomCols + ", m.cSOCode as SoCode, "
            + "convert(varchar(20), s.iRowNo) as SoRow from DispatchLists d "
            + "left join SO_SODetails s on s.iSOsID=d.iSOsID left join SO_SOMain m on m.ID=s.ID where d.DLID=?";
        // invoiced 不写进 DOM，只决定已开票 / 未开票退货（表头 bneedbill）。
        const string RetHeadKeys = "ddate,cmemo,cdepcode,cpersoncode,invoiced";
        const string RetLineKeys = "cwhcode,cmemo";
        // 登录前：表头只收 dDate、cMemo、cDepCode、cPersonCode、cdefine1–16 和布尔 invoiced；表体另收 cWhCode、cMemo、cdefine22–37。
        internal static void CheckReturn(Dictionary<string, object> head, object[] lines)
        {
            CheckKeys(head ?? new Dictionary<string, object>(), true, RetHeadKeys, false);
            CheckInvoiced(head);
            string date = TextOf(head, "ddate");
            DateTime day;
            if (date.Length > 0 && !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw new BridgeException(400, "bad_request", "单据日期必须是 yyyy-MM-dd");
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> map = lines[i] as Dictionary<string, object>;
                if (map == null)
                {
                    throw new BridgeException(400, "bad_request", "表体行必须是对象");
                }
                CheckKeys(map, false, RetLineKeys, false);
            }
        }

        internal static bool MetaReturn(string key, bool head)
        {
            return Allowed(key, head, head ? RetHeadKeys : RetLineKeys, false);
        }

        public static ApiResult Return(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head, object[] lines)
        {
            if (!SaleReturn.Is(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            CheckReturn(head, lines);
            RetJob job = new RetJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.SourceId = sourceId;
            job.Head = WithoutInvoiced(head);
            job.DlCode = PlanReturnHead(ctx.Conn, sourceId);
            job.Lines = PlanReturn(ctx.Conn, sourceId, lines);
            job.Invoiced = DecideInvoiced(job.Lines, RawKey(head, "invoiced"));
            return BuildReturn(job);
        }

        // 来源：蓝字（bReturnFlag=0、cVouchType 05）、非期初、已审核、未关闭。
        static string PlanReturnHead(object conn, int sourceId)
        {
            Dictionary<string, object> src = Rows.One(conn, DispHeadSql, new object[] { sourceId });
            if (src == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.FlagOf(src, "bReturnFlag") || CoRows.Col(src, "cVouchType") != "05")
            {
                throw new BridgeException(400, "bad_request", "仅支持参照蓝字发货单");
            }
            if (CoRows.FlagOf(src, "bFirst"))
            {
                throw new BridgeException(400, "bad_request", "不支持期初发货单");
            }
            if (CoRows.Col(src, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(src, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            return CoRows.Col(src, "cDLCode");
        }

        static List<RetLine> PlanReturn(object conn, int sourceId, object[] lines)
        {
            Dictionary<int, Dictionary<string, object>> body = IndexBy(conn, RetLineSql, sourceId, "iDLsID");
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            List<RetLine> plan = new List<RetLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> map = (Dictionary<string, object>)lines[i];
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
                RetLine line = new RetLine();
                line.LineId = lineId;
                line.Qty = QtyOf(RawKey(map, "quantity"));
                line.SoLine = CoRows.AsId(CoRows.Col(src, "iSOsID"));
                line.SoCode = CoRows.Col(src, "SoCode");
                line.SoRow = CoRows.Col(src, "SoRow");
                line.RoomU = Room(src, false);
                line.RoomI = Room(src, true);
                line.Fields = map;
                plan.Add(line);
            }
            return plan;
        }

        static ApiResult BuildReturn(RetJob job)
        {
            WorkContext ctx = job.Ctx;
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, SaleReturn.RedVt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                NegaDoms(ctx, co, doms, job.SourceId);
                FillReturnHead(job, sys, doms);
                FillReturnLines(job, co, doms);
                string code;
                int id = SaveReturn(job, co, doms, out code);
                return AfterSave(ctx, job.Kind, id, code, RetEcho(job));
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        // 表头用 U8 自己做的红字表头，清掉原单的主键、单号、时间戳、审核关闭修改痕迹后改成新增。
        static void FillReturnHead(RetJob job, object sys, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            Dictionary<string, object> head = job.Head;
            DropExtra(doms[0]);
            object row = OneRow(doms[0]);
            List<string> schema = DomRows.Schema(doms[0]);
            ClearAttrs(doms[0], row, RetHeadClear, schema);
            // 表头主键属性必须在，值为空串。
            DomRows.Set(doms[0], row, "dlid", "", schema);
            DomRows.Set(doms[0], row, "cdlcode", "", schema);
            ApplyFields(doms[0], row, head, true, true);
            DomRows.Set(doms[0], row, "ddate", TextOf(head, "ddate").Length > 0 ? TextOf(head, "ddate") : LoginDate(ctx), schema);
            DomRows.Set(doms[0], row, "cvouchtype", "05", schema);
            DomRows.Set(doms[0], row, "breturnflag", "1", schema);
            DomRows.Set(doms[0], row, "editprop", "A", schema);
            DomRows.Set(doms[0], row, "cMaker", User(ctx), schema);
            DomRows.Set(doms[0], row, "bneedbill", job.Invoiced ? "1" : "0", schema);
            if (job.Lines[0].SoCode.Length > 0 && RetLinkSo)
            {
                DomRows.Set(doms[0], row, "csocode", job.Lines[0].SoCode, schema);
            }
            // GetNegaVouchData 给的是蓝字模板 iVTid，按卡片 03 重取。
            DomRows.Set(doms[0], row, "ivtid", null, schema);
            SoSave.Stamp(ctx, sys, doms[0], SaleReturn.RedCard);
            if (SoDom.Attr(doms[0], "iVTid").Length == 0)
            {
                CoRows.Note(ctx.Item, "getDefaltVTID 没给卡片 03 模板，用缺省 " + RedVtidFallback);
                SoDom.SetAttr(doms[0], "iVTid", RedVtidFallback);
            }
        }

        sealed class RetJob
        {
            internal WorkContext Ctx;
            internal VoucherKind Kind;
            internal int SourceId;
            internal Dictionary<string, object> Head;
            internal string DlCode;
            // 新退货单号（取号后填）。
            internal string Code;
            internal List<RetLine> Lines;
            // 已开票退货（表头 bneedbill=1，回写原行 fretqtyykp）；否则未开票退货（bneedbill=0，回写 fretqtywkp）。
            internal bool Invoiced;
            // K12 参照退货申请单生成时，响应的来源是申请单（RetEcho）；否则为空，来源是 SourceId 的发货单。
            internal string EchoKind;
            internal int EchoId;
        }

        sealed class RetLine
        {
            internal int LineId;
            internal decimal Qty;
            internal int SoLine;
            internal Dictionary<string, object> Fields;
            // 事务里保存前读到的原发货行累计退货数量。
            internal decimal RetBefore;
            internal string SoCode;
            internal string SoRow;
            // 计划时的未开票 / 已开票可退数量（已扣 iRetQuantity）。
            internal decimal RoomU;
            internal decimal RoomI;
            // 事务里保存前读到的原行 fretqtywkp 或 fretqtyykp（按 Invoiced）。
            internal decimal AccBefore;
            // K12 参照退货申请单：申请单行 AutoID、单号，事务里保存前读到的申请行 fretqty（SaleGenApplyRet.cs）。
            internal int AppLine;
            internal string AppCode;
            internal decimal AppBefore;
        }
    }
}
