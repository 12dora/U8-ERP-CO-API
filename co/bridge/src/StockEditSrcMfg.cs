namespace U8Co
{
    // 材料出库、产成品入库修改（StockEditSrc）沿用删除的闸门：来源、生产订单行状态。SQL 与删除共用（MfgGenDel）。
    internal static partial class MfgGen
    {
        internal static bool IsRejectSource(string source)
        {
            return source == RejSourceTitle;
        }

        internal static void RefuseEdit(object conn, VoucherKind kind, int id, string source)
        {
            string want = SourceName(kind);
            bool reject = kind.Name == "product_in" && IsRejectSource(source);
            if (source != want && !reject)
            {
                throw new BridgeException(409, "state_mismatch", "只能修改来源为" + want + "的单据");
            }
            // 合并检验的来源累计入库数（QMMergeCheckDetail.FSUMQUANTITY）在修改时的回写未经实测，不放行。
            if (kind.Name == "product_in" && Rows.Scalar(conn, MergeLineSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "参照合并检验单生成的产成品入库单不能修改，请删除后重新生成");
            }
            string sql = kind.StType == "11" ? OutMoSql : InMoSql;
            string status = Rows.Scalar(conn, sql, new object[] { id });
            if (status == null)
            {
                return;
            }
            status = status.Trim();
            if (status == "4" && kind.Name == "product_in")
            {
                throw new BridgeException(409, "state_mismatch",
                    "生产订单行已关闭（入库完成时 U8 自动关闭），请先在 U8 打开生产订单行再修改");
            }
            RequireReleased(status);
        }
    }
}
