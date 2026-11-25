-- install-release-proc.sql — 在 master 里装一个签名过程 dbo.u8co_release_db，让通道登录（不是 sysadmin）在快照还原时
-- 能结束占住自己测试账套库的会话。每台 SQL 实例由 sysadmin 跑一次；加了新通道就把全部通道登录写上再跑一次。
--
-- 为什么：快照还原要 SINGLE_USER → RESTORE → MULTI_USER。U8 应用服务器用 sa 连测试账套库（.Net SqlClient 连接池），
--   SINGLE_USER 之后它的池连接常常抢先连上、占住唯一的连接；通道登录是库的所有者但故意不是 sysadmin，不能 KILL，
--   RESTORE 和 SET MULTI_USER 就失败（3101 / 5064 / 1205），库停在 SINGLE_USER。
-- 做法（模块签名，最小权限）：
--   * 证书 u8co_release_cert（口令在 T-SQL 里随机生成，不打印、不保存）和从它建的登录 u8co_release_signer。
--     这个登录不能连接，只拿两项服务器权限：ALTER ANY CONNECTION（KILL 要它）和 VIEW SERVER STATE
--     （没有它 sys.dm_exec_sessions 只看得到自己的会话，找不到要结束的会话）。
--   * 过程 dbo.u8co_release_db @db [, @who OUTPUT]：只接受 UFDATA_ / UFMeta_ 开头、不是快照、所有者正是调用者
--     （owner_sid = SUSER_SID(ORIGINAL_LOGIN())）的库，否则 RAISERROR 拒绝；结束占用该库的其他用户会话：当前库是它的，
--     或者在它上面持有库级锁的（从别的库跨库查询它的连接，例如 U8 任务服务从 UFSystem 轮询各账套库）。
--     返回值是结束的个数；@who 带回被结束会话的会话号、主机、程序和登录（还原日志要记下是谁占着）。不出结果集、不 PRINT。
--     可选 @restore_snapshot（只接受该库自己的快照）/ @user_access：在过程里紧凑重试「结束占用会话 → ALTER/RESTORE」，
--     连接池在两步之间来不及重连（见第 3 节）。ALTER 和 RESTORE 仍用调用者自己作为库所有者的权限，证书不补这两项。
--   * 用证书给过程签名，然后 REMOVE PRIVATE KEY：之后谁也不能再用这张证书签别的东西。改过程只能重跑本脚本。
--   * 每个通道登录在 master 里有一个用户，只有这个过程的 EXECUTE。
-- 可以重跑：每次都删掉旧的过程、签名登录和证书再重建（私钥已删，不能给新过程重新签名，所以证书也要换新的）。
--   master 里通道登录的用户已存在就沿用。
-- 撤销：uninstall-release-proc.sql。
--
-- 用法（sysadmin；SSMS 里要打开 SQLCMD 模式）：
--   1. 把 LaneLogins 换成全部通道的 Windows 登录，逗号分隔，例如 HOST01\u8lane1,HOST01\u8lane2。
--      :setvar 优先于 sqlcmd -v，所以要改这里，不要只在命令行传。
--   2. sqlcmd -S <SQL 实例> -E -b -f 65001 -i install-release-proc.sql
--   3. 看末尾的核对结果；e2e 预检的报告里「u8co_release_db」警告应消失。

:on error exit
:setvar LaneLogins "<通道的 Windows 登录，逗号分隔>"

SET NOCOUNT ON;
USE [master];
GO

-- 1. 检查（只读）：sysadmin、占位值、每个登录都存在且不是 sysadmin。
IF ISNULL(IS_SRVROLEMEMBER(N'sysadmin'), 0) <> 1
BEGIN RAISERROR(N'要用 sysadmin 登录执行。', 16, 1); RETURN; END;
IF N'$(LaneLogins)' LIKE N'%[<>]%' OR LTRIM(RTRIM(N'$(LaneLogins)')) = N''
BEGIN RAISERROR(N'LaneLogins 还是占位值：改成通道的 Windows 登录，逗号分隔。', 16, 1); RETURN; END;

