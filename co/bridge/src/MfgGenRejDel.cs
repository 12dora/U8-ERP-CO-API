using System.Collections.Generic;

namespace U8Co
{
    // 删除来源为产品不良品处理单的产成品入库：U8 回退 BFLAG、QualifiedInQty 未经实测，所以在同一事务里核对，没退就回滚。
    internal static partial class MfgGen
    {
        const string RejSourceTitle = "产品不良品处理单";
        const string RejRdSql = "select convert(varchar(20), b.iRejectIds) as RejId, convert(varchar(20), b.iMPoIds) as MoDId,"
            + " convert(varchar(40), isnull(b.iQuantity,0)) as Qty from rdrecords10 b"
            + " where b.ID=? and isnull(b.iRejectIds,0)<>0";

        internal static void GuardRejUndo(StockAt at, int rdId)
        {
            RejUndo undo = new RejUndo();
            undo.RdId = rdId;
            at.Before = undo.Before;
            at.After = undo.After;
        }

        sealed class RejUndo
        {
            public int RdId;
            readonly List<int> _rejects = new List<int>();
            readonly Dictionary<int, decimal> _expect = new Dictionary<int, decimal>();

            // 一张入库单可能有多行挂同一生产订单行，按订单行汇总本单数量。
            public void Before(object conn)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, RejRdSql, new object[] { RdId }, 500);
                Dictionary<int, decimal> qty = new Dictionary<int, decimal>();
                for (int i = 0; rows != null && i < rows.Count; i++)
                {
                    int mo = CoRows.AsId(CoRows.Col(rows[i], "MoDId"));
                    decimal sum;
                    qty.TryGetValue(mo, out sum);
                    qty[mo] = sum + Num(CoRows.Col(rows[i], "Qty"));
                    _rejects.Add(CoRows.AsId(CoRows.Col(rows[i], "RejId")));
                }
                foreach (KeyValuePair<int, decimal> kv in qty)
                {
                    _expect[kv.Key] = Num(Rows.Scalar(conn, MoQtySql, new object[] { kv.Key })) - kv.Value;
                }
            }

            // 未覆盖：与生单核对共用 RejCheckMoQty / RejCheckFlag 两个开关。已入库标记只在该行已无任何入库时要求回到 0。
            public void After(object conn)
            {
                foreach (KeyValuePair<int, decimal> kv in _expect)
                {
                    if (RejCheckMoQty && !Same(Num(Rows.Scalar(conn, MoQtySql, new object[] { kv.Key })), kv.Value))
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 没有回退生产订单合格入库数量");
                    }
                }
                for (int i = 0; RejCheckFlag && i < _rejects.Count; i++)
                {
                    object[] arg = new object[] { _rejects[i] };
                    if (Num(Rows.Scalar(conn, RejUsedSql, arg)) == 0m && Num(Rows.Scalar(conn, RejFlagSql, arg)) != 0m)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 没有回退不良品处理单的已入库标记");
                    }
                }
            }
        }
    }
}
