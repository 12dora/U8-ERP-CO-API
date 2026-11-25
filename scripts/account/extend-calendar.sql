-- extend-calendar.sql — 延长测试账套的系统工作日历（CalendarId = 1，「SYSTEM」日历）。
--
-- 用途：新建账套的工作日历只到建账时预置的日期为止。日历用完后，按工作日排期的单据（生产订单、
--       MRP 等）报错，账套体检 `calendar` 失败（离末日不足 30 天时 warn）。
--       优先在 U8「基础档案 → 工作日历」里延长 SYSTEM 日历；本脚本把源账套里、目标账套最后一天之后的
--       日历行（含每天的工作时段）复制过来。
-- 前提：
--   * 源账套 `RefDb` 是一个已配置好、日历足够长的账套（例如 UFDATA_998_2024），和目标在同一个 SQL 实例上。
--   * 目标 `TargetDb` 是测试账套（例如 UFDATA_801_2024）；没有业务单据，否则拒绝（`Force` = 1 可放行）。
-- 备份：执行前请 DBA 对 `TargetDb` 做一份 COPY_ONLY 完整备份。
-- 写什么：只写 `TargetDb` 的 bas_calendardetail 和 bas_calendardetail_wkhour，新增行；不改已有行。
--   新行的主键接着目标库里小于 1000000000 的最大值往上编（U8 自己的编号器从 1000000000 起发号，那一段不碰）；
--   新编号会到 1000000000 时拒绝，什么都不写。只读 `RefDb`。
-- 还原：恢复备份；或者删除目标库 CalendarId = 1 中本次新增日期（执行前的最后一天之后）的
--   bas_calendardetail 行，以及 calendarDID 指向这些行的 bas_calendardetail_wkhour 行。
--
-- 用法：改下面的占位值（:setvar 优先于 sqlcmd -v），然后
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i extend-calendar.sql
-- 可以重跑：源账套没有更晚的日期时什么都不写。

:on error exit
:setvar RefDb    "UFDATA_<源账套>_<年度>"
:setvar TargetDb "UFDATA_<测试账套>_<年度>"
:setvar Force    "0"

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

-- 2. 业务数据检查
IF '$(Force)' <> '1' AND (
       EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.GL_accvouch)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.mom_order)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.QMCHECKVOUCHERS)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.PO_Pomain)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.SO_SOMain)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord01)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.RdRecord32)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_Vouch)
    OR EXISTS (SELECT 1 FROM [$(TargetDb)].dbo.Ap_CloseBill))
BEGIN RAISERROR(N'目标账套已有业务单据。请在 U8 工作日历里延长；确属测试账套才把 Force 设为 1。', 16, 1); RETURN; END;
GO

-- 3. 复制日历（单个事务）
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRANSACTION;
DECLARE @last datetime = (SELECT MAX(CalDate) FROM [$(TargetDb)].dbo.bas_calendardetail WITH (UPDLOCK, HOLDLOCK) WHERE CalendarId = 1);
-- 只在 1000000000 以下编号：U8 的编号器从 1000000000 起发号。
DECLARE @cap int = 1000000000;
DECLARE @dbase int = (SELECT ISNULL(MAX(CalendarDId), 0) FROM [$(TargetDb)].dbo.bas_calendardetail WITH (UPDLOCK, HOLDLOCK)
                      WHERE CalendarDId < @cap);
DECLARE @wbase int = (SELECT ISNULL(MAX(CalendarWKTID), 0) FROM [$(TargetDb)].dbo.bas_calendardetail_wkhour WITH (UPDLOCK, HOLDLOCK)
                      WHERE CalendarWKTID < @cap);
IF @last IS NULL
BEGIN ROLLBACK TRANSACTION; RAISERROR(N'目标账套没有 SYSTEM 日历（CalendarId = 1）。先在 U8 工作日历里建好。', 16, 1); RETURN; END;

CREATE TABLE #map (old_id int PRIMARY KEY, new_id int NOT NULL);
INSERT INTO #map (old_id, new_id)
SELECT CalendarDId, @dbase + ROW_NUMBER() OVER (ORDER BY CalDate, Seq, CalendarDId)
FROM [$(RefDb)].dbo.bas_calendardetail
WHERE CalendarId = 1 AND CalDate > @last;

IF (SELECT ISNULL(MAX(new_id), 0) FROM #map) >= @cap
   OR @wbase + (SELECT COUNT_BIG(*) FROM [$(RefDb)].dbo.bas_calendardetail_wkhour AS w
                JOIN #map AS m ON m.old_id = w.calendarDID) >= @cap
BEGIN ROLLBACK TRANSACTION; RAISERROR(N'新编号会到 1000000000（U8 编号器的号段），拒绝。请在 U8 工作日历里延长。', 16, 1); RETURN; END;

INSERT INTO [$(TargetDb)].dbo.bas_calendardetail (CalendarDId, CalendarId, CalDate, Seq, WkHours, StartTime, EndTime)
SELECT m.new_id, 1, d.CalDate, d.Seq, d.WkHours, d.StartTime, d.EndTime
FROM [$(RefDb)].dbo.bas_calendardetail AS d
JOIN #map AS m ON m.old_id = d.CalendarDId;

INSERT INTO [$(TargetDb)].dbo.bas_calendardetail_wkhour
    (CalendarWKTID, calendarDID, Sortseq, StartTime, EndTime, StartLoc, EndLoc)
SELECT @wbase + ROW_NUMBER() OVER (ORDER BY w.StartTime, w.Sortseq, w.CalendarWKTID),
       m.new_id, w.Sortseq, w.StartTime, w.EndTime, w.StartLoc, w.EndLoc
FROM [$(RefDb)].dbo.bas_calendardetail_wkhour AS w
JOIN #map AS m ON m.old_id = w.calendarDID;

SELECT 'added' AS t, COUNT(*) AS days, CONVERT(varchar(10), @last, 23) AS old_last FROM #map;
COMMIT TRANSACTION;
GO

-- 4. 核对（只读）：目标的最后一天应不早于源账套，且远于今天。
SELECT 'target' AS t, MIN(CONVERT(varchar(10), CalDate, 23)) AS mn, MAX(CONVERT(varchar(10), CalDate, 23)) AS mx, COUNT(*) AS n
FROM [$(TargetDb)].dbo.bas_calendardetail WHERE CalendarId = 1
UNION ALL
SELECT 'reference', MIN(CONVERT(varchar(10), CalDate, 23)), MAX(CONVERT(varchar(10), CalDate, 23)), COUNT(*)
FROM [$(RefDb)].dbo.bas_calendardetail WHERE CalendarId = 1;
SELECT 'wkhour' AS t, COUNT(*) AS n FROM [$(TargetDb)].dbo.bas_calendardetail_wkhour;
SELECT 'today' AS t, CONVERT(varchar(10), GETDATE(), 23) AS d;
GO
