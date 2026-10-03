using System;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 事务策略只放在这里。测试账上看过审核前后的 @@TRANCOUNT 之后，改这一处即可。
    internal static class CoTrans
    {
        const int NoTransaction = unchecked((int)0x8004D00E);

        // 预演（DryRun）结束之后不能再开事务：再抛同一个预演结果。
        public static void Begin(object conn)
        {
            DryRun.BeforeBegin();
            ComUtil.Call(conn, "BeginTrans", new object[0]);
        }

        public static string Count(object conn)
        {
            return AdoXml.QueryInt(conn, "SELECT @@TRANCOUNT").ToString();
        }

        public static void LockWait(object conn)
        {
            // 新连接上回读时最多等 10 秒，不把未提交的行当成已审核。
            object rs = null;
            try
            {
                rs = ComUtil.Call(conn, "Execute", new object[] { "SET LOCK_TIMEOUT 10000" });
            }
            finally
            {
                ComUtil.Final(rs);
            }
        }

        // 预演时不提交：DryRun.AtCommit 核对 @@TRANCOUNT、回读、回滚，返回要抛的结果（DryRunRun.Close）。
        public static void Commit(object conn)
        {
            if (DryRun.Active)
            {
                throw DryRun.AtCommit(conn);
            }
            ComUtil.Call(conn, "CommitTrans", new object[0]);
            RequireClear(conn);
        }

        // CO 自己结束事务时 CommitTrans 会报 0x8004D00E。这时当成已经提交，再要求没有残留事务。
        public static void CommitSeen(object conn)
        {
            if (DryRun.Active)
            {
                throw DryRun.AtCommit(conn);
            }
            try
            {
                ComUtil.Call(conn, "CommitTrans", new object[0]);
            }
            catch (Exception ex)
            {
                if (!IsNoTransaction(ex))
                {
                    throw;
                }
            }
            RequireClear(conn);
        }

        // 预演结束之后（钩子已回滚）处理函数的回滚一律不报错。
        public static void Rollback(object conn)
        {
            if (DryRun.Finished)
            {
                Quiet(conn);
                return;
            }
            try
            {
                ComUtil.Call(conn, "RollbackTrans", new object[0]);
            }
            catch (Exception ex)
            {
                if (IsNoTransaction(ex))
                {
                    return;
                }
                throw;
            }
        }

        static void Quiet(object conn)
        {
            try
            {
                ComUtil.Call(conn, "RollbackTrans", new object[0]);
            }
            catch (Exception)
            {
            }
        }

        public static bool IsNoTransaction(Exception ex)
        {
            Exception cur = ex;
            while (cur != null)
            {
                COMException com = cur as COMException;
                if (com != null && com.ErrorCode == NoTransaction)
                {
                    return true;
                }
                cur = cur.InnerException;
            }
            return false;
        }

        static void RequireClear(object conn)
        {
            int left = AdoXml.QueryInt(conn, "SELECT @@TRANCOUNT");
            if (left != 0)
            {
                throw new BridgeException(500, "internal", "提交后 @@TRANCOUNT=" + left.ToString());
            }
        }
    }
}
