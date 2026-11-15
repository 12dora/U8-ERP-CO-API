using System.Collections.Generic;

namespace U8Co
{
    // 应付审核 / 应收审核的闸门，在事务里、带锁读表头之后、调用 U8 之前。拒绝一律 409（审批流 409 workflow_enabled）。
    // 审核：已采购复核（销售发票已复核 cChecker）、未审核、没有审批流、没有网络锁、表体没有 cClue、登录日期所在期间未结账。
    // 弃审：已审核、没有凭证、没有核销或其他处理、审核登记行所在期间未结账。审核人是不是本人交给 U8 判断。
    internal static class ArapAuditGate
    {
        public static void Check(object conn, ArapAuditSpec spec, Dictionary<string, object> head, bool undo, string date)
        {
            string type = CoRows.Col(head, "vtype");
            if (System.Array.IndexOf(spec.Types, type) < 0)
            {
                throw new BridgeException(400, "bad_request", "只支持专用发票和普通发票");
            }
            if (undo)
            {
                Unverify(conn, spec, head, type);
                return;
            }
            Verify(conn, spec, head, date);
        }

        static void Verify(object conn, ArapAuditSpec spec, Dictionary<string, object> head, string date)
        {
            if (CoRows.FlagOf(head, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，不能直接审核");
            }
            if (CoRows.Col(head, "reviewer").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "发票未复核，请先复核");
            }
            if (CoRows.Col(head, "auditor").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "发票已" + spec.Side + "审核");
            }
            if (CoRows.FlagOf(head, "locked"))
            {
                throw new BridgeException(409, "state_mismatch", "发票正被其他操作锁定");
            }
            if (ArapAuditSql.Count(head, "clue") > 0)
            {
                throw new BridgeException(409, "state_mismatch", "发票已有" + spec.Side + "处理记录");
            }
            if (ArapAuditSql.LoginPeriodClosed(conn, spec, date))
            {
                throw new BridgeException(409, "state_mismatch", spec.Side + "已结账");
            }
        }

        static void Unverify(object conn, ArapAuditSpec spec, Dictionary<string, object> head, string type)
        {
            if (CoRows.Col(head, "auditor").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "发票未" + spec.Side + "审核");
            }
            string code = CoRows.Col(head, "code");
            if (ArapAuditSql.HasVoucher(conn, spec, type, code))
            {
                throw new BridgeException(409, "state_mismatch", "已生成凭证，请先删除凭证");
            }
            if (ArapAuditSql.HasOther(conn, spec, type, code))
            {
                throw new BridgeException(409, "state_mismatch", "已核销或有其他处理，请先取消");
            }
            if (ArapAuditSql.OwnPeriodClosed(conn, spec, type, code))
            {
                throw new BridgeException(409, "state_mismatch", spec.Side + "已结账");
            }
        }
    }
}
