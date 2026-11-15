namespace U8Co
{
    // ListKinds 的其他报检单（QM11）、其他检验单（QM15）。列顺序同 ListKinds.cs 的说明。
    // 两类都没有来源单据、没有审批流（检验单 IsWfControlled 恒为 0），不带 wf / wf_state / current_auditor。
    // 报检单附加列同产品报检单（来源三列恒为 null）；表体有 UFTS，变更按表头、表体较大的算。
    // 检验单另给对应报检单的单号、ID；部门是检验部门（CDEPCODE），报检部门在附加列。
    internal static partial class ListKinds
    {
        const string QmoChkExtra = "h.CSOURCE AS source, h.CINSPECTCODE AS inspect_code, h.INSPECTID AS inspect_id, "
            + "h.CINSPECTDEPCODE AS inspect_dep_code, h.CCHECKTYPECODE AS check_type, "
            + "h.DMAKETIME AS created_at, h.DMODIFYTIME AS modified_at";

        static string[][] QmOtherRows()
        {
            return new string[][]
            {
                new string[] { "qm_other_inspect", "QMINSPECTVOUCHER", "ID", "h.CINSPECTCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", null, "h.CDEPCODE", null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", "QMINSPECTVOUCHERS", "ID", "UFTS", "h.CVOUCHTYPE = N'QM11'", QmiProExtra, null },
                new string[] { "qm_other_check", "QMCHECKVOUCHER", "ID", "h.CCHECKCODE", "h.DDATE",
                    "h.CCUSCODE", "h.CVENCODE", "h.CWHCODE", "h.CDEPCODE", null, "h.CMAKER",
                    "h.CVERIFIER", "h.DVERIFYDATE", null, null, null, null,
                    "UFTS", null, null, null, "h.CVOUCHTYPE = N'QM15'", QmoChkExtra, null }
            };
        }
    }
}
