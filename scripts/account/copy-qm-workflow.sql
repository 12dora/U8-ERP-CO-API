-- copy-qm-workflow.sql — 把源账套的质量检验方案和审批流（设计 + 发布）复制到空测试账套。
--
-- 用途：质量单据审批用例要求检验单的审批流（QM03 来料检验、QM04 产品检验等）已发布，账套体检 `workflow` 才是 ok。
--       优先在 U8「审批流设计器」里设计并发布；本脚本是没有界面时的替代，复制后发布记录的账套号改成目标账套。
--       在 copy-config.sql、copy-year-config.sql 之后跑。
-- 前提：
--   * 源账套 `RefDb`（例如 UFDATA_998_2024）的审批流已发布、在源账套上能跑通；
--     目标 `TargetDb`（例如 UFDATA_801_2024）是空测试账套，两者在同一个 SQL 实例上，补丁一致。
--   * 目标没有任何业务单据（总账凭证、生产订单、检验单、采购订单、销售订单、采购入库、销售出库、应收应付单、收付款单）。
--     有单据时一律拒绝，没有放行开关。
-- 备份：执行前请 DBA 对 `TargetDb` 做一份 COPY_ONLY 完整备份。
-- 写什么：只写 `TargetDb`（先删后插）：
--   * 检验方案 QMCHECKNORMAL / QMCHECKITEM / QMCHECKGUIDE / QMCHECKPROJECT / QMCHECKPROJECTS。
--     方案里的部门、检验员、人员编码置空：空账套还没有这些档案。
--   * 审批流 Table_WorkFlow、Table_WorkFlowRelease、Table_WorkFlow_ReturnRelease、Table_Workflow_StandardTime、
--     table_workflow_openHistory。保留源账套的编号（GUID）和 cYear，发布记录的 cAcc_Id 改成目标账套号。
--   * 不复制待办、审批实例（Table_Task / WF_*）、操作员与人员的对应（UserHrPersonContro）。
--   只读 `RefDb`。
-- 之后：审批流 XML 里点名的审批人仍是源账套的人员编码。造数（python -m co.e2e.seed 的人员一步）会把这些人员建出来，
--   并把 e2e 操作员对到专用人员；或者在 U8 里打开审批流设计器改审批人后重新发布。
-- 还原：恢复备份。
--
-- 用法：改下面的占位值（:setvar 优先于 sqlcmd -v），然后
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i copy-qm-workflow.sql
-- 可以重跑。

:on error exit
:setvar RefDb    "UFDATA_<源账套>_<年度>"
:setvar TargetDb "UFDATA_<测试账套>_<年度>"

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- 1. 参数检查（只读）
DECLARE @ref sysname = N'$(RefDb)', @dst sysname = N'$(TargetDb)';
IF @ref NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
   OR @dst NOT LIKE N'UFDATA[_][0-9][0-9][0-9][_][12][0-9][0-9][0-9]'
BEGIN RAISERROR(N'RefDb 和 TargetDb 都必须是 UFDATA_<账套>_<年度>。', 16, 1); RETURN; END;
IF SUBSTRING(@ref, 8, 3) = SUBSTRING(@dst, 8, 3)
BEGIN RAISERROR(N'目标账套和源账套不能是同一个账套。', 16, 1); RETURN; END;
IF DB_ID(@ref) IS NULL OR DB_ID(@dst) IS NULL
BEGIN RAISERROR(N'RefDb 或 TargetDb 不存在。', 16, 1); RETURN; END;
GO

-- 2. 空账套检查（没有放行开关）
IF EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.QMCHECKVOUCHERS) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.mom_order)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.GL_accvouch)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.PO_Pomain) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.SO_SOMain)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord01) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord32)
   OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_Vouch) OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_CloseBill)
BEGIN RAISERROR(N'目标账套已有业务数据；配置复制只用于空账套。', 16, 1); RETURN; END;
GO

-- 3. 复制（单个事务）
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @acc nvarchar(3) = SUBSTRING(N'$(TargetDb)', 8, 3);
BEGIN TRANSACTION;

-- 3.1 检验方案
DELETE FROM [$(TargetDb)].dbo.QMCHECKPROJECTS;
DELETE FROM [$(TargetDb)].dbo.QMCHECKPROJECT;
DELETE FROM [$(TargetDb)].dbo.QMCHECKGUIDE;
DELETE FROM [$(TargetDb)].dbo.QMCHECKITEM;
DELETE FROM [$(TargetDb)].dbo.QMCHECKNORMAL;

