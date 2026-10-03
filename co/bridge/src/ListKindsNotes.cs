namespace U8Co
{
    // 应收票据 / 应付票据（AP_Note，cFlag 分 AR / AP）：只读列表，供 vouchers/list、vouchers/search 与事件服务增量轮询。
    // 不是 VoucherKind：不能经 vouchers/load 和写路由，单张读取走 notes/get（NotesRead）。列顺序同 ListKinds.cs 的说明。
    // 往来单位 cDwCode 在测试账套上多为空，单位名称在 dw_name；登记人 cOperator 只有期初票据才填，其余取 cBill（登记人姓名）。
    // 票据没有审核，verified 恒为 0；余额 iRAmount 为 0（已结算、贴现、背书或退回完）时 closed 为 1。表体 AP_Note_Sub 没有 rowversion，
    // 处理（9A / 9C / 9D / 9E）回写表头余额时表头 Ufts 随之变化。
    internal static partial class ListKinds
    {
        const string NoteMaker = "ISNULL(NULLIF(h.cOperator, N''), h.cBill)";
        const string NoteClosed = "CASE WHEN ISNULL(h.iRAmount, 0) = 0 THEN 1 ELSE 0 END";
        const string NoteExtra = "h.cSettleCode AS settle_code, h.iAmount AS amount, h.iRAmount AS remainder, "
            + "h.iCloseID AS close_id, h.bStartFlag AS opening, h.dExpireDate AS expire_date, h.cDWName AS dw_name, "
            + "h.cexch_name AS currency";

        static string[][] NoteRows()
        {
            return new string[][]
            {
                Note("ar_note", "h.cDwCode", null, "h.cFlag = N'AR'"),
                Note("ap_note", null, "h.cDwCode", "h.cFlag = N'AP'")
            };
        }

        static string[] Note(string name, string cus, string ven, string cond)
        {
            return new string[] { name, "AP_Note", "Auto_ID", "h.cVouchID", "h.dSignDate",
                cus, ven, null, "h.cDeptCode", "h.cPerson", NoteMaker,
                null, null, null, NoteClosed, "0", null,
                "Ufts", null, null, null, cond, NoteExtra, null };
        }
    }
}
