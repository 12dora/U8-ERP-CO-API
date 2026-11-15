using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 退货单保存：CoTrans 里带 UPDLOCK 重读原发货行（按选定的未开票 / 已开票规则再查可退数量）与订单行作基准，
    // GetVoucherNO 取号、Save(h, b, 0, newId)，提交前在同一事务里核对（XE 跟踪实测 U8 在保存里回写）：
    // 新单行数与负数量合计；原行 iRetQuantity +qty、fretqtywkp（未开票）或 fretqtyykp（已开票）+qty；
    // 挂订单时订单行 fretquantity +qty、iFHQuantity −qty。不符 409 回滚，审计 detail 记基准与实际值。
    internal static partial class SaleGen
    {
        const string NewLinesSql = "select count(*) as n, sum(case when x.iQuantity >= 0 then 1 else 0 end) as pos, "
            + "convert(varchar(40), isnull(sum(-x.iQuantity),0)) as back from DispatchLists x "
            + "inner join DispatchList xh on xh.DLID=x.DLID where xh.cDLCode=? and xh.bReturnFlag=1";
        const string RetLockSql = "select " + RetRoomCols + " from DispatchLists d with (updlock, holdlock) where d.iDLsID=?";
        const string RetNowSql = "select " + RetRoomCols + " from DispatchLists d where d.iDLsID=?";
        const string SoLockSql = "select convert(varchar(40), isnull(fretquantity,0)) as ret, "
            + "convert(varchar(40), isnull(iFHQuantity,0)) as fh from SO_SODetails with (updlock, holdlock) where iSOsID=?";
        const string SoNowSql = "select convert(varchar(40), isnull(fretquantity,0)) as ret, "
            + "convert(varchar(40), isnull(iFHQuantity,0)) as fh from SO_SODetails where iSOsID=?";

        static int SaveReturn(RetJob job, object co, object[] doms, out string savedCode)
        {
            WorkContext ctx = job.Ctx;
            bool open = false;
            savedCode = "";
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                Dictionary<int, decimal[]> so = ReadReturnBase(ctx.Conn, job);
                string no = SoSave.VoucherNo(co, doms, ctx.Item, "cDLCode");
                if (no.Length == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "没有生成退货单号");
                }
                SoDom.SetAttr(doms[0], "cDLCode", no);
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
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireReturned(conn, job, so); }, "退货单 " + no);
                int id = CoRows.AsId(args[3]);
                if (id <= 0)
                {
                    id = CoRows.AsId(SoDom.Attr(doms[0], "DLID"));
                }
                DocMark.Created(ctx.Conn, "sale_return", id, no);
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

        // 返回 订单行 → {fretquantity 基准, iFHQuantity 基准, 本次退货合计}。同一订单行可能来自多条发货行。
        static Dictionary<int, decimal[]> ReadReturnBase(object conn, RetJob job)
        {
            Dictionary<int, decimal[]> so = new Dictionary<int, decimal[]>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                RetLine line = job.Lines[i];
                Dictionary<string, object> row = Rows.One(conn, RetLockSql, new object[] { line.LineId });
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", "明细行不存在");
                }
                if (line.Qty > Room(row, job.Invoiced))
                {
                    throw new BridgeException(409, "state_mismatch", "超过可退货数量");
                }
                line.RetBefore = RoomNum(row, "r");
                line.AccBefore = RoomNum(row, job.Invoiced ? "y" : "w");
                // K12：参照退货申请单时带 UPDLOCK 记下申请行 fretqty 并重查可退数量。
                LockApply(conn, line);
                if (Linked(line))
                {
                    AddSo(conn, so, line.SoLine, line.Qty);
                }
            }
            return so;
        }

        // 订单行 → {fretquantity 基准, iFHQuantity 基准, 本次退货合计}，带 UPDLOCK。
        static void AddSo(object conn, Dictionary<int, decimal[]> so, int soLine, decimal qty)
        {
            decimal[] slot;
            if (!so.TryGetValue(soLine, out slot))
            {
                Dictionary<string, object> row = Rows.One(conn, SoLockSql, new object[] { soLine });
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", "销售订单行不存在");
                }
                slot = new decimal[] { PuInv.Num(CoRows.Col(row, "ret")), PuInv.Num(CoRows.Col(row, "fh")), 0m };
                so[soLine] = slot;
            }
            slot[2] = slot[2] + qty;
        }

        static void RequireReturned(object conn, RetJob job, Dictionary<int, decimal[]> so)
        {
            RequireNewLines(conn, job);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                RetLine line = job.Lines[i];
                RequireOrig(conn, job.Ctx.Item, line.LineId, new decimal[] { line.RetBefore, line.AccBefore, line.Qty },
                    job.Invoiced);
                RequireApplied(conn, job, line);
            }
            RequireSo(conn, job.Ctx.Item, so, 1m);
        }

        static void RequireNewLines(object conn, RetJob job)
        {
            Dictionary<string, object> row = Rows.One(conn, NewLinesSql, new object[] { job.Code });
            decimal want = 0m;
            for (int i = 0; i < job.Lines.Count; i++)
            {
                want = want + job.Lines[i].Qty;
            }
            decimal n = row == null ? 0m : PuInv.Num(CoRows.Col(row, "n"));
            decimal pos = row == null ? 0m : PuInv.Num(CoRows.Col(row, "pos"));
            decimal back = row == null ? 0m : PuInv.Num(CoRows.Col(row, "back"));
            if (n == job.Lines.Count && pos == 0m && Near(back, want))
            {
                return;
            }
            NoteBack(job.Ctx.Item, "新单 " + job.Code + " 行数 " + Num(n) + " 非负行 " + Num(pos) + " 退货合计 " + Num(back) + "/" + Num(want));
            throw new BridgeException(409, "u8_rejected", "U8 保存的退货单行与请求不一致");
        }

        // 原行：base = {iRetQuantity 基准, fretqtywkp 或 fretqtyykp 基准, 变化量（生单为 +qty，删除为 −qty）}。
        internal static void RequireOrig(object conn, WorkItem item, int lineId, decimal[] base0, bool invoiced)
        {
            Dictionary<string, object> row = Rows.One(conn, RetNowSql, new object[] { lineId });
            decimal r = row == null ? 0m : RoomNum(row, "r");
            decimal acc = row == null ? 0m : RoomNum(row, invoiced ? "y" : "w");
            if (Near(r, base0[0] + base0[2]) && Near(acc, base0[1] + base0[2]))
            {
                return;
            }
            NoteBack(item, "发货行" + Id(lineId) + " ret " + Num(base0[0]) + "→" + Num(r)
                + (invoiced ? " ykp " : " wkp ") + Num(base0[1]) + "→" + Num(acc) + " 应变 " + Num(base0[2]));
            throw new BridgeException(409, "u8_rejected", "U8 没有回写原发货单的累计退货数量");
        }

        // 订单行：sign 1 为生单（fretquantity 加、iFHQuantity 减），-1 为删除（反向）。
        internal static void RequireSo(object conn, WorkItem item, Dictionary<int, decimal[]> so, decimal sign)
        {
            foreach (KeyValuePair<int, decimal[]> pair in so)
            {
                decimal[] slot = pair.Value;
                Dictionary<string, object> row = Rows.One(conn, SoNowSql, new object[] { pair.Key });
                decimal ret = row == null ? 0m : PuInv.Num(CoRows.Col(row, "ret"));
                decimal fh = row == null ? 0m : PuInv.Num(CoRows.Col(row, "fh"));
                if (Near(ret, slot[0] + sign * slot[2]) && Near(fh, slot[1] - sign * slot[2]))
                {
                    continue;
                }
                NoteBack(item, "订单行" + Id(pair.Key) + " fret " + Num(slot[0]) + "→" + Num(ret)
                    + " fh " + Num(slot[1]) + "→" + Num(fh));
                throw new BridgeException(409, "u8_rejected", "U8 没有回写销售订单的退货数量或累计发货数量");
            }
        }

        static void NoteBack(WorkItem item, string text)
        {
            CoRows.Note(item, "回写核对 " + text);
        }

        static bool Near(decimal a, decimal b)
        {
            return Math.Abs(a - b) <= 0.000001m;
        }

        static string Num(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        static string Id(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