INSERT INTO [$(TargetDb)].dbo.QMCHECKNORMAL (CHKNORMALGUID, CCHKNORMALCODE, CCHKNORMALNAME, CMEMO)
SELECT CHKNORMALGUID, CCHKNORMALCODE, CCHKNORMALNAME, CMEMO FROM [$(RefDb)].dbo.QMCHECKNORMAL;

INSERT INTO [$(TargetDb)].dbo.QMCHECKITEM (CHKITEMGUID, CCHKITEMCODE, CCHKITEMNAME, CMEMO)
SELECT CHKITEMGUID, CCHKITEMCODE, CCHKITEMNAME, CMEMO FROM [$(RefDb)].dbo.QMCHECKITEM;

INSERT INTO [$(TargetDb)].dbo.QMCHECKGUIDE (
    CHKGUIDEGUID, CCHKGUIDECODE, CCHKGUIDENAME, CINPORTGRADE, CGUIDEUNIT, CCHKITEMCODE, BREF, CMEMO,
    CINSLEVEL, FAQL, CBUGGRADE, CCHKGUIDTYPE, IDTMETHOD, FDTRATE, FDTQUANTITY, IDTSTYLE, CRULECODE,
    BSYNCHRON, CGROUPCODE, CGCOMUNITCODE, bBreakCheck, bMustBreak, CROUNDED, BROUNDED, BROHS,
    CCHKSTANDARDPROVISION, CMAKER, DCREATEDATE, CMODIFIER, DMODIFYDATE)
SELECT
    CHKGUIDEGUID, CCHKGUIDECODE, CCHKGUIDENAME, CINPORTGRADE, CGUIDEUNIT, CCHKITEMCODE, BREF, CMEMO,
    CINSLEVEL, FAQL, CBUGGRADE, CCHKGUIDTYPE, IDTMETHOD, FDTRATE, FDTQUANTITY, IDTSTYLE, CRULECODE,
    BSYNCHRON, CGROUPCODE, CGCOMUNITCODE, bBreakCheck, bMustBreak, CROUNDED, BROUNDED, BROHS,
    CCHKSTANDARDPROVISION, CMAKER, DCREATEDATE, CMODIFIER, DMODIFYDATE
FROM [$(RefDb)].dbo.QMCHECKGUIDE;

INSERT INTO [$(TargetDb)].dbo.QMCHECKPROJECT (
    PROJECTGUID, ID, IVTID, CPROJECTCODE, CPROJECTNAME, CCHKNORMALCODE, DDATE, CBUILDPERSON, CMAKER, CVOUCHTYPE,
    CDEFINE1, CDEFINE2, CDEFINE3, CDEFINE4, CDEFINE5, CDEFINE6, CDEFINE7, CDEFINE8, CDEFINE9, CDEFINE10,
    CDEFINE11, CDEFINE12, CDEFINE13, CDEFINE14, CDEFINE15, CDEFINE16,
    DCOSTDATE, CCOSTTYPE, CCOSTMAKER, FCOST,
    CDEPCODE, CCHECKPERSONCODE, BSTOPUSE, CMODIFIER, DMODIFYDATE, DMAKETIME, DMODIFYTIME,
    iPrintCount, CCLEANVER, CSYSBARCODE)
SELECT
    PROJECTGUID, ID, IVTID, CPROJECTCODE, CPROJECTNAME, CCHKNORMALCODE, DDATE, CBUILDPERSON, CMAKER, CVOUCHTYPE,
    CDEFINE1, CDEFINE2, CDEFINE3, CDEFINE4, CDEFINE5, CDEFINE6, CDEFINE7, CDEFINE8, CDEFINE9, CDEFINE10,
    CDEFINE11, CDEFINE12, CDEFINE13, CDEFINE14, CDEFINE15, CDEFINE16,
    DCOSTDATE, CCOSTTYPE, CCOSTMAKER, FCOST,
    NULL, NULL, -- 部门、检验员：空账套还没有部门和人员档案
    BSTOPUSE, CMODIFIER, DMODIFYDATE, DMAKETIME, DMODIFYTIME,
    iPrintCount, CCLEANVER, CSYSBARCODE
FROM [$(RefDb)].dbo.QMCHECKPROJECT;

