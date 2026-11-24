using System.Collections.Generic;

namespace U8Co
{
    // 退货单 ← 销售订单（只挂订单行，没有原发货行）。GetNegaVouchData 只收已有的 05 单据，不能从订单做；
    // 走发货单参照订单的构造（SaleGen.Build，red 非空）：VT 10 卡片 03 的 GetDefaultVoucherDom 模板，表头抄
    // sale_RefSOVouch_T，表体抄 sale_RefSOVouch_B、按数量比例缩放后取负，挂 isosid / csocode / cordercode / iorderrowno，
    // 不挂 icorid / ccorcode。退的是已发货的货（不指明发货单）：U8 保存时订单行 iFHQuantity −qty、fretquantity +qty，
    // 不校验上限，所以可退数量 = 订单行 iFHQuantity（累计发货数量，已扣过以前的退货），由桥在 COM 前拦。
    // 保存（SaveReturn）在事务里带 UPDLOCK 重查可退数量，提交前核对订单行 fretquantity +qty、iFHQuantity −qty。
    // 表头 bneedbill 按请求 invoiced，省略为未开票退货。
    internal static partial class SaleGen
    {
        const string SoRoomCols = "convert(varchar(40), isnull(s.iFHQuantity,0)) as fh, "
            + "convert(varchar(40), isnull(s.fretquantity,0)) as ret";
        const string SoRetLineSql = "select s.iSOsID, s.cSCloser, " + SoRoomCols + ", m.cSOCode as SoCode, "
            + "convert(varchar(20), s.iRowNo) as SoRow from SO_SODetails s inner join SO_SOMain m on m.ID=s.ID where s.ID=?";
        const string SoRoomLockSql = "select s.cSCloser, " + SoRoomCols
            + " from SO_SODetails s with (updlock, holdlock) where s.iSOsID=?";
        // 退货单参照订单的行不挂原发货行。
        static readonly string[] RetCorLink = new string[] { "icorid", "ccorcode" };

        // SaleGen.Return 用：来源类型是销售订单。
        internal static bool FromOrder(WorkItem item)
        {
            return item != null && item.Source != null && item.Source.Name == "sale_order";
        }

        static ApiResult ReturnFromSo(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head,
            object[] lines)
        {
            RetJob job = new RetJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.SourceId = sourceId;
            job.Head = WithoutInvoiced(head);
            job.DlCode = "";
            string soCode = OpenOrder(ctx.Conn, sourceId);
            job.Lines = PlanReturn(ctx.Conn, sourceId, lines, true);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                if (job.Lines[i].Qty > job.Lines[i].RoomU)
                {
                    throw new BridgeException(409, "state_mismatch", "超过可退货数量（订单行累计发货数量）");
                }
            }
            job.Invoiced = DecideInvoiced(job.Lines, RawKey(head, "invoiced"));
            HeadFill fill = HeadFillOf(null, null, job.Head, soCode, null);
            fill.Red = job;
            return Build(ctx, kind, sourceId, ShipOf(job.Lines), fill);
        }

        // SaleGen.FillLine 用：发货单 sign 1；退货单 sign -1，数量与金额取负并去掉原发货行关联。
        static void ScaleSigned(object body, object row, decimal sign, decimal qty)
        {
            Scale(body, row, sign * qty);
            if (sign < 0m)
            {
                ClearAttrs(body, row, RetCorLink, DomRows.Schema(body));
            }
        }

        static Dictionary<int, Dictionary<string, object>> RetBody(object conn, int sourceId, bool fromSo)
        {
            if (fromSo)
            {
                return IndexBy(conn, SoRetLineSql, sourceId, "iSOsID");
            }
            return IndexBy(conn, RetLineSql, sourceId, "iDLsID");
        }

        // 订单行可退（SoRoom）：参照订单退货没有已开票 / 未开票之分，两者相同。
        static void SetRooms(RetLine line, Dictionary<string, object> src)
        {
            line.RoomU = line.SoOnly ? SoRoom(src) : Room(src, false);
            line.RoomI = line.SoOnly ? line.RoomU : Room(src, true);
        }

        // 订单行可退 = iFHQuantity（累计发货数量），不小于 0。
        internal static decimal SoRoom(Dictionary<string, object> row)
        {
            decimal room = RoomNum(row, "fh");
            return room < 0m ? 0m : room;
        }

        static ShipLine[] ShipOf(List<RetLine> lines)
        {
            ShipLine[] plan = new ShipLine[lines.Count];
            for (int i = 0; i < lines.Count; i++)
            {
                ShipLine line = new ShipLine();
                line.LineId = lines[i].SoLine;
                line.Qty = lines[i].Qty;
                line.OrderRow = lines[i].SoRow;
                line.Fields = lines[i].Fields;
                plan[i] = line;
            }
            return plan;
        }

        // SaveReturn 的事务里：订单行仍在、未关闭，可退数量放得下。返回带 UPDLOCK 读出的订单行（作回写核对的基准）。
        static Dictionary<string, object> LockSoRoom(object conn, RetLine line)
        {
            Dictionary<string, object> row = Rows.One(conn, SoRoomLockSql, new object[] { line.SoLine });
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", "销售订单行不存在");
            }
            if (CoRows.Col(row, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (line.Qty > SoRoom(row))
            {
                throw new BridgeException(409, "state_mismatch", "超过可退货数量（订单行累计发货数量）");
            }
            return row;
        }
    }
}
