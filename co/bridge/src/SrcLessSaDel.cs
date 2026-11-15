using System;
using System.Collections.Generic;

namespace U8Co
{
    // 删除先开票销售发票（有发货单 DispatchList.SBVID 指回本发票）：同蓝字发票删除（每行 editprop=D，Delete(头, 体) 引用 {0,1}），
    // 但在同一事务里核对 U8 把它按发票生成的发货单也删掉了；还在就回滚，409。未覆盖：U8 删除发票时是否连带删除发货单。
    internal static class SrcLessSaDel
    {
        const string MadeDlSql = "select top 1 convert(varchar(20), DLID) from DispatchList where SBVID=?";
        const string LeftMsg = "先开票生成的发货单未随发票删除，请到 U8 客户端处理";

        // SaleEdit.DeleteInvoice 的钩子：只有真先开票（有发货单指回本发票）走这里，普通蓝字发票不变。
        public static bool IsAdvance(object conn, int invoiceId)
        {
            return Rows.Scalar(conn, MadeDlSql, new object[] { invoiceId }) != null;
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id, int vt)
        {
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, vt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                MarkBody(doms[1]);
                InTran(ctx, co, doms, id);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
            return SaleOrderCo.Gone(ctx, kind, id);
        }

        static void MarkBody(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                DomRows.Set(body, rows[i], "editprop", "D");
            }
        }

        static void InTran(WorkContext ctx, object co, object[] doms, int id)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(ctx.Item, "Delete " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object c) { RequireGone(c, id); }, "先开票发票 " + id);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static void RequireGone(object conn, int id)
        {
            if (Rows.Scalar(conn, MadeDlSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", LeftMsg);
            }
        }
    }
}
