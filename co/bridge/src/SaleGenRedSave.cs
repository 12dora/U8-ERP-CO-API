using System;
using System.Collections.Generic;

namespace U8Co
{
    // 红字销售发票保存：CoTrans 里带 UPDLOCK 重读退货行 iSettleQuantity（负数）与订单行 iKPQuantity 作基准，
    // GetVoucherNO 取号（cSBVCode）、Save(h, b, 0, newId)，提交前在同一事务里核对（StockCall.AfterCheck）：
    // 新发票行数、全是负数量、合计等于请求；退货行 iSettleQuantity 变化 −qty（U8：红字发票的退货行 iSettleQuantity
    // 等于发票数量，负数）；订单行 iKPQuantity 净减 qty（例：订单 10、红票 −4 → iKPQuantity 6）。
    // 不符 409 回滚，审计 detail 记基准与实际值。原蓝字发货行的 fretqtyykp 在退货单保存时已写，不核对。
    internal static partial class SaleGen
    {
        // iKPQuantity 在发票保存时回写，复核、弃复不再变，已在测试账套核对。复核时回写才置 false。
        static readonly bool RedCheckSoKp = true;
        const string RInvNewSql = "select count(*) as n, sum(case when b.iQuantity >= 0 then 1 else 0 end) as pos, "
            + "convert(varchar(40), isnull(sum(-b.iQuantity),0)) as back from SaleBillVouchs b "
            + "inner join SaleBillVouch h on h.SBVID=b.SBVID where h.cSBVCode=? and h.cVouchType=? and h.bReturnFlag=1";
        const string RInvSetLockSql = "select convert(varchar(40), isnull(iSettleQuantity,0)) as v "
            + "from DispatchLists with (updlock, holdlock) where iDLsID=?";
        const string RInvSetNowSql = "select convert(varchar(40), isnull(iSettleQuantity,0)) as v from DispatchLists where iDLsID=?";
        const string RInvKpLockSql = "select convert(varchar(40), isnull(iKPQuantity,0)) as v "
            + "from SO_SODetails with (updlock, holdlock) where iSOsID=?";
        const string RInvKpNowSql = "select convert(varchar(40), isnull(iKPQuantity,0)) as v from SO_SODetails where iSOsID=?";

        static int SaveRed(RedJob job, object co, object[] doms, out string savedCode)
        {
            WorkContext ctx = job.Ctx;
            bool open = false;
            savedCode = "";
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                RedMoves moves = new RedMoves();
                for (int i = 0; i < job.Lines.Count; i++)
                {
                    RedLine line = job.Lines[i];
                    RedLock(ctx.Conn, moves.Settle, line.LineId, RInvSetLockSql, -line.Qty, "退货单行不存在");
                    if (RedCheckSoKp && line.SoLine > 0)
                    {
                        RedLock(ctx.Conn, moves.Kp, line.SoLine, RInvKpLockSql, -line.Qty, "销售订单行不存在");
                    }
                }
                RefuseRedRoom(job, moves);
                string no = SoSave.VoucherNo(co, doms, ctx.Item, "cSBVCode");
                if (no.Length == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "没有生成发票号");
                }
                SoDom.SetAttr(doms[0], "cSBVCode", no);
                SoDom.SetAttr(doms[0], "sbvid", "");
                savedCode = no;
                job.Code = no;
                object[] args = new object[] { doms[0], doms[1], (short)0, "" };
                object ret = ComUtil.CallRef(co, "Save", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(ctx.Item, "Save " + msg);
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireRedSaved(conn, job, moves); }, "红字发票 " + no);
                int id = CoRows.AsId(args[3]);
                DocMark.Created(ctx.Conn, "sale_invoice", id, no);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                return id;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 加锁重读后再核一次剩余：|iSettleQuantity| 加本次不能超过退货数量（计划时的剩余可能已被并发开票用掉）。
        static void RefuseRedRoom(RedJob job, RedMoves moves)
        {
            List<Dictionary<string, object>> rows = Rows.Query(job.Ctx.Conn, RInvLineSql, new object[] { job.SourceId }, 5000);
            for (int i = 0; i < rows.Count; i++)
            {
                decimal[] slot;
                if (!moves.Settle.TryGetValue(CoRows.AsId(CoRows.Col(rows[i], "iDLsID")), out slot))
                {
                    continue;
                }
                if (Math.Abs(slot[0]) - slot[1] > PuInv.Num(CoRows.Col(rows[i], "q")) + 0.000001m)
                {
                    throw new BridgeException(409, "state_mismatch", "超过可生单数量");
                }
            }
        }

