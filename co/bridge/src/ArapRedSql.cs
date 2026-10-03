using System.Collections.Generic;

namespace U8Co
{
    // 红票对冲的查询（往来明细表按 flag 取，WriteoffSql.Detail）；调用方的值只进参数。
    // 另有取消红票对冲（arap/process/cancel 的 9N）时发票累计的取数，交给 ArapUnwriteoffBill 的临时表（U8 的 9P 公式不适用：
    // 9N 建立时 U8 给蓝字发票行加了本次、红字发票行减了本次，取消要按处理行的带符号金额原样加回，见 ArapRedRule）。
    internal static class ArapRedSql
    {
        // 单据各行（iBVid；应收应付单为 0）带符号的余额：应收 借-贷、应付 贷-借，口径同核销（iFlag<3）。参数 (flag, 类型, 单号, 往来单位)。
        const string SlotSql = "select convert(varchar(20), isnull(d.iBVid,0)) as line, {f} as f, {n} as n from {D} d "
            + "where d.cFlag=? and d.cCoVouchType=? and d.cCoVouchID=? and d.cDwCode=? and d.iFlag<3 "
            + "group by isnull(d.iBVid,0) having sum({of})<>0";

        // 审核行（处理行由 U8 从它复制）：符号、部门、业务员、存货（进报文）、合同。条件同转账（TransferSql.SignWhere，别名 s）。
        const string SignSql = "select top 1 convert(varchar(20), s.Auto_ID) as auto, isnull(s.cSign,N'') as csign, "
            + "isnull(s.cDeptCode,N'') as dept, isnull(s.cPerson,N'') as person, isnull(s.cInvCode,N'') as inv, "
            + "isnull(s.cContractID,N'') as contract from {D} s where {W} order by s.Auto_ID";

        // 某处理号的 9N 行按单据（对方单据即自身）和行汇总的带符号金额；另数出不是自身单据的行（形状不符）。参数 (处理号, flag)。
        const string RowsSql = "select d.cCoVouchType as t, d.cCoVouchID as c, convert(varchar(20), isnull(d.iBVid,0)) as line, "
            + "{f} as f, {n} as n, convert(varchar(12), sum(case when d.cVouchType=d.cCoVouchType and d.cVouchID=d.cCoVouchID "
            + "then 0 else 1 end)) as odd from {D} d where d.cProcStyle=N'9N' and d.cCancelNo=? and d.cFlag=? "
            + "group by d.cCoVouchType, d.cCoVouchID, isnull(d.iBVid,0)";

        // 取消 9N 时销售发票行的累计加回：处理行的 借+贷（原币、本币）原样加到 iExchSum / iMoneySum（建立时 U8 加的是它的相反数）。
        // 参数 (处理方式, 处理号)，与 ArapUnwriteoffBill 的 SaleFill 相同。
        internal const string CancelSaleFill = "INSERT INTO #ap_SaleBillVouchHXdata (autoid,iexchsum,imoneysum) SELECT d.iBVid, "
            + "isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0), isnull(d.iDAmount,0)+isnull(d.iCAmount,0) FROM Ar_Detail d "
            + "WHERE d.cProcStyle=? AND d.cCancelNo=? AND d.cFlag=N'AR' AND d.cCoVouchType LIKE '2%' AND isnull(d.iBVid,0)<>0";

        // 采购发票同上，加到 iOriTotal / iTotal（未覆盖：应付 9N 建立时 U8 是否同样回写 PurBillVouchs，建立的事务内核对会证明）。
        internal const string CancelPurFill = "INSERT INTO #ap_PurBillVouchHXdata (id,iOriTotal,iTotal) SELECT d.iBVid, "
            + "isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0), isnull(d.iDAmount,0)+isnull(d.iCAmount,0) FROM Ap_Detail d "
            + "WHERE d.cProcStyle=? AND d.cCancelNo=? AND d.cFlag=N'AP' AND d.cCoVouchType LIKE '0%' AND isnull(d.iBVid,0)<>0";

        static string Open(string flag, string suffix)
        {
            string d = "isnull(d.iDAmount" + suffix + ",0)";
            string c = "isnull(d.iCAmount" + suffix + ",0)";
            return flag == "AP" ? c + "-" + d : d + "-" + c;
        }

        internal static string SlotText(string flag)
        {
            return SlotSql.Replace("{D}", WriteoffSql.Detail(flag)).Replace("{f}", WriteoffSql.Dec("sum(" + Open(flag, "_f") + ")", 2))
                .Replace("{n}", WriteoffSql.Dec("sum(" + Open(flag, "") + ")", 2)).Replace("{of}", Open(flag, "_f"));
        }

        internal static string RowsText(string flag)
        {
            return RowsSql.Replace("{D}", WriteoffSql.Detail(flag)).Replace("{f}", WriteoffSql.Dec("sum(" + Open(flag, "_f") + ")", 2))
                .Replace("{n}", WriteoffSql.Dec("sum(" + Open(flag, "") + ")", 2));
        }

        public static List<RedSlot> Slots(object conn, TransferDoc d, string dw)
        {
            List<RedSlot> slots = new List<RedSlot>();
            object[] args = new object[] { d.Side, d.Ask.Type, d.Ask.Code, dw };
            foreach (Dictionary<string, object> row in Rows.Query(conn, SlotText(d.Side), args, 1001))
            {
                RedSlot s = new RedSlot();
                s.Line = CoRows.AsId(CoRows.Col(row, "line"));
                s.SignedF = WriteoffSql.Num(CoRows.Col(row, "f"));
                s.SignedN = WriteoffSql.Num(CoRows.Col(row, "n"));
                slots.Add(s);
            }
            return slots;
        }

        public static Dictionary<string, object> SignRow(object conn, TransferDoc d, int line, string dw)
        {
            List<object> args = new List<object>();
            string where = TransferSql.SignWhere(d, line, dw, args);
            string sql = SignSql.Replace("{D}", WriteoffSql.Detail(d.Side)).Replace("{W}", where);
            return Rows.One(conn, sql, args.ToArray());
        }

        // 该处理号的 9N 行：键（类型|单号|行）→ [原币, 本币] 带符号金额。odd 返回不是自身单据的行数。
        public static Dictionary<string, decimal[]> Written(object conn, string flag, string cancelNo, out int odd)
        {
            Dictionary<string, decimal[]> map = new Dictionary<string, decimal[]>(System.StringComparer.Ordinal);
            odd = 0;
            foreach (Dictionary<string, object> row in Rows.Query(conn, RowsText(flag), new object[] { cancelNo, flag }, 1001))
            {
                string key = ArapRedRule.Key(CoRows.Col(row, "t"), CoRows.Col(row, "c"), CoRows.AsId(CoRows.Col(row, "line")));
                map[key] = new decimal[] { WriteoffSql.Num(CoRows.Col(row, "f")), WriteoffSql.Num(CoRows.Col(row, "n")) };
                odd += CoRows.AsId(CoRows.Col(row, "odd"));
            }
            return map;
        }
    }
}
