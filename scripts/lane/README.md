# 签名还原过程（可选）

自动回归 `co/e2e` 的全貌和前提见 [`docs/testing.md`](../../docs/testing.md) 第 4 节。这里只放每台 SQL 实例由 sysadmin 跑一次的脚本，用来让快照还原不被 U8 应用服务器的连接池卡住。**可选**：没装也能跑回归，预检只给警告，还原照旧靠重试。

下文的「通道登录」指运行回归的 Windows 登录（配置里的 `server.windows_principal`，不写时是 `server.ssh_user` 对应的 Windows 身份）；一个测试账套配一个登录，只拥有自己的两个库。

| 脚本 | 做什么 |
| --- | --- |
| `install-release-proc.sql` | 在 `master` 里装签名过程 `dbo.u8co_release_db`，并给通道登录 `EXECUTE` |
| `uninstall-release-proc.sql` | 删掉这个过程、签名登录和证书 |

## 为什么要 `u8co_release_db`

快照还原的顺序是 `SET SINGLE_USER WITH ROLLBACK IMMEDIATE` → `RESTORE … FROM DATABASE_SNAPSHOT` → `SET MULTI_USER`。U8 应用服务器以 `sa` 连测试账套库时，用的是 .Net SqlClient 连接池；`SINGLE_USER` 之后，池里的连接常常抢先连上，占住唯一的连接。通道登录是库的所有者，但故意不是 sysadmin，不能 `KILL`。于是 `RESTORE` 和 `SET MULTI_USER` 失败（3101、5064、1205），库停在 `SINGLE_USER`，只能等池连接空闲超时，或者请 DBA 手工 `KILL`。

装了这个过程之后，`co/e2e/remote/snapshot-revert.ps1` 在每个 `SET SINGLE_USER`、`RESTORE`、`USE`、`SET MULTI_USER` 之前，在同一批、同一连接里执行：

```sql
IF OBJECT_ID(N'master.dbo.u8co_release_db') IS NOT NULL
BEGIN BEGIN TRY EXEC master.dbo.u8co_release_db N'<库名>'; END TRY BEGIN CATCH END CATCH; END;
```

（示意；实际语句先按所有者和库名守卫，过程有 `@who` 参数时带上它，见 `Get-ReleaseSql`。）

结束占用的会话和紧跟的 `ALTER` 之间的间隔最短。过程不在、没有权限或拒绝时静默跳过，还原照旧靠重试（每 2–5 秒，最多约 3 分钟）。

过程结束两类会话：当前库就是该库的；以及当前库不是它、但在它上面持有库级锁的（`sys.dm_tran_locks` 的 `DATABASE` 锁）。后一类是跨库读写它的连接，例如 U8 应用服务器上的任务服务连在 `UFSystem`、每分钟重连并轮询各账套库；只看 `database_id` 找不到它，白天还原就一直等不到单用户槽位。可选的输出参数 `@who` 带回被结束会话的会话号、主机、程序和登录，还原日志记成「u8co_release_db <库> 结束会话 N 个：会话 57 主机 … 程序 … 登录 …」；重试用尽仍被占住时，报错里也带上最近结束过的会话，便于找 DBA 处理。服务器上还是旧版过程（没有 `@who`）时，还原脚本照旧按旧的调用方式执行；要用上新的结束范围和日志，sysadmin 重跑一次本安装脚本。

### 服务器端紧凑重试（`@restore_snapshot` / `@user_access`）

有些环境里，U8 应用服务器日夜轮询测试账套库，池连接被结束后约 1 秒就重连。上面那种「结束会话」和「`ALTER`」分属客户端两次往返的做法抢不过，还原约 3 分钟后失败。现在的过程多了三个可选参数：

| 参数 | 作用 |
| --- | --- |
| `@restore_snapshot sysname` | 每轮：结束占用会话 → 紧接着同一个动态语句 `SET SINGLE_USER WITH ROLLBACK IMMEDIATE; RESTORE … FROM DATABASE_SNAPSHOT`；成功后用同样的循环重新 `SET SINGLE_USER`（`RESTORE` 会回到 MULTI_USER）。快照必须是 `@db` 自己的 |
| `@user_access nvarchar(20)` | `SINGLE_USER` 或 `MULTI_USER`：同样的循环只改用户访问模式。不能和 `@restore_snapshot` 同给 |
| `@tries int OUTPUT` | 带回尝试次数 |

