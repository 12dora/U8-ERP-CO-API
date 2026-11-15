using System;
using System.Collections.Generic;

namespace U8Co
{
    // 退货单的可退数量（XE 跟踪实测的 U8 规则）。保存后 U8 对原发货行检查
    //   CASE WHEN ABS(iSettleQuantity) - (ABS(iQuantity) - ABS(fretqtywkp)) > 0 THEN ABS(iQuantity) - ABS(fretqtywkp)
    //   ELSE ABS(iSettleQuantity) END - ABS(fretqtyykp) < 0 → 「退货数量不能大于应发货数量」。
    // 表头 bneedbill=0（未开票退货）时 U8 把退货数加到原行 fretqtywkp，bneedbill=1（已开票退货）加到 fretqtyykp。
    // 未开票可退 U = iQuantity - iSettleQuantity - fretqtywkp；已开票可退 I = 上式 CASE - fretqtyykp；
    // 两者再不超过 iQuantity - iRetQuantity。
    internal static partial class SaleGen
    {
        const string RetRoomCols = "convert(varchar(40), isnull(d.iQuantity,0)) as q, "
            + "convert(varchar(40), isnull(d.iSettleQuantity,0)) as s, convert(varchar(40), isnull(d.fretqtywkp,0)) as w, "
            + "convert(varchar(40), isnull(d.fretqtyykp,0)) as y, convert(varchar(40), isnull(d.iRetQuantity,0)) as r";

        static decimal Room(Dictionary<string, object> row, bool invoiced)
        {
            decimal q = Math.Abs(RoomNum(row, "q"));
            decimal s = Math.Abs(RoomNum(row, "s"));
            decimal w = Math.Abs(RoomNum(row, "w"));
            decimal y = Math.Abs(RoomNum(row, "y"));
            decimal left = q - Math.Abs(RoomNum(row, "r"));
            decimal room = invoiced ? (s - (q - w) > 0m ? q - w : s) - y : q - s - w;
            room = Math.Min(room, left);
            return room < 0m ? 0m : room;
        }

        static decimal RoomNum(Dictionary<string, object> row, string col)
        {
            return PuInv.Num(CoRows.Col(row, col));
        }

        // 请求 invoiced：false 只按未开票、true 只按已开票；省略时全部行都放得下未开票就按未开票，否则全部放得下
        // 已开票就按已开票，都不行 409（一张退货单只能是一种，混合的要拆开）。
        static bool DecideInvoiced(List<RetLine> lines, object requested)
        {
            if (requested is bool)
            {
                bool want = (bool)requested;
                if (!Fits(lines, want))
                {
                    throw new BridgeException(409, "state_mismatch", want ? "超过已开票可退数量" : "超过未开票可退数量");
                }
                return want;
            }
            if (Fits(lines, false))
            {
                return false;
            }
            if (Fits(lines, true))
            {
                return true;
            }
            throw new BridgeException(409, "state_mismatch",
                "超过可退货数量：未开票可退与已开票可退都放不下全部行，请按 invoiced 拆成两张退货单");
        }

        static bool Fits(List<RetLine> lines, bool invoiced)
        {
            for (int i = 0; i < lines.Count; i++)
            {
                decimal room = invoiced ? lines[i].RoomI : lines[i].RoomU;
                if (lines[i].Qty > room)
                {
                    return false;
                }
            }
            return true;
        }

        static void CheckInvoiced(Dictionary<string, object> head)
        {
            if (head != null && HasInvoiced(head) && !(RawKey(head, "invoiced") is bool))
            {
                throw new BridgeException(400, "bad_request", "invoiced 必须是布尔");
            }
        }

        static bool HasInvoiced(Dictionary<string, object> head)
        {
            foreach (string key in head.Keys)
            {
                if (Same(key, "invoiced"))
                {
                    return true;
                }
            }
            return false;
        }

        static Dictionary<string, object> WithoutInvoiced(Dictionary<string, object> head)
        {
            Dictionary<string, object> copy = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> pair in head)
            {
                if (!Same(pair.Key, "invoiced"))
                {
                    copy[pair.Key] = pair.Value;
                }
            }
            return copy;
        }
    }
}
