using System.Collections.Generic;

namespace U8Co
{
    // 删除来源为产品不良品处理单的产成品入库：U8 回退 BFLAG、QualifiedInQty 未经实测，所以在同一事务里核对，没退就回滚。
    // 不良品处理单一侧的回退核对与采购入库共用（RejReverse）。
    internal static partial class MfgGen
    {
        const string RejRdSql = "select convert(varchar(20), b.iMPoIds) as MoDId,"
            + " convert(varchar(40), isnull(b.iQuantity,0)) as Qty from rdrecords10 b"
            + " where b.ID=? and isnull(b.iRejectIds,0)<>0";

        internal static void GuardRejUndo(StockAt at, int rdId)
        {
            RejUndo undo = new RejUndo();
            undo.RdId = rdId;
            undo.Reject = new RejReverse(RejSrc.Product, rdId);
            at.Before = undo.Before;
            at.After = undo.After;
        }

        sealed class RejUndo
        {
            public int RdId;
            public RejReverse Reject;
            readonly Dictionary<int, decimal> _expect = new Dictionary<int, decimal>();

            // 一张入库单可能有多行挂同一生产订单行，按订单行汇总本单数量。
            public void Before(object conn)
            {
                Reject.Before(conn);
                List<Dictionary<string, object>> rows = Rows.Query(conn, RejRdSql, new object[] { RdId }, 500);
                Dictionary<int, decimal> qty = new Dictionary<int, decimal>();
                for (int i = 0; rows != null && i < rows.Count; i++)
                {
                    int mo = CoRows.AsId(CoRows.Col(rows[i], "MoDId"));
                    decimal sum;
                    qty.TryGetValue(mo, out sum);
                    qty[mo] = sum + Num(CoRows.Col(rows[i], "Qty"));
                }
                foreach (KeyValuePair<int, decimal> kv in qty)
                {
                    _expect[kv.Key] = Num(Rows.Scalar(conn, MoQtySql, new object[] { kv.Key })) - kv.Value;
                }
            }

            // 未覆盖：与生单核对共用 RejCheckMoQty / RejSrc.RejCheckFlag 两个开关。已入库标记只在该行已无任何入库时要求回到 0。
            public void After(object conn)
            {
                foreach (KeyValuePair<int, decimal> kv in _expect)
                {
                    if (RejCheckMoQty && !Same(Num(Rows.Scalar(conn, MoQtySql, new object[] { kv.Key })), kv.Value))
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 没有回退生产订单合格入库数量");
                    }
                }
                Reject.After(conn);
            }
        }
    }
}
