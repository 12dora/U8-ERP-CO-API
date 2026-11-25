-- uninstall-release-proc.sql — 撤销 install-release-proc.sql：删掉 master 里的 dbo.u8co_release_db、签名登录 u8co_release_signer
-- 和证书 u8co_release_cert。删过程会连同它的签名和 EXECUTE 授权一起删掉。
--
-- 不删 master 里通道登录的用户：安装时可能沿用了原来就有的用户，脚本分不清。确实不再需要时由 sysadmin 手工
--   DROP USER（先确认它在 master 里没有别的权限）。
-- 撤销之后 e2e 照旧能还原，只是单用户槽位被别的连接占住时只能等待重试；预检报告会出现 u8co_release_db 警告。
--
-- 用法（sysadmin）：
--   sqlcmd -S <SQL 实例> -E -b -f 65001 -i uninstall-release-proc.sql
-- 可以重跑：不存在的跳过。

:on error exit

SET NOCOUNT ON;
USE [master];
GO

IF ISNULL(IS_SRVROLEMEMBER(N'sysadmin'), 0) <> 1
BEGIN RAISERROR(N'要用 sysadmin 登录执行。', 16, 1); RETURN; END;
GO

IF OBJECT_ID(N'dbo.u8co_release_db', N'P') IS NOT NULL DROP PROCEDURE dbo.u8co_release_db;
IF SUSER_ID(N'u8co_release_signer') IS NOT NULL DROP LOGIN [u8co_release_signer];
IF CERT_ID(N'u8co_release_cert') IS NOT NULL DROP CERTIFICATE [u8co_release_cert];
GO

-- 核对（只读）：三项都应为 0。
SELECT CASE WHEN OBJECT_ID(N'dbo.u8co_release_db', N'P') IS NULL THEN 0 ELSE 1 END AS proc_left,
       CASE WHEN SUSER_ID(N'u8co_release_signer') IS NULL THEN 0 ELSE 1 END AS login_left,
       CASE WHEN CERT_ID(N'u8co_release_cert') IS NULL THEN 0 ELSE 1 END AS cert_left;
GO
