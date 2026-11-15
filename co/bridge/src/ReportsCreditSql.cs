namespace U8Co
{
    // 客户信用的 SQL（ReportsCredit 用）。各项照 U8 信用余额表的存储过程（Sa_saleCreReport 及其调用的
    // CreditSoForReport、CreditDLForReport、CreditBillForReport 和视图 Ap_CreditDetail）改写成一条查询：
    // opt.sv = 1 是信用检查点「保存」（含未审核单据），0 是「审核」（只含已审核，改用 U8 的已审核累计列）。
    // 订单一项用的是「信用余额控制用余额表」（SA bUseBanlaceTable）打开时的口径；关闭时 U8 另有一套算法（未经实测）。
    internal static class ReportsCreditSql
    {
        // {FILTER}：客户编码、只看受控客户、游标、数据权限。参数：检查点 | limit+1 | 过滤…
        internal const string Main = ";WITH opt AS (SELECT CONVERT(int, ?) sv),"
            + " pg AS (SELECT TOP (?) c.cCusCode FROM Customer c WHERE 1=1{FILTER} ORDER BY c.cCusCode),"
            + Orders + Dispatches + Bills + Receivables + Expenses
            + " SELECT c.cCusCode code, c.cCusName name, c.bCredit ctl, CONVERT(decimal(18,2), c.iCusCreLine) line,"
            + " c.iCusCreDate days, c.bCreditDate ctl_days, c.cCusCreGrade grade, c.cCusCreditCompany company,"
            + " CONVERT(decimal(18,2), ISNULL(so.amt, 0)) so_amt, CONVERT(decimal(18,2), ISNULL(dl.amt, 0)) dl_amt,"
            + " CONVERT(decimal(18,2), ISNULL(bl.amt, 0)) bl_amt, CONVERT(decimal(18,2), ISNULL(ar.amt, 0)) ar_amt,"
            + " CONVERT(decimal(18,2), ISNULL(ex.amt, 0)) ex_amt"
            + " FROM pg JOIN Customer c ON c.cCusCode=pg.cCusCode LEFT JOIN so ON so.cus=c.cCusCode"
            + " LEFT JOIN dl ON dl.cus=c.cCusCode LEFT JOIN bl ON bl.cus=c.cCusCode LEFT JOIN ar ON ar.cus=c.cCusCode"
            + " LEFT JOIN ex ON ex.cus=c.cCusCode ORDER BY c.cCusCode";

        // 未执行完的销售订单：未关闭行按未发货（直运销售按未开票）数量折算价税合计；数量为 0 的行按金额。
        const string Orders = " so AS (SELECT a.cCusCode cus, SUM(CASE WHEN ISNULL(b.iQuantity, 0)=0 THEN"
            + " CASE WHEN SIGN(ISNULL(b.iNatSum, 0))<>SIGN(ISNULL(x.sm, 0)) THEN ISNULL(b.iNatSum, 0)"
            + " WHEN o.sv=1 THEN ISNULL(b.iNatSum, 0)-ISNULL(x.sm, 0)*(ISNULL(b.iNatSum, 0)/b.iSum)"
            + " WHEN ABS(ISNULL(b.iNatSum, 0))-ABS(ISNULL(x.sm, 0))>=0 THEN ISNULL(b.iNatSum, 0)-ISNULL(x.sm, 0) ELSE 0 END"
            + " ELSE CASE WHEN SIGN(b.iQuantity)<>SIGN(ISNULL(x.sq, 0)) THEN ISNULL(b.iNatSum, 0)"
            + " WHEN b.iQuantity-ISNULL(x.sq, 0)<=0 THEN 0"
            + " ELSE CAST(ISNULL(b.iNatSum, 0)/b.iQuantity*(b.iQuantity-ISNULL(x.sq, 0)) AS decimal(26,2)) END END) amt"
            + " FROM SO_SOMain a JOIN SO_SODetails b ON b.ID=a.ID CROSS JOIN opt o"
            + " CROSS APPLY (SELECT CASE WHEN o.sv=1 THEN CASE WHEN a.cBusType=N'直运销售' THEN b.iKPQuantity"
            + " ELSE b.iFHQuantity END ELSE b.fVeriDispQty END sq, CASE WHEN o.sv=1 THEN CASE WHEN a.cBusType=N'直运销售'"
            + " THEN b.iKPMoney ELSE b.iFHMoney END ELSE b.fVeriDispSum END sm) x"
            + " WHERE a.cCusCode IN (SELECT cCusCode FROM pg) AND ISNULL(b.iSum, 0)<>0 AND ISNULL(a.bcashsale, 0)=0"
            + " AND ISNULL(b.cSCloser, N'')=N'' AND (o.sv=1 OR ISNULL(a.cVerifier, N'')<>N'') GROUP BY a.cCusCode),";

        // 未开票的发货单：需开票、未结算完的行，扣掉已开票和退货。
        const string Dispatches = " dl AS (SELECT a.cCusCode cus, SUM(CASE WHEN x.qn<>0 THEN"
            + " CASE WHEN SIGN(x.qn)<>SIGN(ISNULL(x.bq, 0)) AND SIGN(ISNULL(x.bq, 0))<>0 THEN ISNULL(b.iNatSum, 0)"
            + " ELSE CAST((CASE WHEN ABS(x.qn)-ABS(ISNULL(x.rq, 0))-ABS(ISNULL(b.frettbquantity, 0))-ABS(ISNULL(x.bq, 0))>0"
            + " THEN x.qn-ISNULL(x.rq, 0)-ISNULL(b.frettbquantity, 0)-ISNULL(x.bq, 0) ELSE 0 END)"
            + "*(ISNULL(b.iNatSum, 0)/x.qn) AS decimal(26,2)) END"
            + " ELSE CASE WHEN SIGN(ISNULL(b.iNatSum, 0))<>SIGN(ISNULL(x.bm, 0)) AND SIGN(ISNULL(x.bm, 0))<>0 THEN ISNULL(b.iNatSum, 0)"
            + " WHEN o.sv=1 THEN CASE WHEN ABS(b.iNatSum)-(ABS(ISNULL(b.fretsum, 0))+ABS(ISNULL(b.iSettleNum, 0))*b.iNatSum/b.iSum)>0"
            + " THEN b.iNatSum-(ISNULL(b.fretsum, 0)+ISNULL(b.iSettleNum, 0))*b.iNatSum/b.iSum ELSE 0 END"
            + " WHEN ABS(b.iNatSum)-ABS(ISNULL(b.fVeriRetSum, 0))-ABS(ISNULL(b.fVeriBillSum, 0))>0"
            + " THEN b.iNatSum-ISNULL(b.fVeriRetSum, 0)-ISNULL(b.fVeriBillSum, 0) ELSE 0 END END) amt"
            + " FROM DispatchList a JOIN DispatchLists b ON b.DLID=a.DLID CROSS JOIN opt o"
            + " CROSS APPLY (SELECT ISNULL(b.iQuantity, 0)+ISNULL(b.TBQuantity, 0) qn,"
            + " CASE WHEN o.sv=1 THEN b.iSettleQuantity ELSE b.fVeriBillQty END bq,"
            + " CASE WHEN o.sv=1 THEN b.fretqtywkp ELSE b.fVeriRetQty END rq,"
            + " CASE WHEN o.sv=1 THEN b.iSettleNum ELSE b.fVeriBillSum END bm) x"
            + " WHERE a.cCusCode IN (SELECT cCusCode FROM pg) AND (ISNULL(a.bFirst, 0)=1 OR a.dDate>=(SELECT"
            + " ISNULL(MAX(CASE WHEN ISDATE(s.cValue)=1 THEN CONVERT(datetime, s.cValue) END), '19000101')"
            + " FROM AccInformation s WHERE s.cSysID=N'SA' AND s.cName=N'dStartDate'))"
            + " AND a.cVouchType<>N'00' AND ISNULL(a.bcashsale, 0)=0 AND ISNULL(a.bneedbill, 0)=1"
            + " AND ISNULL(b.bSettleAll, 0)=0 AND ISNULL(b.iSum, 0)<>0 AND ISNULL(a.iSale, 0)=0"
            + " AND (o.sv=1 OR ISNULL(a.cVerifier, N'')<>N'') GROUP BY a.cCusCode),";

        // 销售已开、应收未审核的发票（扣现结）。
        const string Bills = " bl AS (SELECT a.cCusCode cus, SUM(ISNULL(b.iNatSum, 0)-ISNULL(b.iMoneySum, 0)) amt"
            + " FROM SaleBillVouch a JOIN SaleBillVouchs b ON b.SBVID=a.SBVID CROSS JOIN opt o"
            + " WHERE a.cCusCode IN (SELECT cCusCode FROM pg) AND ISNULL(a.bcashsale, 0)=0 AND ISNULL(a.cVerifier, N'')=N''"
            + " AND a.cSource=N'销售' AND ISNULL(a.cInvalider, N'')=N'' AND (o.sv=1 OR ISNULL(a.cChecker, N'')<>N'')"
            + " GROUP BY a.cCusCode),";

        // 应收账款余额：U8 自己的视图 Ap_CreditDetail（应收明细，检查点为保存时加未审核的应收单和收款单）。
        const string Receivables = " ar AS (SELECT d.cCusCode cus, SUM(d.Exsum) amt FROM Ap_CreditDetail d CROSS JOIN opt o"
            + " WHERE d.cCusCode IN (SELECT cCusCode FROM pg) AND (o.sv=1 OR d.bverify=1) GROUP BY d.cCusCode),";

        // 代垫费用单：保存点取未审核的；审核点取已审核、应收未审核的。
        const string Expenses = " ex AS (SELECT a.cCusCode cus, SUM(ISNULL(b.INatMoney, 0)) amt FROM ExpenseVouch a"
            + " JOIN ExpenseVouchs b ON b.ID=a.ID CROSS JOIN opt o"
            + " LEFT JOIN Ap_Vouch v ON v.cVouchType=a.cVouchType AND v.cVouchID=a.cVouchID"
            + " WHERE a.cCusCode IN (SELECT cCusCode FROM pg) AND ((o.sv=1 AND ISNULL(a.cVerifier, N'')=N'')"
            + " OR (o.sv=0 AND ISNULL(a.cVerifier, N'')<>N'' AND ISNULL(v.cCheckMan, N'')=N'')) GROUP BY a.cCusCode)";

        // 销售选项（AccInformation SA）与应收是否启用。
        internal const string Options = "SELECT MAX(CASE WHEN cSysID=N'SA' AND cName=N'bCredit' THEN cValue END) credit,"
            + " MAX(CASE WHEN cSysID=N'SA' AND cName=N'cCrCheckFunction' THEN cValue END) formula,"
            + " MAX(CASE WHEN cSysID=N'SA' AND cName=N'bCrCheckWhen' THEN cValue END) point,"
            + " MAX(CASE WHEN cSysID=N'SA' AND cName=N'bUseBanlaceTable' THEN cValue END) bal,"
            + " MAX(CASE WHEN cSysID=N'AR' AND cName=N'dARStartDate' THEN cValue END) ar"
            + " FROM AccInformation WHERE cSysID IN (N'SA', N'AR')";
    }
}
