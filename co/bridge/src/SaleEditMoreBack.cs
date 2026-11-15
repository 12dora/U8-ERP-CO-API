using System;
using System.Collections.Generic;

namespace U8Co
{
    // 生单来的销售单据修改（SaleEditMore）的回写基准与核对，复用生单、删除的锁定 SQL 与核对函数。每一行（含没改的，应变 0）都核对：
    // 发货单（U8 按差调整）：订单行 iFHQuantity = 基准 + Σ(新 − 旧)，fretquantity 不变。
    //   新增行（Added）旧数量为 0，按新数量计入该订单行的应变（未经实测）。
    // 退货单：同删除（SaleReturnDel），按绝对值少退的数 Σ(|旧| − |新|) 退回：原行 iRetQuantity 与
    //   fretqtywkp（bneedbill=0）或 fretqtyykp（bneedbill=1）减，订单行 fretquantity 减、iFHQuantity 加。
    // 销售发票：发货行 iSettleQuantity = 基准 + Σ(新 − 旧)，发货行其余累计不变。
    //   订单行 iKPQuantity 只记基准、实际和应变到审计，不核对（未覆盖：U8 修改发票时是否按差回写累计开票）。
    internal static partial class SaleGen
    {
        const string KpLockSql = "select convert(varchar(40), isnull(iKPQuantity,0)) as kp from SO_SODetails "
            + "with (updlock, holdlock) where iSOsID=?";
        const string KpNowSql = "select convert(varchar(40), isnull(iKPQuantity,0)) as kp from SO_SODetails where iSOsID=?";

        internal static SaleEditBack EditBase(object conn, SaleEditPlan plan)
        {
            SaleEditBack back = new SaleEditBack();
            back.Kind = plan.Kind.Name;
            if (plan.Invoice)
            {
                SettleBase(conn, plan, back.Settle);
                KpBase(conn, plan, back.Kp);
                return back;
            }
            if (plan.Red)
            {
                back.Invoiced = CoRows.FlagOf(Rows.One(conn, NeedBillSql, new object[] { plan.Id }), "bneedbill");
            }
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                SaleEditRow row = plan.Rows[i];
                if (!plan.Red)
                {
                    if (row.SoLine > 0)
                    {
                        AddSo(conn, back.So, row.SoLine, row.New - row.Old);
                    }
                    continue;
                }
                decimal less = Math.Abs(row.Old) - Math.Abs(row.New);
                if (row.Src > 0)
                {
                    AddOrig(conn, back.Orig, row.Src, less, back.Invoiced);
                }
                if (row.SoLine > 0)
                {
                    AddSo(conn, back.So, row.SoLine, less);
                }
            }
            return back;
        }

        internal static void EditCheck(object conn, WorkItem item, SaleEditBack back)
        {
            if (back.Kind == "sale_invoice")
            {
                NoteKp(conn, item, back.Kp);
                RequireSettled(conn, item, back.Settle);
                return;
            }
            if (back.Kind == SaleReturn.KindName)
            {
                // 挂退货申请单行的退货行不能改数量、不能删（SaleEditMore.RefuseAppLine），申请行 fretqty 不变，这里不核对。
                RequireUndone(conn, item, back.Orig, back.So, new Dictionary<int, decimal[]>(), back.Invoiced);
                return;
            }
            RequireShipped(conn, item, back.So);
        }

