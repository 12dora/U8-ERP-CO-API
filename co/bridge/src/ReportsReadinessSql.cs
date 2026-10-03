using System.Text;

namespace U8Co
{
    // 账套体检的 SQL（全部只读）。表名、列名都是这里的常量；调用方的值（账套号、操作员）只进 ? 参数。
    // 跨库的只有 UFSYSTEM..UA_PatchList / UA_Period / UA_Account_sub，读不到时该项为 unknown。
    // 只查目录、配置表和小表的 COUNT / MAX，不扫单据表。
    internal static class ReportsReadinessSql
    {
        // 数据库服务器的当天（不用 Windows 时钟）。
        internal const string TodaySql = "SELECT CONVERT(varchar(10), GETDATE(), 23) AS d";

        // 本账套库的目录检查：补丁表、补丁旁证列（审批流历史的 signatureid，补丁才加；缺了只 warn）、工作日历表、供应商扩展自定义项表。
        internal const string CatalogSql = "SELECT"
            + " CASE WHEN OBJECT_ID(N'dbo.UA_PatchList') IS NULL THEN 0 ELSE 1 END AS patch_table,"
            + " CASE WHEN COL_LENGTH(N'dbo.WFAudit', N'signatureid') IS NULL THEN 0 ELSE 1 END AS canary,"
            + " CASE WHEN OBJECT_ID(N'dbo.bas_calendardetail') IS NULL THEN 0 ELSE 1 END AS cal_table,"
            + " CASE WHEN OBJECT_ID(N'dbo.Vendor_extradefine') IS NULL THEN 0 ELSE 1 END AS vendor_ext";

        // 只在 CatalogSql 确认表存在后再跑（表不存在时整句编译失败）。
        internal const string PatchLocalSql = "SELECT COUNT(*) AS n FROM dbo.UA_PatchList";
        internal const string PatchSysSql = "SELECT COUNT(*) AS n FROM UFSYSTEM..UA_PatchList";

        // 本账套已建年度（未删除）：每年一行，起止日期。参数：账套号。
        internal const string PeriodSql = "SELECT iYear AS y, CONVERT(varchar(10), MIN(dBegin), 23) AS b,"
            + " CONVERT(varchar(10), MAX(dEnd), 23) AS e FROM UFSYSTEM..UA_Period"
            + " WHERE cAcc_Id=? AND (bIsDelete=0 OR bIsDelete IS NULL) GROUP BY iYear ORDER BY iYear";

        // 总账月结：每年一行，1 到 12 期里未结账（bflag 不是 1）的期数和第一个未结账期间（同 GlPostCheck 的 FirstOpenSql；
        // 全部已结账为 NULL）。也是 UA_Period 读不到时年度名单的后备。
        internal const string GlMendSql = "SELECT iyear AS y,"
            + " SUM(CASE WHEN iperiod BETWEEN 1 AND 12 AND ISNULL(bflag,0)=0 THEN 1 ELSE 0 END) AS open_n,"
            + " MIN(CASE WHEN iperiod BETWEEN 1 AND 12 AND ISNULL(bflag,0)=0 THEN iperiod END) AS first_open"
            + " FROM GL_mend GROUP BY iyear ORDER BY iyear";

        // SYSTEM 日历（CalendarId=1）的最后一天。
        internal const string CalendarSql = "SELECT CONVERT(varchar(10), MAX(CalDate), 23) AS m"
            + " FROM bas_calendardetail WHERE CalendarId=1";

        // 已启用的子系统（iYear=9999 那一行的启用日期）。参数：账套号。
        internal const string ModulesSql = "SELECT RTRIM(cSub_Id) AS s FROM UFSYSTEM..UA_Account_sub"
            + " WHERE cAcc_Id=? AND iYear=9999 AND dSubSysUsed IS NOT NULL";

        // 来料 / 产品检验单（QM03 / QM04）已发布并启用的审批流，与 AdoXml.WorkflowReleased 同一判断；
        // 操作员是否对到了人员（审批人配置的是人员）。参数：操作员编码。
        internal const string WorkflowSql = "SELECT"
            + " (SELECT COUNT(*) FROM Table_WorkFlowRelease r JOIN AuditBizObjects o ON o.cBizObjectId=r.cBizObjectId"
            + " WHERE r.Status=0 AND o.srcTable=N'QMCHECKVOUCHER' AND r.cBizObjectId=N'QM03'"
            + " AND r.cBizEventId=o.cBizObjectId+'.Submit') AS qm03,"
            + " (SELECT COUNT(*) FROM Table_WorkFlowRelease r JOIN AuditBizObjects o ON o.cBizObjectId=r.cBizObjectId"
            + " WHERE r.Status=0 AND o.srcTable=N'QMCHECKVOUCHER' AND r.cBizObjectId=N'QM04'"
            + " AND r.cBizEventId=o.cBizObjectId+'.Submit') AS qm04,"
            + " (SELECT COUNT(*) FROM UserHrPersonContro WHERE cUser_Id=? AND ISNULL(cPsn_Num,N'')<>N'') AS linked";

        // 本位币、缺省采购类型、末级收发类别（发 / 收）、单据编号规则。
        internal const string DefaultsSql = "SELECT"
            + " (SELECT COUNT(*) FROM foreigncurrency WHERE iotherused=-1) AS home,"
            + " (SELECT COUNT(*) FROM PurchaseType WHERE bDefault=1) AS pt,"
            + " (SELECT COUNT(*) FROM Rd_Style WHERE bRdEnd=1 AND bRdFlag=0) AS rd_out,"
            + " (SELECT COUNT(*) FROM Rd_Style WHERE bRdEnd=1 AND bRdFlag=1) AS rd_in,"
            + " (SELECT COUNT(*) FROM VoucherNumber) AS numbering";

        // 按年度的配置表：{ 表名, 附加条件 }。建立年度账只拷 code / GradeDef / AccInformation_Year，其余要从起始年拷。
        internal static readonly string[][] YearlyTables = new string[][]
        {
            new string[] { "GL_CashItemDataSource", "" },
            new string[] { "Ap_InputCode", "" },
            new string[] { "AP_OppCodeSet", "" },
            new string[] { "AP_CtrlCodeSet", "" },
            new string[] { "Ap_SStyleCode", "" },
            new string[] { "code", "" },
            new string[] { "GradeDef_Base", " WHERE KEYWORD=N'code'" },
            new string[] { "AccInformation_Year", "" }
        };

        // 一句查完：每表每年度一行 { t, y, n }。
        internal static readonly string YearlySql = BuildYearly();

        static string BuildYearly()
        {
            StringBuilder sql = new StringBuilder();
            for (int i = 0; i < YearlyTables.Length; i++)
            {
                string table = YearlyTables[i][0];
                sql.Append(i == 0 ? "" : " UNION ALL ")
                    .Append("SELECT N'").Append(table).Append("' AS t, iYear AS y, COUNT(*) AS n FROM ").Append(table)
                    .Append(YearlyTables[i][1]).Append(" GROUP BY iYear");
            }
            return sql.ToString();
        }
    }
}
