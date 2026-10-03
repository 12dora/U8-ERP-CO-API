using System;
using System.Collections.Generic;

namespace U8Co
{
    // 退货单删除：VT 10 读入、每行 editprop=D、CoTrans 里带 UPDLOCK 记下原发货行（按 iCorID）和订单行的基准，
    // CO Delete 之后在同一事务里核对 U8 把回写退回去了：原行 iRetQuantity 与 fretqtywkp（表头 bneedbill=0）或
    // fretqtyykp（bneedbill=1）减退货数；订单行 fretquantity 减、iFHQuantity 加。不符 409 回滚，审计记前后值。
    // 没有 iCorID 的旧退货行不核对原行。K12：挂着退货申请单行（irtnappid）的行另核对申请行 fretqty 退回（SaleGenApplyRet.cs）。
    internal static partial class SaleGen
    {
        const string RedLinesSql = "select iDLsID, iCorID, iSOsID, isnull(irtnappid,0) as app, "
            + "convert(varchar(40), -isnull(iQuantity,0)) as qty from DispatchLists where DLID=?";
        const string NeedBillSql = "select bneedbill from DispatchList where DLID=?";

        internal static ApiResult DeleteReturn(WorkContext ctx, VoucherKind kind, int id)
        {
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, SaleReturn.RedVt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                List<object> rows = DomRows.RowsOf(doms[1]);
                for (int i = 0; i < rows.Count; i++)
                {
                    DomRows.Set(doms[1], rows[i], "editprop", "D");
                }
                DeleteTran(ctx, co, doms, id);
                return SaleOrderCo.Gone(ctx, kind, id);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        static void DeleteTran(WorkContext ctx, object co, object[] doms, int id)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                bool invoiced = CoRows.FlagOf(Rows.One(ctx.Conn, NeedBillSql, new object[] { id }), "bneedbill");
                Dictionary<int, decimal[]> origs = new Dictionary<int, decimal[]>();
                Dictionary<int, decimal[]> so = new Dictionary<int, decimal[]>();
                // K12：参照退货申请单生成的行（irtnappid），核对申请行 fretqty 退回（SaleGenApplyRet.cs）。
                Dictionary<int, decimal[]> apps = new Dictionary<int, decimal[]>();
                ReadDeleteBase(ctx.Conn, id, invoiced, origs, so, apps);
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(ctx.Item, "Delete " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireUndone(conn, ctx.Item, origs, so, apps, invoiced); },
                    "退货单 " + Id(id));
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // origs：原行 → {iRetQuantity 基准, fretqtywkp / fretqtyykp 基准, 变化量（负的退货数合计）}。
        static void ReadDeleteBase(object conn, int id, bool invoiced, Dictionary<int, decimal[]> origs,
            Dictionary<int, decimal[]> so, Dictionary<int, decimal[]> apps)
        {
            List<Dictionary<string, object>> lines = Rows.Query(conn, RedLinesSql, new object[] { id }, 500);
            for (int i = 0; i < lines.Count; i++)
            {
                decimal qty = PuInv.Num(CoRows.Col(lines[i], "qty"));
                int orig = CoRows.AsId(CoRows.Col(lines[i], "iCorID"));
                if (orig > 0)
                {
                    AddOrig(conn, origs, orig, qty, invoiced);
                }
                int soLine = CoRows.AsId(CoRows.Col(lines[i], "iSOsID"));
                if (soLine > 0)
                {
                    AddSo(conn, so, soLine, qty);
                }
                AddApp(conn, apps, CoRows.AsId(CoRows.Col(lines[i], "app")), qty);
            }
        }

        static void AddOrig(object conn, Dictionary<int, decimal[]> origs, int orig, decimal qty, bool invoiced)
        {
            decimal[] slot;
            if (!origs.TryGetValue(orig, out slot))
            {
                Dictionary<string, object> row = Rows.One(conn, RetLockSql, new object[] { orig });
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", "原发货单行不存在");
                }
                slot = new decimal[] { RoomNum(row, "r"), RoomNum(row, invoiced ? "y" : "w"), 0m };
                origs[orig] = slot;
            }
            slot[2] = slot[2] - qty;
        }

        static void RequireUndone(object conn, WorkItem item, Dictionary<int, decimal[]> origs,
            Dictionary<int, decimal[]> so, Dictionary<int, decimal[]> apps, bool invoiced)
        {
            foreach (KeyValuePair<int, decimal[]> pair in origs)
            {
                RequireOrig(conn, item, pair.Key, pair.Value, invoiced);
            }
            RequireSo(conn, item, so, -1m);
            RequireAppsBack(conn, item, apps);
        }
    }
}