INSERT INTO [$(TargetDb)].dbo.QMCHECKPROJECTS (
    ID, AUTOID, CCHKITEMCODE, CCHKGUIDECODE, CGUIDEUNIT, BGUIDETYPE, CSTANDARD, IUPPERLIMIT, ILOWERLIMIT,
    CINPORTGRADE, CCHKAPPCODE, CINVCODE, CCHKPLACECODE, CCHKDRAWCODE, CCHKTEMPCODE, CPERSONCODE,
    CDEFINE22, CDEFINE23, CDEFINE24, CDEFINE25, CDEFINE26, CDEFINE27, CDEFINE28, CDEFINE29, CDEFINE30,
    CDEFINE31, CDEFINE32, CDEFINE33, CDEFINE34, CDEFINE35, CDEFINE36, CDEFINE37,
    CMEMO, CCHKENVCODE, CINSLEVEL, FAQL, CBUGGRADE, CRAMISTYPE, CISDEVCOSTTYPE, FISDEVFEE,
    CISMATYPE, FISMAUNITPRICE, FISMARATE, FISMAFEE, CISMANRATETYPE, FISMANFEE, BSHOWPROOF,
    CCHKGUIDTYPE, IDTMETHOD, FDTRATE, FDTQUANTITY, CRULECODE, FUPPERWARP, FLOWERWARP, bRecordDetails,
    CGROUPCODE, CGCOMUNITCODE, BBREAKCHECK, BMUSTBREAK, CROUNDED, BROUNDED, BROHS, BRANDORPROJECTB,
    CCHKSTANDARDPROVISION, BATCHPROPERTYB, BMUSTCHECK, IVOUCHROWNO)
SELECT
    ID, AUTOID, CCHKITEMCODE, CCHKGUIDECODE, CGUIDEUNIT, BGUIDETYPE, CSTANDARD, IUPPERLIMIT, ILOWERLIMIT,
    CINPORTGRADE, CCHKAPPCODE, CINVCODE, CCHKPLACECODE, CCHKDRAWCODE, CCHKTEMPCODE,
    NULL, -- 人员：空账套还没有人员档案
    CDEFINE22, CDEFINE23, CDEFINE24, CDEFINE25, CDEFINE26, CDEFINE27, CDEFINE28, CDEFINE29, CDEFINE30,
    CDEFINE31, CDEFINE32, CDEFINE33, CDEFINE34, CDEFINE35, CDEFINE36, CDEFINE37,
    CMEMO, CCHKENVCODE, CINSLEVEL, FAQL, CBUGGRADE, CRAMISTYPE, CISDEVCOSTTYPE, FISDEVFEE,
    CISMATYPE, FISMAUNITPRICE, FISMARATE, FISMAFEE, CISMANRATETYPE, FISMANFEE, BSHOWPROOF,
    CCHKGUIDTYPE, IDTMETHOD, FDTRATE, FDTQUANTITY, CRULECODE, FUPPERWARP, FLOWERWARP, bRecordDetails,
    CGROUPCODE, CGCOMUNITCODE, BBREAKCHECK, BMUSTBREAK, CROUNDED, BROUNDED, BROHS, BRANDORPROJECTB,
    CCHKSTANDARDPROVISION, BATCHPROPERTYB, BMUSTCHECK, IVOUCHROWNO
FROM [$(RefDb)].dbo.QMCHECKPROJECTS;

-- 3.2 审批流设计和发布
DELETE FROM [$(TargetDb)].dbo.table_workflow_openHistory;
DELETE FROM [$(TargetDb)].dbo.Table_Workflow_StandardTime;
DELETE FROM [$(TargetDb)].dbo.Table_WorkFlow_ReturnRelease;
DELETE FROM [$(TargetDb)].dbo.Table_WorkFlowRelease;
DELETE FROM [$(TargetDb)].dbo.Table_WorkFlow;

INSERT INTO [$(TargetDb)].dbo.Table_WorkFlow (
    WK_ID, ProcessID, SolutionID, KindID, Version, SL_ID, WorkflowName, Description, BinaryData,
    EventID, XML, Status, LastUpdatedDateTime, RuleID, RouterExp, cBizObjectId, cBizEventId,
    bModifyBizData, bCheckDataPopedom, AuthString, bRevokeAudtion, bVHeadAttach, iVTemplate,
    bMakerSubmit, ProductVersion, bVAllAttach, bVBodyAttach, bAutoAudit, bupdate)