        static void RequireRedSaved(object conn, RedJob job, RedMoves moves)
        {
            Dictionary<string, object> row = Rows.One(conn, RInvNewSql, new object[] { job.Code, job.Want.Vouch });
            decimal want = 0m;
            for (int i = 0; i < job.Lines.Count; i++)
            {
                want = want + job.Lines[i].Qty;
            }
            decimal n = row == null ? 0m : PuInv.Num(CoRows.Col(row, "n"));
            decimal pos = row == null ? 0m : PuInv.Num(CoRows.Col(row, "pos"));
            decimal back = row == null ? 0m : PuInv.Num(CoRows.Col(row, "back"));
            if (n != job.Lines.Count || pos != 0m || !Near(back, want))
            {
                NoteBack(job.Ctx.Item, "新发票 " + job.Code + " 行数 " + Num(n) + " 非负行 " + Num(pos) + " 合计 " + Num(back)
                    + "/" + Num(want));
                throw new BridgeException(409, "u8_rejected", "U8 保存的红字发票行与请求不一致");
            }
            RequireRedMoved(conn, job.Ctx.Item, moves);
        }

        // 行 → {基准, 应变量}；同一行多次出现时变化量累加。
        internal static void RedLock(object conn, Dictionary<int, decimal[]> map, int key, string lockSql, decimal delta,
            string missing)
        {
            decimal[] slot;
            if (!map.TryGetValue(key, out slot))
            {
                Dictionary<string, object> row = Rows.One(conn, lockSql, new object[] { key });
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", missing);
                }
                slot = new decimal[] { PuInv.Num(CoRows.Col(row, "v")), 0m };
                map[key] = slot;
            }
            slot[1] = slot[1] + delta;
        }

        internal static void RequireRedMoved(object conn, WorkItem item, RedMoves moves)
        {
            RequireMap(conn, item, moves.Settle, RInvSetNowSql, "退货单行", "U8 没有回写退货单的累计开票数量");
            RequireMap(conn, item, moves.Kp, RInvKpNowSql, "订单行", "U8 没有回写销售订单的累计开票数量");
        }

        static void RequireMap(object conn, WorkItem item, Dictionary<int, decimal[]> map, string nowSql, string label,
            string message)
        {
            foreach (KeyValuePair<int, decimal[]> pair in map)
            {
                Dictionary<string, object> row = Rows.One(conn, nowSql, new object[] { pair.Key });
                decimal now = row == null ? 0m : PuInv.Num(CoRows.Col(row, "v"));
                if (Near(now, pair.Value[0] + pair.Value[1]))
                {
                    continue;
                }
                NoteBack(item, label + Id(pair.Key) + " " + Num(pair.Value[0]) + "→" + Num(now) + " 应变 " + Num(pair.Value[1]));
                throw new BridgeException(409, "u8_rejected", message);
            }
        }

        internal sealed class RedMoves
        {
            // 退货行 iDLsID → {iSettleQuantity 基准, 应变量}。
            internal Dictionary<int, decimal[]> Settle = new Dictionary<int, decimal[]>();
            // 订单行 iSOsID → {iKPQuantity 基准, 应变量}。
            internal Dictionary<int, decimal[]> Kp = new Dictionary<int, decimal[]>();
        }
    }
}
