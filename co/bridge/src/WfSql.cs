namespace U8Co
{
    internal static class WfSql
    {
        public static string HeadSql(VoucherKind kind)
        {
            return "SELECT " + kind.CodeColumn + " AS code, "
                + "IVERIFYSTATE AS verify_state, iVerifyStateNew AS verify_state_new, "
                + "IsWfControlled AS controlled, iReturnCount AS return_count, "
                + "cCurrentAuditor AS current_auditor, "
                + kind.VerifierColumn + " AS verifier, "
                + "CONVERT(varchar(19), " + kind.VerifyDateColumn + ", 120) AS verified_at "
                + "FROM " + kind.HeadTable + " WHERE " + kind.IdColumn + "=? AND CVOUCHTYPE=?";
        }

        public static string InstanceSql()
        {
            return "SELECT TOP 1 CONVERT(varchar(40), PIID) AS piid, FlagCode AS flag_code, "
                + "StartPerformer AS started_by, CONVERT(varchar(19), StartTime, 120) AS started_at "
                + "FROM WF_ActiveFlow WHERE RTRIM(VoucherType)=? AND RTRIM(VoucherId)=CONVERT(varchar(20), ?) "
                + "ORDER BY CASE WHEN FlagCode=0 THEN 0 ELSE 1 END, StartTime DESC, ID DESC";
        }

        public static string PendingSql()
        {
            return "SELECT CONVERT(varchar(40), t.cTK_ID) AS task_id, "
                + "CONVERT(varchar(40), t.cActivityInstID) AS activity_id, "
                + "t.cUserID AS person, u.cUser_Id AS operator, t.cTaskType AS task_type "
                + "FROM Table_Task t OUTER APPLY (SELECT TOP 1 cUser_Id FROM UserHrPersonContro "
                + "WHERE RTRIM(cPsn_Num)=RTRIM(t.cUserID) ORDER BY cUser_Id) u "
                + "WHERE RTRIM(t.cVoucherID)=CONVERT(varchar(20), ?) AND RTRIM(t.cVoucherType)=? AND t.cTK_State=0";
        }

        public static string AbandonSql()
        {
            return "SELECT COUNT(*) AS n FROM WFAudit WHERE BizObjectId=? "
                + "AND RTRIM(VoucherId)=CONVERT(varchar(20), ?) AND Action=7 AND RTRIM(OperatorId)=?";
        }

        public static string AgreeSql()
        {
            return "SELECT TOP 1 a.Action AS action FROM WFAudit a "
                + "INNER JOIN (SELECT TOP 1 PIID FROM WF_ActiveFlow "
                + "WHERE RTRIM(VoucherType)=? AND RTRIM(VoucherId)=CONVERT(varchar(20), ?) "
                + "ORDER BY StartTime DESC, ID DESC) f "
                + "ON REPLACE(REPLACE(LOWER(RTRIM(a.InstanceId)), '{', ''), '}', '') "
                + "= REPLACE(REPLACE(LOWER(CONVERT(varchar(40), f.PIID)), '{', ''), '}', '') "
                + "WHERE a.BizObjectId=? AND RTRIM(a.VoucherId)=CONVERT(varchar(20), ?) "
                + "AND a.Action=1 AND RTRIM(a.OperatorId)=?";
        }

        public static string HistorySql()
        {
            return "SELECT a.Action AS action, a.TaskName AS task, a.Opinion AS opinion, "
                + "a.OperatorId AS person, u.cUser_Id AS operator, a.OperatorName AS name, "
                + "CONVERT(varchar(19), a.OperationDate, 120) AS at "
                + "FROM WFAudit a OUTER APPLY (SELECT TOP 1 cUser_Id FROM UserHrPersonContro "
                + "WHERE RTRIM(cPsn_Num)=RTRIM(a.OperatorId) ORDER BY cUser_Id) u "
                + "WHERE a.BizObjectId=? AND RTRIM(a.VoucherId)=CONVERT(varchar(20), ?) "
                + "ORDER BY a.OperationDate, a.id";
        }

        public static string TaskSql()
        {
            return "SELECT CONVERT(varchar(40), t.cTK_ID) AS task_id, RTRIM(t.cVoucherType) AS biz, "
                + "RTRIM(t.cVoucherID) AS voucher_id, t.cTaskType AS task_type, "
                + "CONVERT(varchar(40), t.cActivityInstID) AS activity_id, "
                + "t.cFromUserID AS from_name, CONVERT(varchar(19), t.cCreateTime, 120) AS created_at, "
                + "t.cTK_Name AS title, CONVERT(varchar(40), t.cProcInstID) AS piid, "
                + "t.ExtendField3 AS extend_code, c.CCHECKCODE AS check_code, r.CREJECTCODE AS reject_code "
                + "FROM Table_Task t "
                + "LEFT JOIN QMCHECKVOUCHER c ON RTRIM(t.cVoucherID)=CONVERT(varchar(20), c.ID) "
                + "AND c.CVOUCHTYPE=RTRIM(t.cVoucherType) "
                + "LEFT JOIN QMREJECTVOUCHER r ON RTRIM(t.cVoucherID)=CONVERT(varchar(20), r.ID) "
                + "AND r.CVOUCHTYPE=RTRIM(t.cVoucherType) "
                + "WHERE t.cTK_State=0 AND RTRIM(t.cUserID)=? "
                + "ORDER BY t.cCreateTime, t.cTK_ID";
        }
    }
}
