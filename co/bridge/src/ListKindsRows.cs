namespace U8Co
{
    // ListKinds 的库存、生产、质检、应收应付各行。列顺序同 ListKinds.cs 的说明。
    internal static partial class ListKinds
    {
        const string RdExtra = "h.cRdCode AS rd_code, h.cBusType AS bus_type, h.cSource AS source, "
            + "h.cOrderCode AS order_code, h.cDLCode AS dl_ref, h.dnmaketime AS created_at, "
            + "h.dnmodifytime AS modified_at";
        const string TvExtra = "h.cIWhCode AS in_wh_code, h.cIDepCode AS in_dep_code, h.cORdCode AS out_rd_code, "
            + "h.cIRdCode AS in_rd_code, h.csource AS source, h.dnmaketime AS created_at, "
            + "h.dnmodifytime AS modified_at";

        static string[][] StRows()
        {
            return new string[][]
            {
                new string[] { "purchase_in", "RdRecord01", "ID", "h.cCode", "h.dDate",
                    "h.cCusCode", "h.cVenCode", "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cHandler", "h.dVeriDate", null, null, null, "h.bredvouch",
                    "ufts", "rdrecords01", "ID", "rowufts", null, RdExtra, null },
                new string[] { "other_in", "RdRecord08", "ID", "h.cCode", "h.dDate",
                    "h.cCusCode", "h.cVenCode", "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cHandler", "h.dVeriDate", null, null, null, "h.bredvouch",
                    "ufts", "rdrecords08", "ID", "rowufts", null, RdExtra, null },
                new string[] { "other_out", "RdRecord09", "ID", "h.cCode", "h.dDate",
                    "h.cCusCode", "h.cVenCode", "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cHandler", "h.dVeriDate", null, null, null, "h.bredvouch",
                    "ufts", "rdrecords09", "ID", "rowufts", null, RdExtra, null },
                new string[] { "product_in", "rdrecord10", "ID", "h.cCode", "h.dDate",
                    "h.cCusCode", "h.cVenCode", "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cHandler", "h.dVeriDate", null, null, null, "h.bredvouch",
                    "ufts", "rdrecords10", "ID", "rowufts", null, RdExtra, null },
                new string[] { "material_out", "rdrecord11", "ID", "h.cCode", "h.dDate",
                    "h.cCusCode", "h.cVenCode", "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cHandler", "h.dVeriDate", null, null, null, "h.bredvouch",
                    "ufts", "rdrecords11", "ID", "rowufts", null, RdExtra, null },
                new string[] { "sale_out", "rdrecord32", "ID", "h.cCode", "h.dDate",
                    "h.cCusCode", "h.cVenCode", "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cHandler", "h.dVeriDate", null, null, null, "h.bredvouch",
                    "ufts", "rdrecords32", "ID", "rowufts", null, RdExtra, null },
                // 调拨：wh / dep 是调出方，调入方在附加列。
                new string[] { "transfer", "TransVouch", "ID", "h.cTVCode", "h.dTVDate",
                    null, null, "h.cOWhCode", "h.cODepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifyPerson", "h.dVerifyDate", null, null, null, null,
                    "ufts", null, null, null, null, TvExtra, null }
            };
        }

        // 生产订单的审核、关闭都在行上（同 SqlRead.MoState）：全部行已下达为已审核，全部行 Status=4 为已关闭。
        const string MoApply = " OUTER APPLY (SELECT COUNT(d.MoDId) AS n, "
            + "SUM(CASE WHEN d.Status IN (3, 4) AND NULLIF(LTRIM(RTRIM(d.RelsUser)), N'') IS NOT NULL "
            + "THEN 1 ELSE 0 END) AS rel, SUM(CASE WHEN d.Status = 4 THEN 1 ELSE 0 END) AS cl, "
            + "MAX(d.RelsUser) AS verifier, MAX(d.RelsDate) AS rels_date, MAX(d.Ufts) AS bu "
            + "FROM mom_orderdetail d WHERE d.MoId = h.MoId) b";
        const string MoVerified = "CASE WHEN b.n > 0 AND b.rel = b.n THEN 1 ELSE 0 END";
        const string MoClosed = "CASE WHEN b.n > 0 AND b.cl = b.n THEN 1 ELSE 0 END";
        const string MoExtra = "h.CreateTime AS created_at, h.ModifyTime AS modified_at";
        // 审批流状态（iVerifyStateNew：0 未提交、1 审批中、2 通过、-1 不通过）和当前审核人（姓名），事件服务靠它发 workflow。
        const string QmWf = ", h.iVerifyStateNew AS wf_state, h.cCurrentAuditor AS current_auditor";
        const string QmcExtra = "h.CSOURCE AS source, h.CPOCODE AS po_code, h.IsWfControlled AS wf, "
            + "h.DMAKETIME AS created_at, h.DMODIFYTIME AS modified_at" + QmWf;
        const string QmpExtra = "h.CSOURCE AS source, h.IsWfControlled AS wf, "
            + "h.DMAKETIME AS created_at, h.DMODIFYTIME AS modified_at" + QmWf;
        const string QmrExtra = "h.CSOURCE AS source, h.CCHECKCODE AS check_code, h.IsWfControlled AS wf" + QmWf;

        // 报检单（QM01 / QM02）：没有审批流，不带 wf / wf_state / current_auditor。部门是业务部门（CDEPCODE），
        // 报检部门在附加列；仓库、存货在表体，列表不给。表体有 UFTS，变更按表头、表体较大的算。
        const string QmiSrc = "h.CSOURCE AS source, h.CSOURCECODE AS source_code, h.CSOURCEID AS source_id, ";
        const string QmiTail = "h.CINSPECTDEPCODE AS inspect_dep_code, h.CCHECKTYPECODE AS check_type, "
            + "h.DMAKETIME AS created_at, h.DMODIFYTIME AS modified_at";
        const string QmiInExtra = QmiSrc + "h.DARRIVALDATE AS arrival_date, " + QmiTail;
        const string QmiProExtra = QmiSrc + QmiTail;

        static string[][] MoQmRows()
        {
            return new string[][]
            {
                new string[] { "production_order", "mom_order", "MoId", "h.MoCode", "h.CreateDate",
                    null, null, null, null, null, "h.CreateUser",
                    "b.verifier", "b.rels_date", null, MoClosed, MoVerified, null,
                    "Ufts", "mom_orderdetail", "MoId", "Ufts", null, MoExtra, MoApply },
                new string[] { "qm_incoming_inspect", "QMINSPECTVOUCHER", "ID", "h.CINSPECTCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", null, "h.CDEPCODE", null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", "QMINSPECTVOUCHERS", "ID", "UFTS", "h.CVOUCHTYPE = N'QM01'", QmiInExtra, null },
                new string[] { "qm_product_inspect", "QMINSPECTVOUCHER", "ID", "h.CINSPECTCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", null, "h.CDEPCODE", null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", "QMINSPECTVOUCHERS", "ID", "UFTS", "h.CVOUCHTYPE = N'QM02'", QmiProExtra, null },
                new string[] { "qm_incoming_check", "QMCHECKVOUCHER", "ID", "h.CCHECKCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", "h.CWHCODE", "h.CDEPCODE", null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", null, null, null, "h.CVOUCHTYPE = N'QM03'", QmcExtra, null },
                new string[] { "qm_product_check", "QMCHECKVOUCHER", "ID", "h.CCHECKCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", "h.CWHCODE", "h.CDEPCODE", null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", null, null, null, "h.CVOUCHTYPE = N'QM04'", QmpExtra, null },
                new string[] { "qm_incoming_reject", "QMREJECTVOUCHER", "ID", "h.CREJECTCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", "h.CWHCODE", null, null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", null, null, null, "h.CVOUCHTYPE = N'QM05'", QmrExtra, null },
                new string[] { "qm_product_reject", "QMREJECTVOUCHER", "ID", "h.CREJECTCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", "h.CWHCODE", null, null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", null, null, null, "h.CVOUCHTYPE = N'QM06'", QmrExtra, null }
            };
        }

        // 收付款单、应收应付单：cDwCode 按 cFlag 是客户或供应商。表体没有 rowversion。
        // 不选 cBankAccount / cNatBankAccount（银行账号）。
        const string CbExtra = "h.cVouchType AS vouch_type, h.cFlag AS ledger, h.iAmount AS amount, "
            + "h.iAmount_f AS amount_fc, h.cexch_name AS currency, h.iExchRate AS rate, h.cSSCode AS settle_code, "
            + "h.cCode AS km_code, h.cDigest AS digest, h.cPzID AS gl_ref, h.cCoVouchType AS source_type, "
            + "h.bPrePay AS prepay, h.IsWfControlled AS wf, h.dcreatesystime AS created_at, "
            + "h.dmodifysystime AS modified_at";
        const string ApvExtra = "h.cVouchType AS vouch_type, h.cFlag AS ledger, h.cLink AS link, "
            + "h.iAmount AS amount, h.iAmount_f AS amount_fc, h.cexch_name AS currency, h.iExchRate AS rate, "
            + "h.cCode AS km_code, h.cDigest AS digest, h.cPZid AS gl_ref, h.cCoVouchType AS source_type, "
            + "h.bd_c AS debit_side, h.dcreatesystime AS created_at, h.dmodifysystime AS modified_at";

        static string[][] ArRows()
        {
            return new string[][]
            {
                new string[] { "ar_receipt", "Ap_CloseBill", "iID", "h.cVouchID", "h.dVouchDate",
                    "h.cDwCode", null, null, "h.cDeptCode", "h.cPerson", "h.cOperator",
                    "h.cCheckMan", "h.dverifydate", null, null, null, null,
                    "Ufts", null, null, null, "h.cFlag = N'AR' AND h.cVouchType = N'48'", CbExtra, null },
                new string[] { "ap_payment", "Ap_CloseBill", "iID", "h.cVouchID", "h.dVouchDate",
                    null, "h.cDwCode", null, "h.cDeptCode", "h.cPerson", "h.cOperator",
                    "h.cCheckMan", "h.dverifydate", null, null, null, null,
                    "Ufts", null, null, null, "h.cFlag = N'AP' AND h.cVouchType = N'49'", CbExtra, null },
                new string[] { "ar_bill", "Ap_Vouch", "Auto_ID", "h.cVouchID", "h.dVouchDate",
                    "h.cDwCode", null, null, "h.cDeptCode", "h.cPerson", "h.cOperator",
                    "h.cCheckMan", "h.dverifydate", null, null, null, null,
                    "Ufts", null, null, null, "h.cFlag = N'AR' AND h.cVouchType = N'R0'", ApvExtra, null },
                new string[] { "ap_bill", "Ap_Vouch", "Auto_ID", "h.cVouchID", "h.dVouchDate",
                    null, "h.cDwCode", null, "h.cDeptCode", "h.cPerson", "h.cOperator",
                    "h.cCheckMan", "h.dverifydate", null, null, null, null,
                    "Ufts", null, null, null, "h.cFlag = N'AP' AND h.cVouchType = N'P0'", ApvExtra, null },
                // 供应商退款（AP48）、客户退款（AR49）：同收付款单，往来单位仍按 cFlag 取供应商 / 客户。
                new string[] { "ap_refund", "Ap_CloseBill", "iID", "h.cVouchID", "h.dVouchDate",
                    null, "h.cDwCode", null, "h.cDeptCode", "h.cPerson", "h.cOperator",
                    "h.cCheckMan", "h.dverifydate", null, null, null, null,
                    "Ufts", null, null, null, "h.cFlag = N'AP' AND h.cVouchType = N'48'", CbExtra, null },
                new string[] { "ar_refund", "Ap_CloseBill", "iID", "h.cVouchID", "h.dVouchDate",
                    "h.cDwCode", null, null, "h.cDeptCode", "h.cPerson", "h.cOperator",
                    "h.cCheckMan", "h.dverifydate", null, null, null, null,
                    "Ufts", null, null, null, "h.cFlag = N'AR' AND h.cVouchType = N'49'", CbExtra, null }
            };
        }
    }
}