循环最多 60 轮、约 90 秒，失败后只停 0.1 秒；过程里 `DEADLOCK_PRIORITY HIGH`、`LOCK_TIMEOUT` 10 秒。用尽时先再试几次放开多用户，然后 `RAISERROR`「u8co_release_db <库>：重试 N 次仍被占用，最后错误 <号>：<原文> 占用会话：…」并返回 -2；拒绝返回 -1。`ALTER DATABASE` 和 `RESTORE` 仍用调用者作为库所有者的权限，证书不补这两项。

`snapshot-revert.ps1` 先查过程有没有 `@restore_snapshot` 参数：有就用它还原，回到批里马上 `USE` 该库占住唯一的连接（被抢先就用 `@user_access = N'SINGLE_USER'` 再占，最多 20 轮），之后照旧导出任务表、算同会话指纹、放开多用户；放开多用户也用 `@user_access = N'MULTI_USER'`。没有这个参数（旧版过程）或没装时按原来的批执行。升级：sysadmin 重跑本安装脚本。

## 权限是怎么收窄的

- 通道登录只拿到 `master.dbo.u8co_release_db` 的 `EXECUTE`，不进任何服务器角色。
- `KILL` 要的权限在证书登录 `u8co_release_signer` 上：`ALTER ANY CONNECTION`，以及 `VIEW SERVER STATE`（没有它 `sys.dm_exec_sessions` 只看得到自己的会话，找不到要结束的会话）。这个登录从证书建，不能连接；权限只经过证书签名的过程生效。
- 过程只接受 `UFDATA_` / `UFMeta_` 开头、不是快照、**所有者正是调用者**（`owner_sid = SUSER_SID(ORIGINAL_LOGIN())`）的库，否则报错拒绝。所以一个通道登录只能结束占住自己两个库的会话，碰不到正式账套和别的测试账套。
- 证书的口令在脚本里随机生成，不打印、不保存。签名之后立刻 `REMOVE PRIVATE KEY`，谁也不能再用这张证书给别的代码签名。改过程只能重跑安装脚本，它会换一张新证书重新签。

## 安装

1. 先按 `co/e2e/remote/grant-lane.sql.example` 给每个测试账套建好登录、改好库的所有者。
2. 改 `install-release-proc.sql` 顶部的 `LaneLogins`：这台实例上全部通道登录，逗号分隔（例如 `HOST01\u8lane1,HOST01\u8lane2`）。`:setvar` 优先于 `sqlcmd -v`，要改文件里的值。
3. 以 sysadmin 执行：

   ```bash
   sqlcmd -S <SQL 实例> -E -b -f 65001 -i install-release-proc.sql
   ```

   脚本拒绝：不是 sysadmin、`LaneLogins` 还是占位值、登录不存在、登录是 sysadmin。
4. 看末尾的核对：`cert` 的 `private_key` 是 `NO_PRIVATE_KEY`；`signature` 一行；`signer_perm` 有 `ALTER ANY CONNECTION` 和 `VIEW SERVER STATE`（另有建登录自带的 `CONNECT SQL`）；`execute` 每个通道登录一行；`owned_db` 列出每个通道登录拥有的库（不含快照），应只有它自己的测试账套库——过程的授权只看库的所有者。
5. 下一次 `session open` 或 `run_e2e` 的预检报告里，`u8co_release_db` 的警告应消失（预检用 `HAS_PERMS_BY_NAME` 查通道登录能不能执行它；不能时只警告，不拦）。

加了新的测试账套和登录：把全部通道登录写上，重跑一次。重跑会删掉旧的过程、签名登录和证书再重建，`master` 里已有的通道用户沿用。

## 撤销

```bash
sqlcmd -S <SQL 实例> -E -b -f 65001 -i uninstall-release-proc.sql
```

它删掉过程（连同签名和授权）、签名登录和证书，不删 `master` 里通道登录的用户（安装时可能沿用了原有用户，分不清）；需要时手工 `DROP USER`。撤销之后回归照旧能跑，只是回到等待重试。
