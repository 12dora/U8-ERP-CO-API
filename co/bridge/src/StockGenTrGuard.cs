using System;
using System.Collections.Generic;

namespace U8Co
{
    // 调拨单参照调拨申请单的事务内核对：生成前加锁重查可调拨数量，保存后核对申请单行 iTvSumQuantity 加了本次数量、
    // 新调拨单的行都带 iTRIds；删除这类调拨单时核对 iTvSumQuantity 退回（GuardTrUndo，StockCo.Delete 调用）。
    // 核对失败时回滚并 409；U8 已自行提交时由 StockCall.AfterCheck 改报 504。
    internal static partial class StockGen
    {
        const string TrSumSql = "select convert(varchar(40), isnull(iTvSumQuantity,0)) from ST_AppTransVouchs where autoID=?";
        const string TrLockSql = "select b.cBCloser as Closer, convert(varchar(40), isnull(b.iTvChkQuantity,0)) as Chk,"
            + " convert(varchar(40), isnull(b.iTvSumQuantity,0)) as Summed"
            + " from ST_AppTransVouchs b with (updlock, holdlock) where b.autoID=?";
        const string TrLinkedSql = "select count(*) from TransVouchs where ID=? and iTRIds=?";
        const string TrUsedSql = "select convert(varchar(20), t.iTRIds) as Line, convert(varchar(40), sum(isnull(t.iTVQuantity,0))) as Qty"
            + " from TransVouchs t where t.ID=? and isnull(t.iTRIds,0)<>0 group by t.iTRIds";
        const string TrAddSql = "update b set iTvSumQuantity = isnull(b.iTvSumQuantity,0) + t.q, iTVSumNum = isnull(b.iTVSumNum,0) + t.n"
            + " from ST_AppTransVouchs b inner join (select iTRIds, sum(isnull(iTVQuantity,0)) as q, sum(isnull(iTVNum,0)) as n"
            + " from TransVouchs where ID=? and isnull(iTRIds,0)<>0 group by iTRIds) t on t.iTRIds = b.autoID";
        const string TrSubSql = "update b set iTvSumQuantity = isnull(b.iTvSumQuantity,0) - t.q, iTVSumNum = isnull(b.iTVSumNum,0) - t.n"
            + " from ST_AppTransVouchs b inner join (select iTRIds, sum(isnull(iTVQuantity,0)) as q, sum(isnull(iTVNum,0)) as n"
            + " from TransVouchs where ID=? and isnull(iTRIds,0)<>0 group by iTRIds) t on t.iTRIds = b.autoID";

        // 删除调拨单：行上有 iTRIds（参照调拨申请单生成）才加核对。
        internal static void GuardTrUndo(StockAt at, int tvId)
        {
            TrUndo undo = new TrUndo();
            undo.TvId = tvId;
            at.Before = undo.Before;
            at.After = undo.After;
        }

        static decimal TrSum(object conn, int lineId)
        {
            return Num(Rows.Scalar(conn, TrSumSql, new object[] { lineId }));
        }

        // 未覆盖项的开关（TrBridgeWritesSum）：U8 不回写时由桥在事务里按新调拨单的行加减。
        static void TrWriteSum(object conn, int tvId, bool add)
        {
            if (!TrBridgeWritesSum)
            {
                return;
            }
            GlSql.Exec(conn, add ? TrAddSql : TrSubSql, new object[] { tvId });
        }

        static void TrSame(decimal now, decimal expect, string message)
        {
            if (Math.Abs(now - expect) > 0.000001m)
            {
                throw new BridgeException(409, "u8_rejected", message);
            }
        }

        sealed class TrGuard
        {
            readonly List<TrLine> _want;
            readonly object[] _args;
            readonly List<decimal> _base = new List<decimal>();

            public TrGuard(List<TrLine> want, object[] args)
            {
                _want = want;
                _args = args;
            }

            public void Before(object conn)
            {
                for (int i = 0; i < _want.Count; i++)
                {
                    TrLine item = _want[i];
                    _base.Add(RequireTrLeft(item, Rows.One(conn, TrLockSql, new object[] { item.LineId })));
                }
            }

            public void After(object conn)
            {
                int tvId = CoRows.AsId(Values.Text(_args[6]).Trim());
                if (tvId <= 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有返回新调拨单的主键");
                }
                TrWriteSum(conn, tvId, true);
                for (int i = 0; i < _want.Count; i++)
                {
                    TrLine item = _want[i];
                    string linked = Rows.Scalar(conn, TrLinkedSql, new object[] { tvId, item.LineId });
                    if (Num(linked) < 1m)
                    {
                        throw new BridgeException(409, "u8_rejected", "新调拨单的行没有指向调拨申请单行（iTRIds）");
                    }
                    TrSame(TrSum(conn, item.LineId), _base[i] + item.Qty, "U8 没有回写调拨申请单累计调拨数量");
                }
            }
        }

        sealed class TrUndo
        {
            public int TvId;
            readonly List<int> _ids = new List<int>();
            readonly List<decimal> _expect = new List<decimal>();

            public void Before(object conn)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, TrUsedSql, new object[] { TvId }, 500);
                for (int i = 0; rows != null && i < rows.Count; i++)
                {
                    int id = AsId(CoRows.Col(rows[i], "Line"));
                    decimal qty = Num(CoRows.Col(rows[i], "Qty"));
                    Rows.One(conn, TrLockSql, new object[] { id });
                    _ids.Add(id);
                    _expect.Add(TrSum(conn, id) - qty);
                }
                if (_ids.Count > 0)
                {
                    TrWriteSum(conn, TvId, false);
                }
            }

            public void After(object conn)
            {
                for (int i = 0; i < _ids.Count; i++)
                {
                    TrSame(TrSum(conn, _ids[i]), _expect[i], "U8 没有回退调拨申请单累计调拨数量");
                }
            }
        }
    }
}
