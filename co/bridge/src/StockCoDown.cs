using System.Collections.Generic;

namespace U8Co
{
    // 库存单据删除前的检查：下游采购发票、采购结算、销售发票引用了本单的行就不能删；来源单据的累计数 / 标记要回退。
    internal static partial class StockCo
    {
        // 采购入库行已被采购发票或采购结算引用。
        internal static bool Billed(object conn, VoucherKind kind, int id)
        {
            return Down(conn, InvoiceSql(kind), id) || Down(conn, SettleSql(kind), id);
        }

        static bool Down(object conn, string sql, int id)
        {
            return Rows.Scalar(conn, sql, new object[] { id }) != null;
        }

        static string InvoiceSql(VoucherKind kind)
        {
            return "select top 1 convert(varchar(40), p.RdsId) from PurBillVouchs p "
                + "where isnull(p.RdsId,0)<>0 and p.RdsId in (select b.AutoID from "
                + kind.BodyTable + " b where b." + kind.BodyFk + "=?)";
        }

        static string SettleSql(VoucherKind kind)
        {
            return "select top 1 convert(varchar(40), s.iRdsID) from PurSettleVouchs s "
                + "where isnull(s.iRdsID,0)<>0 and s.iRdsID in (select b.AutoID from "
                + kind.BodyTable + " b where b." + kind.BodyFk + "=?)";
        }

        static string SaleSql(VoucherKind kind)
        {
            return "select top 1 convert(varchar(40), s.isaleoutid) from SaleBillVouchs s "
                + "where isnull(s.isaleoutid,0)<>0 and s.isaleoutid in (select b.AutoID from "
                + kind.BodyTable + " b where b." + kind.BodyFk + "=?)";
        }

        // 删除时核对 U8 回退来源单据的累计数量 / 标记：来料检验单、来料不良品处理单、到货单、调拨申请单（没退就回滚）。
        static void GuardUndo(StockAt at, VoucherKind kind, Dictionary<string, object> before, int id)
        {
            if (kind.StType == "12")
            {
                // 参照调拨申请单生成的调拨单：核对申请单行累计调拨数量退回（没有 iTRIds 的行不查）。
                StockGen.GuardTrUndo(at, id);
                return;
            }
            string source = StockMsg.Col(before, "cSource");
            if (kind.StType != "01")
            {
                return;
            }
            if (source == "来料检验单")
            {
                StockGen.GuardQmUndo(at, id);
            }
            else if (source == RejSrc.Incoming.Title)
            {
                StockGen.GuardRejUndo(at, id);
            }
            else if (source == "采购到货单")
            {
                StockGen.GuardArrUndo(at, id);
            }
        }
    }
}