CREATE TABLE #lane (name sysname COLLATE DATABASE_DEFAULT NOT NULL PRIMARY KEY);
DECLARE @list nvarchar(4000) = N'$(LaneLogins)' + N',', @pos int, @item nvarchar(4000);
WHILE LEN(@list) > 0
BEGIN
    SET @pos = CHARINDEX(N',', @list);
    SET @item = LTRIM(RTRIM(LEFT(@list, @pos - 1)));
    SET @list = SUBSTRING(@list, @pos + 1, 4000);
    IF @item <> N'' AND NOT EXISTS (SELECT 1 FROM #lane WHERE name = @item)
        INSERT INTO #lane (name) VALUES (@item);
END;
IF NOT EXISTS (SELECT 1 FROM #lane)
BEGIN RAISERROR(N'LaneLogins 里没有登录名。', 16, 1); RETURN; END;
-- 不用 SUSER_SID(name)：Windows 账户即使没有登录，它也能从 Windows 解析出 SID。
IF EXISTS (SELECT 1 FROM #lane AS l WHERE NOT EXISTS (SELECT 1 FROM sys.server_principals AS p WHERE p.name = l.name))
BEGIN RAISERROR(N'LaneLogins 里有不存在的登录（先建好通道登录）。', 16, 1); RETURN; END;
IF EXISTS (SELECT 1 FROM #lane WHERE IS_SRVROLEMEMBER(N'sysadmin', name) = 1)
BEGIN RAISERROR(N'LaneLogins 里有 sysadmin 登录；通道登录必须不是 sysadmin。', 16, 1); RETURN; END;
SELECT 'lane_login' AS t, name FROM #lane ORDER BY name;
GO

-- 2. 删掉旧的过程（连同签名和 EXECUTE 授权）、签名登录、证书。
IF OBJECT_ID(N'dbo.u8co_release_db', N'P') IS NOT NULL DROP PROCEDURE dbo.u8co_release_db;
IF SUSER_ID(N'u8co_release_signer') IS NOT NULL DROP LOGIN [u8co_release_signer];
IF CERT_ID(N'u8co_release_cert') IS NOT NULL DROP CERTIFICATE [u8co_release_cert];
GO

-- 3. 过程。改动之后必须重跑整个脚本重新签名，否则签名失效、过程什么都结束不了。
-- 三种用法：
--   只给 @db：结束占用该库的会话一次（旧用法，返回结束的个数）。
--   再给 @restore_snapshot：服务器端紧凑重试「结束占用会话 → SET SINGLE_USER WITH ROLLBACK IMMEDIATE + RESTORE +
--     再 SET SINGLE_USER WITH ROLLBACK IMMEDIATE（同一个动态语句，中间没有间隔；RESTORE 会回到快照当时的 MULTI_USER）」，
--     哪一步失败都整句重来（连 RESTORE 一起，空档里别人写入的也还原掉）；调用方紧接着 USE 该库占住唯一的连接。
--   或给 @user_access = SINGLE_USER / MULTI_USER：同样的循环只改用户访问模式（重新占槽位、放开多用户）。
-- 循环最多 60 次、约 90 秒；每次失败后只停 0.1 秒。用尽时再试最多 5 次放开多用户，不把库留在被别人占着的 SINGLE_USER。ALTER / RESTORE 用的是调用者自己的权限（库的所有者），
-- 证书只补 KILL 和看会话的权限。@tries 带回尝试次数；用尽仍失败时 RAISERROR（文本里有「最后错误 <错误号>」和占用会话）
-- 并返回 -2，拒绝时返回 -1。
CREATE PROCEDURE dbo.u8co_release_db
    @db sysname,
    @who nvarchar(1000) = NULL OUTPUT,
    @restore_snapshot sysname = NULL,
    @user_access nvarchar(20) = NULL,
    @tries int = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    -- ALTER DATABASE 不当死锁牺牲者（1205）；等锁最多 10 秒就进下一轮，重新结束占用会话再试。
    SET DEADLOCK_PRIORITY HIGH;
    SET LOCK_TIMEOUT 10000;
    DECLARE @id int = DB_ID(@db), @me int = @@SPID, @n int = 0, @spid int, @kill nvarchar(40), @tag nvarchar(400),
            @key nvarchar(400), @step nvarchar(max) = NULL, @again nvarchar(max) = NULL, @err int = 0,
            @msg nvarchar(400) = N'', @deadline datetime2(3) = DATEADD(SECOND, 90, SYSDATETIME()), @limit int = 60,
            @phase int = 0, @fail int = 0, @failMsg nvarchar(400) = N'';
    SET @who = N'';
    SET @tries = 0;
    IF @id IS NULL OR @id <= 4
       OR NOT (@db LIKE N'UFDATA[_]%' OR @db LIKE N'UFMeta[_]%')
       OR NOT EXISTS (SELECT 1 FROM sys.databases
                      WHERE database_id = @id AND source_database_id IS NULL
                        AND owner_sid = SUSER_SID(ORIGINAL_LOGIN()))
    BEGIN
        RAISERROR(N'u8co_release_db 拒绝 %s：只处理调用者自己拥有的 UFDATA_ / UFMeta_ 库（不含快照）。', 16, 1, @db);
        RETURN -1;
    END;
    IF @restore_snapshot IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = @restore_snapshot AND source_database_id = @id)
    BEGIN
        RAISERROR(N'u8co_release_db 拒绝 %s：%s 不是这个库的快照。', 16, 1, @db, @restore_snapshot);
        RETURN -1;
    END;
    IF (@user_access IS NOT NULL AND @user_access NOT IN (N'SINGLE_USER', N'MULTI_USER'))
       OR (@user_access IS NOT NULL AND @restore_snapshot IS NOT NULL)
    BEGIN
        RAISERROR(N'u8co_release_db 拒绝 %s：@user_access 只能是 SINGLE_USER 或 MULTI_USER，且不能和 @restore_snapshot 同给。', 16, 1, @db);
        RETURN -1;
    END;
    SET @again = N'ALTER DATABASE ' + QUOTENAME(@db) + N' SET SINGLE_USER WITH ROLLBACK IMMEDIATE;';
    -- RESTORE 会回到快照当时的 MULTI_USER：同一个动态语句里紧接着重新 SINGLE_USER，不给 U8 应用服务器的池连接留出写入的空档。
    -- 末尾这句失败时库停在多用户，别人可能已经写入：下一轮连 RESTORE 一起重做，把这些写入也还原掉，不只是重新占回单用户。
    IF @restore_snapshot IS NOT NULL
        SET @step = @again + N' RESTORE DATABASE ' + QUOTENAME(@db) + N' FROM DATABASE_SNAPSHOT = '
            + QUOTENAME(@restore_snapshot, N'''') + N'; ' + @again;
    ELSE IF @user_access = N'SINGLE_USER'
        SET @step = @again;
    ELSE IF @user_access = N'MULTI_USER'
        SET @step = N'ALTER DATABASE ' + QUOTENAME(@db) + N' SET MULTI_USER;';
    WHILE 1 = 1
    BEGIN
        SET @tries += 1;
        -- 单用户槽位按库级锁算：当前库不是它、但正跨库读写它的会话也占着，只看 database_id 会漏掉，SINGLE_USER 一直等不到。
        DECLARE victims CURSOR LOCAL FAST_FORWARD FOR
            SELECT s.session_id,
                   LEFT(N'会话 ' + CONVERT(nvarchar(11), s.session_id) + N' 主机 ' + ISNULL(s.host_name, N'-')
                        + N' 程序 ' + ISNULL(s.program_name, N'-') + N' 登录 ' + ISNULL(s.login_name, N'-'), 400),
                   LEFT(N' 主机 ' + ISNULL(s.host_name, N'-') + N' 程序 ' + ISNULL(s.program_name, N'-')
                        + N' 登录 ' + ISNULL(s.login_name, N'-'), 400)
            FROM sys.dm_exec_sessions AS s
            WHERE s.session_id <> @me AND s.is_user_process = 1
              AND (s.database_id = @id
                   OR EXISTS (SELECT 1 FROM sys.dm_tran_locks AS l
                              WHERE l.request_session_id = s.session_id AND l.resource_type = N'DATABASE'
                                AND l.resource_database_id = @id));
        OPEN victims;
        FETCH NEXT FROM victims INTO @spid, @tag, @key;
        WHILE @@FETCH_STATUS = 0
        BEGIN
            SET @kill = N'KILL ' + CONVERT(nvarchar(11), @spid) + N';';
            BEGIN TRY
                EXEC (@kill);
                SET @n += 1;
                -- 去掉换行和分隔符，日志一行一条；同一主机、程序、登录只记第一个会话（重试时连接池反复重连）；放不下的只记个数。
                SET @tag = REPLACE(REPLACE(REPLACE(@tag, NCHAR(31), N' '), NCHAR(13), N' '), NCHAR(10), N' ');
                SET @key = REPLACE(REPLACE(REPLACE(@key, NCHAR(31), N' '), NCHAR(13), N' '), NCHAR(10), N' ');
                IF CHARINDEX(@key + N'；', @who + N'；') = 0 AND LEN(@who) + LEN(@tag) < 990
                    SET @who = @who + CASE WHEN @who = N'' THEN N'' ELSE N'；' END + @tag;
            END TRY
            BEGIN CATCH
                -- 会话已经自己断开（6106 等）：不算，继续下一个。
            END CATCH;
            FETCH NEXT FROM victims INTO @spid, @tag, @key;
        END;
        CLOSE victims;
        DEALLOCATE victims;
        IF @step IS NULL BREAK;
        -- 结束占用会话之后马上执行，不停顿：U8 应用服务器的连接池约 1 秒内就会重连。
        SET @err = 0;
        BEGIN TRY
            EXEC (@step);
        END TRY
        BEGIN CATCH
            SET @err = ERROR_NUMBER();
            SET @msg = LEFT(REPLACE(REPLACE(ERROR_MESSAGE(), NCHAR(13), N' '), NCHAR(10), N' '), 400);
        END CATCH;
        IF @err = 0 BREAK;
        IF @tries >= @limit OR SYSDATETIME() >= @deadline
        BEGIN
            IF @phase = 2 OR @user_access = N'MULTI_USER' BREAK;
            -- 用尽：库不能停在被别人占着的 SINGLE_USER（调用方重来时整批连编译都过不去），最后几轮改为放开多用户。
            SET @fail = @err;
            SET @failMsg = @msg;
            SET @phase = 2;
            SET @step = N'ALTER DATABASE ' + QUOTENAME(@db) + N' SET MULTI_USER;';
            SET @limit = @tries + 5;
            SET @deadline = DATEADD(SECOND, 15, SYSDATETIME());
            CONTINUE;
        END;
        WAITFOR DELAY '00:00:00.100';
    END;
    IF @phase = 2
    BEGIN
        SET @err = @fail;
        SET @msg = @failMsg;
    END;
    IF @err <> 0
    BEGIN
        RAISERROR(N'u8co_release_db %s：重试 %d 次仍被占用，最后错误 %d：%s 占用会话：%s', 16, 1, @db, @tries, @err, @msg, @who);
        RETURN -2;
    END;
    RETURN @n;
END;
GO

-- 4. 证书、签名登录、签名、删私钥、通道登录的 EXECUTE。口令只在这一批的变量里。
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @pw nvarchar(128) = CONVERT(nvarchar(36), NEWID()) + N'aZ9!' + CONVERT(nvarchar(36), NEWID());
DECLARE @sql nvarchar(max);
SET @sql = N'CREATE CERTIFICATE [u8co_release_cert] ENCRYPTION BY PASSWORD = ' + QUOTENAME(@pw, N'''')
    + N' WITH SUBJECT = N''u8co_release_db signer'', EXPIRY_DATE = ''20991231'';';
EXEC (@sql);
CREATE LOGIN [u8co_release_signer] FROM CERTIFICATE [u8co_release_cert];
GRANT ALTER ANY CONNECTION TO [u8co_release_signer];
GRANT VIEW SERVER STATE TO [u8co_release_signer];
SET @sql = N'ADD SIGNATURE TO dbo.u8co_release_db BY CERTIFICATE [u8co_release_cert] WITH PASSWORD = '
    + QUOTENAME(@pw, N'''') + N';';
EXEC (@sql);
SET @pw = NULL;
SET @sql = NULL;
ALTER CERTIFICATE [u8co_release_cert] REMOVE PRIVATE KEY;

DECLARE @login sysname, @user sysname;
DECLARE lanes CURSOR LOCAL FAST_FORWARD FOR SELECT name FROM #lane;
OPEN lanes;
FETCH NEXT FROM lanes INTO @login;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @user = (SELECT name FROM sys.database_principals WHERE sid = SUSER_SID(@login));
    IF @user IS NULL
    BEGIN
        SET @user = @login;
        SET @sql = N'CREATE USER ' + QUOTENAME(@user) + N' FOR LOGIN ' + QUOTENAME(@login) + N';';
        EXEC (@sql);
    END;
    SET @sql = N'GRANT EXECUTE ON dbo.u8co_release_db TO ' + QUOTENAME(@user) + N';';
    EXEC (@sql);
    FETCH NEXT FROM lanes INTO @login;
END;
CLOSE lanes;
DEALLOCATE lanes;
GO

-- 5. 核对（只读）：
--    cert 一行，private_key 为 NO_PRIVATE_KEY；signature 一行，签名证书是 u8co_release_cert；
--    signer_perm 有 ALTER ANY CONNECTION、VIEW SERVER STATE，另有建登录时自带的 CONNECT SQL（证书登录不能连接，无害），
--    不应有别的；execute 每个通道登录一行；
--    owned_db 列出每个通道登录拥有的库（不含快照）——授权只看库的所有者，这就是它能结束会话的全部范围
--    （过程另外只接受 UFDATA_ / UFMeta_ 开头的）。应只有这个通道自己的测试账套库；有别的库，先改那个库的所有者。
SELECT 'cert' AS t, name, pvt_key_encryption_type_desc AS private_key, CONVERT(varchar(10), expiry_date, 23) AS expiry
FROM sys.certificates WHERE name = N'u8co_release_cert';
SELECT 'signature' AS t, OBJECT_NAME(cp.major_id) AS proc_name, c.name AS cert
FROM sys.crypt_properties AS cp JOIN sys.certificates AS c ON c.thumbprint = cp.thumbprint
WHERE cp.major_id = OBJECT_ID(N'dbo.u8co_release_db');
SELECT 'signer_perm' AS t, sp.permission_name, sp.state_desc
FROM sys.server_permissions AS sp JOIN sys.server_principals AS p ON p.principal_id = sp.grantee_principal_id
WHERE p.name = N'u8co_release_signer' ORDER BY sp.permission_name;
SELECT 'execute' AS t, dp.name AS grantee, SUSER_SNAME(dp.sid) AS login, pe.state_desc
FROM sys.database_permissions AS pe JOIN sys.database_principals AS dp ON dp.principal_id = pe.grantee_principal_id
WHERE pe.major_id = OBJECT_ID(N'dbo.u8co_release_db') AND pe.permission_name = N'EXECUTE'
ORDER BY dp.name;
SELECT 'owned_db' AS t, l.name AS login, d.name AS db
FROM #lane AS l JOIN sys.databases AS d ON d.owner_sid = SUSER_SID(l.name) AND d.source_database_id IS NULL
ORDER BY l.name, d.name;
GO
