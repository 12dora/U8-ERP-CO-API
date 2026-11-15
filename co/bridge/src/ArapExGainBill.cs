using System;
using System.Globalization;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 汇兑损益的发票累计回写：脚本填好 #ap_SaleBillVouchHXdata / #ap_purbillHXdata（autoid, iexchsum, imoneysum）后，
    // 照 U8 交给回写组件（同一连接、同一事务）：
    //   新增：销售发票 Ussaupdispatch.clsWrite2Bill.UpdateBillForAR（采购发票由脚本直接改 PurBillVouchs.iTotal，U8 不调 UpdateBillForAP）；
    //   取消：销售发票同上，采购发票 Pu_Productinf.cls_ForAPsrv.UpdateBillForAP（U8 取消时两个都调，实测；签名按运行时类型库核对，见 --check-signatures）。
    // 调用前把涉及的发票行累计值整批快照到 #exg_bill，调用后一条 SQL 核对 累计 = 原值 + 临时表合计（U8 自己的公式，
    // 销售发票 iExchSum / iMoneySum，采购发票 iOriTotal / iTotal），不符回滚、409。
    internal static class ArapExGainBill
    {
        const string Write2Bill = "Ussaupdispatch.clsWrite2Bill";
        const string ForAp = "Pu_Productinf.cls_ForAPsrv";

        const string SaleSnap = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#exg_bill') IS NOT NULL DROP TABLE #exg_bill;"
            + " SELECT x.AutoID AS id, ISNULL(x.iExchSum,0) AS a, ISNULL(x.iMoneySum,0) AS b INTO #exg_bill"
            + " FROM SaleBillVouchs x WITH (UPDLOCK, HOLDLOCK)"
            + " INNER JOIN (SELECT DISTINCT autoid FROM #ap_SaleBillVouchHXdata) t ON x.AutoID = t.autoid; SET NOCOUNT OFF;";
        const string PurSnap = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#exg_bill') IS NOT NULL DROP TABLE #exg_bill;"
            + " SELECT x.ID AS id, ISNULL(x.iOriTotal,0) AS a, ISNULL(x.iTotal,0) AS b INTO #exg_bill"
            + " FROM PurBillVouchs x WITH (UPDLOCK, HOLDLOCK)"
            + " INNER JOIN (SELECT DISTINCT autoid FROM #ap_purbillHXdata) t ON x.ID = t.autoid; SET NOCOUNT OFF;";

        // 临时表里有、快照里没有的发票行（行已不存在）也算不符。
        const string SaleBad = "select top 1 convert(varchar(20), s.autoid) from (select autoid, sum(iexchsum) a, sum(imoneysum) b"
            + " from #ap_SaleBillVouchHXdata group by autoid) s left join #exg_bill p on p.id = s.autoid"
            + " left join SaleBillVouchs x on x.AutoID = s.autoid"
            + " where p.id is null or x.AutoID is null or abs(isnull(x.iExchSum,0) - (p.a + s.a)) > 0.005"
            + " or abs(isnull(x.iMoneySum,0) - (p.b + s.b)) > 0.005";
        const string PurBad = "select top 1 convert(varchar(20), s.autoid) from (select autoid, sum(iexchsum) a, sum(imoneysum) b"
            + " from #ap_purbillHXdata group by autoid) s left join #exg_bill p on p.id = s.autoid"
            + " left join PurBillVouchs x on x.ID = s.autoid"
            + " where p.id is null or x.ID is null or abs(isnull(x.iOriTotal,0) - (p.a + s.a)) > 0.005"
            + " or abs(isnull(x.iTotal,0) - (p.b + s.b)) > 0.005";

        const string DropSnap = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#exg_bill') IS NOT NULL DROP TABLE #exg_bill; SET NOCOUNT OFF;";

        public static void Sale(WorkContext ctx)
        {
            Run(ctx, false);
        }

        public static void Purchase(WorkContext ctx)
        {
            Run(ctx, true);
        }

        static void Run(WorkContext ctx, bool purchase)
        {
            object conn = ctx.Conn;
            string title = purchase ? "采购发票" : "销售发票";
            string bad = purchase ? PurBad : SaleBad;
            UnwriteoffSql.Run(conn, purchase ? PurSnap : SaleSnap);
            if (purchase)
            {
                Write2(ctx, ForAp, "UpdateBillForAP", ArapExGainSql.PurTable, title);
            }
            else
            {
                Write2(ctx, Write2Bill, "UpdateBillForAR", ArapExGainSql.SaleTable, title);
            }
            string line = Rows.Scalar(conn, bad, new object[0]);
            if (line != null && line.Trim().Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", "汇兑损益回写后" + title + "行 " + line.Trim() + " 的累计核销不符，已回滚");
            }
            UnwriteoffSql.Run(conn, DropSnap);
        }

        // 类型库：BSTR UpdateBillForAR(ByRef CN As Connection, ByRef strTblName As String)，没有 Init；引用 {0,1}（取消核销实测）。
        // BSTR UpdateBillForAP(ByRef DBconn As Connection, ByVal ctablename As String)：只有连接按引用 {0}。返回空串为成功，非空是 U8 的错误说明。
        // 组件不开自己的事务，在桥的事务里；调用前后 @@TRANCOUNT 变了就说不清写没写进去，504。
        static void Write2(WorkContext ctx, string progId, string member, string table, string title)
        {
            object w = null;
            try
            {
                w = ComUtil.Create(progId);
                if (w == null)
                {
                    throw new BridgeException(503, "com_unavailable", "组件无法创建 " + progId);
                }
                string tran = CoTrans.Count(ctx.Conn);
                object[] args = new object[] { ctx.Conn, table };
                int[] refs = progId == ForAp ? new int[] { 0 } : new int[] { 0, 1 };
                Refused(ComUtil.CallRef(w, member, args, refs), title);
                CoRows.Note(ctx.Item, progId + "." + member + " @@TRANCOUNT " + tran + " → " + CoTrans.Count(ctx.Conn));
                if (CoTrans.Count(ctx.Conn) != tran)
                {
                    throw new BridgeException(504, "outcome_unknown", "U8 回写" + title + "的组件改变了事务，结果未知，请核对汇兑损益");
                }
            }
            catch (COMException ex)
            {
                CoRows.Note(ctx.Item, progId + "." + member + " " + ex.Message);
                throw new BridgeException(409, "u8_rejected", ArapCo.Said(ex.Message, "U8 回写" + title + "失败"));
            }
            finally
            {
                ComUtil.Final(w);
            }
        }

        static void Refused(object ret, string title)
        {
            string text = ret == null ? "" : Convert.ToString(ret, CultureInfo.InvariantCulture).Trim();
            if (text.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 回写" + title + "失败：" + text);
            }
        }
    }
}
