namespace U8Co
{
    // 退货申请单（sale_return_apply，ReturnsApplyRead）的列表行，只读。列顺序同 ListKinds.cs 的说明。
    // 表头有 ufts，审核人 cVerifier、审核日期 dverifydate、关闭人 cCloser；表体没有 rowversion，增量只按表头 ufts。
    // APPLY 汇总表体行数、数量和价税合计（退货数量、金额为负数）。red 不填：退货申请单没有红蓝字之分。
    internal static partial class ListKinds
    {
        const string RaApply = " OUTER APPLY (SELECT COUNT(*) AS n, SUM(d.iQuantity) AS qty, SUM(d.iSum) AS amount "
            + "FROM SA_ReturnsApplyDetail d WHERE d.ID = h.ID) b";
        const string RaExtra = "h.cSTCode AS sale_type, h.cBusType AS bus_type, h.iswfcontrolled AS wf, "
            + "h.iverifystate AS verify_state, h.cexch_name AS currency, h.cMemo AS memo, b.n AS line_count, "
            + "b.qty AS quantity, b.amount AS amount, h.dcreatesystime AS created_at, h.dmodifysystime AS modified_at";

        static string[][] ReturnsApplyRows()
        {
            return new string[][]
            {
                new string[] { ReturnsApplyRead.KindName, "SA_ReturnsApplyMain", "ID", "h.cCode", "h.dDate",
                    "h.cCusCode", null, null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifier", "h.dverifydate", "h.cCloser", null, null, null,
                    "ufts", null, null, null, null, RaExtra, RaApply }
            };
        }
    }
}
