namespace U8Co
{
    // 质量单据修改的闸门（全部在 U8 调用之前）。U8 自己不拦：已审核的其他报检单照样能改（也不盖修改人），
    // 所以下面每一条都由桥判断，不自动弃审（弃审是另一项状态变化，调用方要自己先做）。
    // 检验单（QM03 / QM04 / QM15）：受审批流控制且已提交或已审核、未受控却在途 409 workflow_enabled（先在 workflow/* 撤回或弃审）；
    // 已审核 409（其他检验单可经 vouchers/verify 弃审）；已入库（FSUMQUANTITY、各类入库行的 iCheckIdBaks）、已生成不良品处理单
    // （BREJFLAG、QMREJECTVOUCHER.CHECKID）409。其他报检单（QM11）：已审核 409（先弃审）；已有其他检验单 409。
    internal static class QmEditGate
    {
        const string DownSql = "select top 1 x.k from (select 'in' as k from rdrecords01 where iCheckIdBaks=?"
            + " union all select 'in' as k from rdrecords08 where iCheckIdBaks=?"
            + " union all select 'in' as k from rdrecords09 where iCheckIdBaks=?"
            + " union all select 'in' as k from rdrecords10 where iCheckIdBaks=?"
            + " union all select 'reject' as k from QMREJECTVOUCHER where CHECKID=?) x";

        public static void Check(object conn, QmEditJob job, int id)
        {
            RequireOpen(job);
            if (QmSql.Dec(CoRows.Col(job.Doc, "FSUMQUANTITY")) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", job.Title + "已入库，不能修改");
            }
            if (CoRows.Col(job.Doc, "BREJFLAG") == "1")
            {
                throw new BridgeException(409, "state_mismatch", job.Title + "已生成不良品处理单，不能修改");
            }
            string down = QmSql.Scalar(conn, DownSql, id, id, id, id, id);
            if (down.Length > 0)
            {
                throw new BridgeException(409, "state_mismatch",
                    job.Title + (down == "reject" ? "已生成不良品处理单" : "已被入库单引用") + "，不能修改");
            }
        }

        // 审批流与审核状态：受控且已提交（iVerifyStateNew 不为 0）或已审核，或未受控却在途，都按审批流处理。
        static void RequireOpen(QmEditJob job)
        {
            bool wf = Flag(CoRows.Col(job.Doc, "IsWfControlled"));
            bool moving = QmSql.Dec(CoRows.Col(job.Doc, "iVerifyStateNew")) != 0m;
            bool verified = CoRows.Col(job.Doc, "CVERIFIER").Length > 0;
            if (moving || (wf && verified))
            {
                throw new BridgeException(409, "workflow_enabled",
                    job.Title + "已提交审批或已审核，请先在审批流中撤回或弃审（workflow/*）后再修改");
            }
            if (!verified)
            {
                return;
            }
            if (QmOthSpec.IsCheck(job.Ask.Kind))
            {
                throw new BridgeException(409, "state_mismatch", job.Title + "已审核，请先弃审（vouchers/verify action=unverify）后再修改");
            }
            throw new BridgeException(409, "state_mismatch", job.Title + "已审核，不能修改");
        }

        public static void Inspect(object conn, QmEditJob job, int id)
        {
            if (CoRows.Col(job.Doc, "CVERIFIER").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", job.Title + "已审核，请先弃审（vouchers/verify action=unverify）后再修改");
            }
            if (QmSql.Dec(QmSql.Scalar(conn, QmOthDel.InsDownSql, id, id)) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "已有其他检验单，不能修改");
            }
        }

        static bool Flag(string text)
        {
            return text == "1" || string.Equals(text, "true", System.StringComparison.OrdinalIgnoreCase);
        }
    }
}