SELECT
    WK_ID, ProcessID, SolutionID, KindID, Version, SL_ID, WorkflowName, Description, BinaryData,
    EventID, XML, Status, LastUpdatedDateTime, RuleID, RouterExp, cBizObjectId, cBizEventId,
    bModifyBizData, bCheckDataPopedom, AuthString, bRevokeAudtion, bVHeadAttach, iVTemplate,
    bMakerSubmit, ProductVersion, bVAllAttach, bVBodyAttach, bAutoAudit, bupdate
FROM [$(RefDb)].dbo.Table_WorkFlow;

INSERT INTO [$(TargetDb)].dbo.Table_WorkFlowRelease (
    PDID, WK_ID, ProcessID, cYear, cAcc_Id, SL_ID, SolutionID, KindID, WorkflowName, Description,
    DesignVersion, Version, BinaryData, EventID, XML, ReleaseTime, LastUpdatedDateTime, Status,
    RuleID, RouterExp, cBizObjectId, cBizEventId, bModifyBizData, bCheckDataPopedom, CommonString,
    bRevokeAudtion, bVHeadAttach, iVTemplate, bMakerSubmit, ProductVersion, bVAllAttach, bVBodyAttach, bAutoAudit, bupdate)
SELECT
    PDID, WK_ID, ProcessID, cYear, @acc, SL_ID, SolutionID, KindID, WorkflowName, Description,
    DesignVersion, Version, BinaryData, EventID, XML, ReleaseTime, LastUpdatedDateTime, Status,
    RuleID, RouterExp, cBizObjectId, cBizEventId, bModifyBizData, bCheckDataPopedom, CommonString,
    bRevokeAudtion, bVHeadAttach, iVTemplate, bMakerSubmit, ProductVersion, bVAllAttach, bVBodyAttach, bAutoAudit, bupdate
FROM [$(RefDb)].dbo.Table_WorkFlowRelease;

INSERT INTO [$(TargetDb)].dbo.Table_WorkFlow_ReturnRelease (
    PDID, WK_ID, cYear, cAcc_Id, bAllowContinue, bAllowReturn, bAbandanOneByOne, Version, DesignVersion, bAllowReturnSubmitter)
SELECT PDID, WK_ID, cYear, @acc, bAllowContinue, bAllowReturn, bAbandanOneByOne, Version, DesignVersion, bAllowReturnSubmitter
FROM [$(RefDb)].dbo.Table_WorkFlow_ReturnRelease;

INSERT INTO [$(TargetDb)].dbo.Table_Workflow_StandardTime (
    PDID, WK_ID, cYear, cAcc_Id, Version, DesignVersion, StandardTime, StandardTimeHour, WorkflowName)
SELECT PDID, WK_ID, cYear, @acc, Version, DesignVersion, StandardTime, StandardTimeHour, WorkflowName
FROM [$(RefDb)].dbo.Table_Workflow_StandardTime;

SET IDENTITY_INSERT [$(TargetDb)].dbo.table_workflow_openHistory ON;
INSERT INTO [$(TargetDb)].dbo.table_workflow_openHistory (id, DocID, Name, opentime)
SELECT id, DocID, Name, opentime FROM [$(RefDb)].dbo.table_workflow_openHistory;
SET IDENTITY_INSERT [$(TargetDb)].dbo.table_workflow_openHistory OFF;

COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：两边条数应相同；发布记录的 cAcc_Id 应全是目标账套号，启用的（Status = 0）应含检验单审批流。
SELECT N'QMCHECKPROJECT' AS tbl,
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.QMCHECKPROJECT) AS n_ref, (SELECT COUNT(*) FROM [$(TargetDb)].dbo.QMCHECKPROJECT) AS n_target
UNION ALL SELECT N'QMCHECKPROJECTS',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.QMCHECKPROJECTS), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.QMCHECKPROJECTS)
UNION ALL SELECT N'QMCHECKGUIDE',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.QMCHECKGUIDE), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.QMCHECKGUIDE)
UNION ALL SELECT N'Table_WorkFlow',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.Table_WorkFlow), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.Table_WorkFlow)
UNION ALL SELECT N'Table_WorkFlowRelease',
       (SELECT COUNT(*) FROM [$(RefDb)].dbo.Table_WorkFlowRelease), (SELECT COUNT(*) FROM [$(TargetDb)].dbo.Table_WorkFlowRelease);
SELECT cBizObjectId, Status, cYear, cAcc_Id, WorkflowName FROM [$(TargetDb)].dbo.Table_WorkFlowRelease ORDER BY cBizObjectId, Status;
GO
