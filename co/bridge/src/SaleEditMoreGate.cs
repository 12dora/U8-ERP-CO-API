using System.Collections.Generic;

namespace U8Co
{
    // 生单来的销售单据修改（SaleEditMore）复用删除的闸门和 SQL：发货单 / 退货单同 DeleteDispatch / SaleReturn.Delete，
    // 销售发票同 DeleteInvoice。审批中的文案换成「不能修改」，另拒绝已关闭的发货单和红字发票。
    internal static partial class SaleEdit
    {
        internal static Dictionary<string, object> GateEditDispatch(object conn, VoucherKind kind, int id)
        {
            Dictionary<string, object> snap = CoRows.HeadRow(conn, kind, id);
            Dictionary<string, object> shape = Rows.One(conn, ShapeSql, new object[] { id });
            if (snap == null || shape == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (SaleReturn.Is(kind))
            {
                RefuseNotRed(shape);
            }
            else
            {
                RefuseBlue(shape);
            }
            snap["vstate"] = CoRows.Col(shape, "iverifystate");
            SaleOrderCo.RefuseRunning(conn, kind, id, snap, "单据正在审批，不能修改");
            if (CoRows.Col(snap, "verifier").Length > 0 || CoRows.Col(snap, "verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (CoRows.Col(snap, "closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (Rows.Scalar(conn, OutSql, new object[] { id }) != null
                || Rows.Scalar(conn, BillSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
            return snap;
        }

        internal static Dictionary<string, object> GateEditInvoice(object conn, VoucherKind kind, int id)
        {
            Dictionary<string, object> snap = CoRows.HeadRow(conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.FlagOf(snap, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，不能修改");
            }
            if (CoRows.Col(snap, "verifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已复核");
            }
            if (CoRows.Col(snap, "ar_verifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已在应收系统审核");
            }
            if (CoRows.FlagOf(snap, "red"))
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字销售发票");
            }
            SaleGen.RefuseRedLinked(conn, id);
            RefuseStaleCheck(conn, id);
            return snap;
        }

        // 26 专用发票 VT 0，27 普通发票 VT 2；其他 400。
        internal static int InvoiceVtOf(Dictionary<string, object> snap)
        {
            return InvoiceVt(CoRows.Col(snap, "vouch_type"));
        }

        // 与 SaleReturn.RequireRed 同一判断和文案。
        static void RefuseNotRed(Dictionary<string, object> shape)
        {
            if (!CoRows.FlagOf(shape, "bReturnFlag") || CoRows.Col(shape, "cVouchType") != "05")
            {
                throw new BridgeException(400, "bad_request", "该单据不是退货单（红字发货单）");
            }
        }
    }
}
