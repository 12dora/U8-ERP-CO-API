using System.Collections.Generic;

namespace U8Co
{
    // 销售发票修改的 DOM 补丁。GetVoucherData 读入后原样 Save 状态 1，U8 报
    // 「单据[..]第1行 存货[..]的记录不正确，先开票不可以参照发货单！」，什么也没改。
    // 读代码和账套数据的结论：U8 靠表头 iDisp 区分开票方式。界面参照发货单开的蓝字发票 iDisp 是 1；
    // 为 0 的是先开票（发货单比发票晚几秒生成，DispatchList.SBVID 指回发票）。SaleBillVouch.iDisp
    // 的列缺省是 0，桥早先的生单从来源视图 Sales_FHD_T 拷表头，那里没有 idisp，于是那些发票都是 iDisp=0，
    // U8 修改时把它当先开票，再看到行上有 iDLsID 就拒绝。读入的 DOM 来自 SaleBillVouchZT，其中有 idisp。
    // 所以：真先开票（有发货单 SBVID 指回本发票）或有行没有发货单来源的，登录前 409 拒绝（RefuseAdvance）；
    // 其余（每行都有 iDLsID）读入的 idisp 为 0 或空时写 1，已是 1 不动。表头 cdlcode、表体 cbdlcode 为空时
    // 按行上的 iDLsID 补发货单号（界面开的发票这两列都有值，生单时 cbdlcode 在 InvBodySkip 里被跳过）。
    internal static partial class SaleEditMore
    {
        const string DlCodeSql = "select h.cDLCode from DispatchLists d inner join DispatchList h on h.DLID=d.DLID "
            + "where d.iDLsID=?";
        const string AdvanceSql = "select top 1 DLID from DispatchList where SBVID=?";
        const string AdvanceMsg = "先开票或无发货单来源的发票暂不支持修改";

        // 登录前：每行都要有发货单来源（plan.Rows 的 Src 是 iDLsID），且没有发货单 SBVID 指回本发票（真先开票）。
        static void RefuseAdvance(object conn, SaleEditPlan plan)
        {
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                if (plan.Rows[i].Src <= 0)
                {
                    throw new BridgeException(409, "state_mismatch", AdvanceMsg);
                }
            }
            if (Rows.Scalar(conn, AdvanceSql, new object[] { plan.Id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", AdvanceMsg);
            }
        }

        static void PinInvoice(WorkContext ctx, object[] doms)
        {
            string first = PinBodyCodes(ctx, doms[1]);
            List<string> schema = DomRows.Schema(doms[0]);
            object head = HeadRow(doms[0]);
            string disp = Match(schema, "idisp");
            if (disp.Length == 0)
            {
                CoRows.Note(ctx.Item, "发票表头没有 idisp");
            }
            else if (DomRows.Get(head, disp).Trim().Length == 0 || DomRows.Get(head, disp).Trim() == "0")
            {
                CoRows.Note(ctx.Item, "idisp " + DomRows.Get(head, disp) + "→1");
                DomRows.Set(doms[0], head, disp, "1", schema);
            }
            string code = Match(schema, "cdlcode");
            if (code.Length > 0 && first.Length > 0 && DomRows.Get(head, code).Trim().Length == 0)
            {
                DomRows.Set(doms[0], head, code, first, schema);
            }
        }

        // 返回第一张发货单号（表头补 cdlcode 用）。读入的行没有 iDLsID 同 RefuseAdvance 拒绝。
        static string PinBodyCodes(WorkContext ctx, object body)
        {
            List<string> schema = DomRows.Schema(body);
            string col = Match(schema, "cbdlcode");
            string first = "";
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                int dl = CoRows.AsId(DomRows.Get(rows[i], "idlsid"));
                if (dl <= 0)
                {
                    throw new BridgeException(409, "state_mismatch", AdvanceMsg);
                }
                string have = col.Length == 0 ? "" : DomRows.Get(rows[i], col).Trim();
                string no = have.Length > 0 ? have : Values.Text(Rows.Scalar(ctx.Conn, DlCodeSql, new object[] { dl })).Trim();
                if (first.Length == 0)
                {
                    first = no;
                }
                if (col.Length > 0 && have.Length == 0 && no.Length > 0)
                {
                    DomRows.Set(body, rows[i], col, no, schema);
                }
            }
            return first;
        }
    }
}
