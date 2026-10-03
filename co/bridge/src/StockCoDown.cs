namespace U8Co
{
    // 库存单据删除前的下游查询：采购发票、采购结算、销售发票引用了本单的行就不能删。
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
    }
}
