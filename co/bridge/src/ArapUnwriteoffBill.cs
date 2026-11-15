using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 取消核销时发票累计核销的回写。两段临时表 SQL 与 U8「取消操作」取消核销时一致（实测核对）：
    // 销售发票：#ap_SaleBillVouchHXdata 填好后交给 U8 的 Ussaupdispatch.clsWrite2Bill.UpdateBillForAR（同一连接），
    //   它加回 SaleBillVouchs.iExchSum / iMoneySum，并连带订单、发货单的累计核销和信用额度（U8 自己的逻辑，桥不另写）；
    // 采购发票：#ap_PurBillVouchHXdata → #u8co_purbill → PurBillVouchs.iOriTotal / iTotal（9P 分支自带的 SQL，U8 不调 UpdateBillForAP）。
    // 写之前记下各行的累计值和临时表里的合计，写之后核对 累计 = 原值 + 临时表合计（U8 自己的公式），不符回滚、409。
    internal static class ArapUnwriteoffBill
    {
        const decimal Tolerance = 0.005m;
        const string Write2Bill = "Ussaupdispatch.clsWrite2Bill";
        const string SaleTable = "#ap_SaleBillVouchHXdata";

        internal const string SaleTmp = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#ap_SaleBillVouchHXdata') IS NOT NULL "
            + "DROP TABLE #ap_SaleBillVouchHXdata; CREATE TABLE #ap_SaleBillVouchHXdata(autoid bigint, iexchsum decimal(29,6), "
            + "imoneysum decimal(29,6)); CREATE INDEX idx_HXautoid ON #ap_SaleBillVouchHXdata(autoid); SET NOCOUNT OFF;";

        const string SaleFill = "INSERT INTO #ap_SaleBillVouchHXdata (autoid,iexchsum,imoneysum) SELECT b.autoid, "
            + "CASE WHEN a.iJE=0 AND a.iWB=0 THEN b.iSum ELSE CASE WHEN bReturnFlag=1 THEN 0-a.iWB ELSE a.iWB END END AS iexchsum, "
            + "CASE WHEN a.iJE=0 AND a.iWB=0 THEN b.iNatSum ELSE CASE WHEN bReturnFlag=1 THEN 0-a.iJE ELSE a.iJE END END AS imoneysum "
            + "FROM (SELECT ard.iBVid AS iInvID, "
            + "CASE WHEN ard.csign='F' AND cCoVouchType < '48' THEN (ard.iDAmount+ard.iDAmount) "
            + "ELSE 0-(ard.iDAmount+ard.iCAmount) END AS iJE, "
            + "CASE WHEN ard.csign='F' AND cCoVouchType < '48' THEN (ard.iDAmount_f+ard.iCAmount_f) "
            + "ELSE 0-(ard.iDAmount_f+ard.iCAmount_f) END AS iWB "
            + "FROM AR_detail ard WHERE cProcStyle=? AND cCancelNo=? AND (cflag=N'AR' OR cbustype=N'代理进口') "
            + "AND ISNULL(cContractID,'')='' AND cCoVouchType LIKE '2%') a "
            + "LEFT JOIN SaleBillVouchs b ON a.iInvID=b.autoid LEFT JOIN SaleBillVouch c ON c.SBVID=b.SBVID";

        const string PurTmp = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#ap_PurBillVouchHXdata') IS NOT NULL "
            + "DROP TABLE #ap_PurBillVouchHXdata; CREATE TABLE #ap_PurBillVouchHXdata(id bigint, iOriTotal decimal(29,6), "
            + "iTotal decimal(29,6)); CREATE INDEX idx_HXid ON #ap_PurBillVouchHXdata(id); SET NOCOUNT OFF;";

        const string PurFill = "INSERT INTO #ap_PurBillVouchHXdata (id,iOriTotal,iTotal) SELECT b.id, "
            + "CASE WHEN a.iJE=0 AND a.iWB=0 THEN b.iOriSum ELSE CASE WHEN bNegative=1 THEN 0-a.iWB ELSE a.iWB END END AS iOriTotal, "
            + "CASE WHEN a.iJE=0 AND a.iWB=0 THEN b.iSum ELSE CASE WHEN bNegative=1 THEN 0-a.iJE ELSE a.iJE END END AS iTotal "
            + "FROM (SELECT ard.iBVid AS iInvID, "
            + "CASE WHEN ard.csign='F' AND cCoVouchType < '48' THEN (ard.iDAmount+ard.iDAmount) "
            + "ELSE 0-(ard.iDAmount+ard.iCAmount) END AS iJE, "
            + "CASE WHEN ard.csign='F' AND cCoVouchType < '48' THEN (ard.iDAmount_f+ard.iCAmount_f) "
            + "ELSE 0-(ard.iDAmount_f+ard.iCAmount_f) END AS iWB "
            + "FROM AP_detail ard WHERE cProcStyle=? AND cCancelNo=? AND cflag=N'AP' "
            + "AND ISNULL(cContractID,'')='' AND cCoVouchType LIKE '0%') a "
            + "LEFT JOIN PurBillVouchs b ON a.iInvID=b.id LEFT JOIN PurBillVouch c ON c.PBVID=b.PBVID";

        // 临时表不用 U8 的 #billtmp：同一连接上先调过 UpdateBillForAR 时它留下的 #billtmp 列不同，整批编译会报「列名 'id' 无效」。
        const string PurUpdate = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#u8co_purbill') IS NOT NULL DROP TABLE #u8co_purbill; "
            + "SELECT SUM(ISNULL(iOriTotal,0)) AS iOriTotal, SUM(ISNULL(iTotal,0)) AS iTotal, id INTO #u8co_purbill "
            + "FROM #ap_PurBillVouchHXdata GROUP BY id; CREATE INDEX idx_u8co_purbill ON #u8co_purbill(id); "
            + "UPDATE PurBillVouchs SET iOriTotal=ISNULL(PurBillVouchs.iOriTotal,0)+ISNULL(#u8co_purbill.iOriTotal,0), "
            + "iTotal=ISNULL(PurBillVouchs.iTotal,0)+ISNULL(#u8co_purbill.iTotal,0) "
            + "FROM PurBillVouchs INNER JOIN #u8co_purbill ON PurBillVouchs.id=#u8co_purbill.id; DROP TABLE #u8co_purbill; SET NOCOUNT OFF;";

        const string SaleSums = "select convert(varchar(20), autoid) as id, {a} as a, {b} as b from #ap_SaleBillVouchHXdata "
            + "where autoid is not null group by autoid";
        const string PurSums = "select convert(varchar(20), id) as id, {a} as a, {b} as b from #ap_PurBillVouchHXdata "
            + "where id is not null group by id";
        const string SaleNow = "select {a} as a, {b} as b from SaleBillVouchs x with (UPDLOCK, HOLDLOCK) where x.AutoID=?";
        const string PurNow = "select {a} as a, {b} as b from PurBillVouchs x with (UPDLOCK, HOLDLOCK) where x.ID=?";

        public static void Sale(WorkContext ctx, UnwriteoffPlan plan)
        {
            Sale(ctx, "9P", plan.CancelNo, "取消核销");
        }

        public static void Purchase(object conn, UnwriteoffPlan plan)
        {
            Purchase(conn, "9P", plan.CancelNo, "取消核销");
        }

        // 按处理方式和处理号回写（取消核销 9P；取消应收冲应付 9I、应付冲应收 9J 用同一段，ArapProcCancel；
        // 取消红票对冲 9N 换成 ArapRedSql 的取数：处理行的借+贷原样加回，不走 9P 公式）。
        // label 是核对不符时消息的前缀（「取消核销」等）。
        public static void Sale(WorkContext ctx, string style, string cancelNo, string label)
        {
            object conn = ctx.Conn;
            UnwriteoffSql.Run(conn, SaleTmp);
            GlSql.Exec(conn, style == ArapRedRule.Style ? ArapRedSql.CancelSaleFill : SaleFill, new object[] { style, cancelNo });
            Dictionary<int, decimal[]> sums = Read(conn, Cols(SaleSums, "sum(iexchsum)", "sum(imoneysum)"), null);
            string now = Cols(SaleNow, "x.iExchSum", "x.iMoneySum");
            Dictionary<int, decimal[]> before = Read(conn, now, sums.Keys);
            Write2(ctx, style == "9P" ? "该核销号" : "处理号 " + cancelNo);
            Check(conn, now, sums, before, label + "后销售发票");
            UnwriteoffSql.Run(conn, "DROP TABLE " + SaleTable);
        }

        public static void Purchase(object conn, string style, string cancelNo, string label)
        {
            UnwriteoffSql.Run(conn, PurTmp);
            GlSql.Exec(conn, style == ArapRedRule.Style ? ArapRedSql.CancelPurFill : PurFill, new object[] { style, cancelNo });
            Dictionary<int, decimal[]> sums = Read(conn, Cols(PurSums, "sum(iOriTotal)", "sum(iTotal)"), null);
            string now = Cols(PurNow, "x.iOriTotal", "x.iTotal");
            Dictionary<int, decimal[]> before = Read(conn, now, sums.Keys);
            UnwriteoffSql.Run(conn, PurUpdate);
            Check(conn, now, sums, before, label + "后采购发票");
            UnwriteoffSql.Run(conn, "DROP TABLE #ap_PurBillVouchHXdata");
        }

        static string Cols(string sql, string a, string b)
        {
            return sql.Replace("{a}", WriteoffSql.Dec(a, 2)).Replace("{b}", WriteoffSql.Dec(b, 2));
        }

        // ids 为 null：读临时表合计（id → [a, b]）；否则逐行读累计值。
        static Dictionary<int, decimal[]> Read(object conn, string sql, IEnumerable<int> ids)
        {
            Dictionary<int, decimal[]> map = new Dictionary<int, decimal[]>();
            if (ids == null)
            {
                foreach (Dictionary<string, object> row in Rows.Query(conn, sql, new object[0], 1001))
                {
                    map[CoRows.AsId(CoRows.Col(row, "id"))] = Pair(row);
                }
                return map;
            }
            foreach (int id in ids)
            {
                map[id] = Pair(Rows.One(conn, sql, new object[] { id }));
            }
            return map;
        }

        static decimal[] Pair(Dictionary<string, object> row)
        {
            return new decimal[] { WriteoffSql.Num(CoRows.Col(row, "a")), WriteoffSql.Num(CoRows.Col(row, "b")) };
        }

        static void Check(object conn, string sql, Dictionary<int, decimal[]> sums, Dictionary<int, decimal[]> before, string title)
        {
            foreach (KeyValuePair<int, decimal[]> pair in sums)
            {
                decimal[] now = Pair(Rows.One(conn, sql, new object[] { pair.Key }));
                decimal[] was = before[pair.Key];
                for (int i = 0; i < 2; i++)
                {
                    if (Math.Abs(now[i] - (was[i] + pair.Value[i])) > Tolerance)
                    {
                        throw new BridgeException(409, "u8_rejected", title + "行 " + pair.Key + " 的累计核销不符");
                    }
                }
            }
        }

        // 类型库：BSTR UpdateBillForAR(ByRef CN As Connection, ByRef strTblName As String)，没有 Init；引用 {0,1}。
        // 返回空串为成功，非空是 U8 的错误说明（实测）。
        // 组件不开自己的事务，在桥的事务里；调用前后 @@TRANCOUNT 变了就说不清写没写进去，504。
        // what：504 消息里让调用方核对的对象（「该核销号」或「处理号 …」）。
        internal static void Write2(WorkContext ctx, string what)
        {
            object w = null;
            try
            {
                w = ComUtil.Create(Write2Bill);
                if (w == null)
                {
                    throw new BridgeException(503, "com_unavailable", "组件无法创建 " + Write2Bill);
                }
                string tran = CoTrans.Count(ctx.Conn);
                object[] args = new object[] { ctx.Conn, SaleTable };
                Refused(ComUtil.CallRef(w, "UpdateBillForAR", args, new int[] { 0, 1 }));
                CoRows.Note(ctx.Item, "clsWrite2Bill.UpdateBillForAR @@TRANCOUNT " + tran + " → " + CoTrans.Count(ctx.Conn));
                if (CoTrans.Count(ctx.Conn) != tran)
                {
                    throw new BridgeException(504, "outcome_unknown", "U8 回写销售发票的组件改变了事务，结果未知，请核对" + what);
                }
            }
            catch (COMException ex)
            {
                CoRows.Note(ctx.Item, "clsWrite2Bill " + ex.Message);
                throw new BridgeException(409, "u8_rejected", ArapCo.Said(ex.Message, "U8 回写销售发票失败"));
            }
            finally
            {
                ComUtil.Final(w);
            }
        }

        // 返回非空串即 U8 拒绝，原文带回。
        static void Refused(object ret)
        {
            string text = ret == null ? "" : Convert.ToString(ret, System.Globalization.CultureInfo.InvariantCulture).Trim();
            if (text.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 回写销售发票失败：" + text);
            }
        }
    }
}