        // 发货行 → {q, s, w, y, r 基准, 应变}，带 UPDLOCK（RetLockSql）。
        static void SettleBase(object conn, SaleEditPlan plan, Dictionary<int, decimal[]> settle)
        {
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                SaleEditRow row = plan.Rows[i];
                if (row.Src <= 0)
                {
                    continue;
                }
                decimal[] slot;
                if (!settle.TryGetValue(row.Src, out slot))
                {
                    Dictionary<string, object> found = Rows.One(conn, RetLockSql, new object[] { row.Src });
                    if (found == null)
                    {
                        throw new BridgeException(409, "state_mismatch", "发货单行不存在");
                    }
                    slot = new decimal[] {
                        RoomNum(found, "q"), RoomNum(found, "s"), RoomNum(found, "w"), RoomNum(found, "y"), RoomNum(found, "r"), 0m
                    };
                    settle[row.Src] = slot;
                }
                slot[5] = slot[5] + row.New - row.Old;
            }
        }

        // 订单行 → {iKPQuantity 基准, 应变}，带 UPDLOCK。订单行不存在不拒绝（只记录，未经实测）。
        static void KpBase(object conn, SaleEditPlan plan, Dictionary<int, decimal[]> kp)
        {
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                SaleEditRow row = plan.Rows[i];
                if (row.SoLine <= 0)
                {
                    continue;
                }
                decimal[] slot;
                if (!kp.TryGetValue(row.SoLine, out slot))
                {
                    Dictionary<string, object> found = Rows.One(conn, KpLockSql, new object[] { row.SoLine });
                    slot = new decimal[] { found == null ? 0m : PuInv.Num(CoRows.Col(found, "kp")), 0m };
                    kp[row.SoLine] = slot;
                }
                slot[1] = slot[1] + row.New - row.Old;
            }
        }

        // 未覆盖：只把订单行累计开票数量的前后值写进审计 detail，不因不符而失败。
        static void NoteKp(object conn, WorkItem item, Dictionary<int, decimal[]> kp)
        {
            foreach (KeyValuePair<int, decimal[]> pair in kp)
            {
                Dictionary<string, object> row = Rows.One(conn, KpNowSql, new object[] { pair.Key });
                string now = row == null ? "无" : Num(PuInv.Num(CoRows.Col(row, "kp")));
                NoteBack(item, "订单行" + Id(pair.Key) + " kp " + Num(pair.Value[0]) + "→" + now + " 应变 " + Num(pair.Value[1]));
            }
        }

        static void RequireShipped(object conn, WorkItem item, Dictionary<int, decimal[]> so)
        {
            foreach (KeyValuePair<int, decimal[]> pair in so)
            {
                decimal[] slot = pair.Value;
                Dictionary<string, object> row = Rows.One(conn, SoNowSql, new object[] { pair.Key });
                decimal ret = row == null ? 0m : PuInv.Num(CoRows.Col(row, "ret"));
                decimal fh = row == null ? 0m : PuInv.Num(CoRows.Col(row, "fh"));
                if (row != null && Near(ret, slot[0]) && Near(fh, slot[1] + slot[2]))
                {
                    continue;
                }
                NoteBack(item, "订单行" + Id(pair.Key) + " fh " + Num(slot[1]) + "→" + Num(fh) + " 应变 " + Num(slot[2])
                    + " fret " + Num(slot[0]) + "→" + Num(ret));
                throw new BridgeException(409, "u8_rejected", "U8 没有按修改回写销售订单的累计发货数量");
            }
        }

        static void RequireSettled(object conn, WorkItem item, Dictionary<int, decimal[]> settle)
        {
            foreach (KeyValuePair<int, decimal[]> pair in settle)
            {
                decimal[] b = pair.Value;
                Dictionary<string, object> row = Rows.One(conn, RetNowSql, new object[] { pair.Key });
                decimal s = row == null ? 0m : RoomNum(row, "s");
                bool same = row != null && Near(RoomNum(row, "q"), b[0]) && Near(RoomNum(row, "w"), b[2])
                    && Near(RoomNum(row, "y"), b[3]) && Near(RoomNum(row, "r"), b[4]);
                if (same && Near(s, b[1] + b[5]))
                {
                    continue;
                }
                NoteBack(item, "发货行" + Id(pair.Key) + " settle " + Num(b[1]) + "→" + Num(s) + " 应变 " + Num(b[5])
                    + (same ? "" : " 其他累计有变"));
                throw new BridgeException(409, "u8_rejected", "U8 没有按修改回写发货单的累计开票数量");
            }
        }
    }

    // Orig：原发货行 → {iRetQuantity 基准, fretqtywkp / fretqtyykp 基准, 应变}；So：订单行 → {fretquantity 基准,
    // iFHQuantity 基准, 应变}；Settle：发货行 → {iQuantity, iSettleQuantity, fretqtywkp, fretqtyykp, iRetQuantity 基准, 应变}；
    // Kp：订单行 → {iKPQuantity 基准, 应变}（销售发票，只记录）。
    internal sealed class SaleEditBack
    {
        internal string Kind;
        internal bool Invoiced;
        internal Dictionary<int, decimal[]> Orig = new Dictionary<int, decimal[]>();
        internal Dictionary<int, decimal[]> So = new Dictionary<int, decimal[]>();
        internal Dictionary<int, decimal[]> Settle = new Dictionary<int, decimal[]>();
        internal Dictionary<int, decimal[]> Kp = new Dictionary<int, decimal[]>();
    }
}
