using System;
using System.Data.SqlClient;
using System.Transactions;

namespace U8Co
{
    // 记账事务的异常分类（提交前的都已回滚、未写入）：
    // - 死锁牺牲品（1205）、锁请求超时（1222）、命令超时（SqlClient -2）、TimeoutException、事务已中止或事务层出错
    //   （TransactionException，含事务超时、升级 MSDTC 失败）：503，可以稍后重试；
    // - 桥自己的 SQL（GlPostSql）其余失败：500 internal；U8 组件里的 SQL 其余失败：500 internal（写明是 U8 组件）；
    // - U8 组件抛的其他异常：409 u8_rejected（U8 原文第一行）；桥自己的拒绝（BridgeException）原样。
    // 提交（scope.Dispose）时：TransactionAbortedException 说明已回滚，503；TransactionInDoubtException 和其他异常结果未知，504。
    internal static class GlPostErr
    {
        static readonly int[] RetryNumbers = new int[] { 1205, 1222, -2 };

        const string RetryTail = "，已回滚，未写入，可以稍后重试";

        // 桥自己的 SqlClient 读写失败（GlPostSql.Rows / Exec）。
        public static Exception Own(Exception ex)
        {
            Exception known = Known(ex);
            if (known != null)
            {
                return known;
            }
            return new BridgeException(500, "internal", "桥的记账 SQL 出错，已回滚，未写入：" + GlPostTx.FirstLine(ex.Message));
        }

        // U8 总账组件（VouchPostGather、VouchPostAll）抛的异常。
        public static Exception U8(Exception ex)
        {
            Exception known = Known(ex);
            if (known != null)
            {
                return known;
            }
            if (Find<SqlException>(ex) != null)
            {
                return new BridgeException(500, "internal", "U8 记账组件的 SQL 出错，已回滚，未写入：" + GlPostTx.FirstLine(ex.Message));
            }
            return new BridgeException(409, "u8_rejected", "U8 记账失败，未写入：" + GlPostTx.FirstLine(ex.Message));
        }

        // 事务作用域外、Complete 之前冒出来的异常（没经过上面两个入口的，或者 Dispose 回滚时又抛的）。
        public static Exception Before(Exception ex)
        {
            Exception known = Known(ex);
            if (known != null)
            {
                return known;
            }
            return new BridgeException(500, "internal", "记账出错，已回滚，未写入：" + GlPostTx.FirstLine(ex.Message));
        }

        // scope.Complete() 之后、Dispose 提交时的异常。
        public static Exception Commit(Exception ex)
        {
            if (ex is TransactionAbortedException)
            {
                return new BridgeException(503, "u8_unavailable", "记账事务提交时被中止（" + GlPostTx.FirstLine(ex.Message) + "）" + RetryTail);
            }
            return new BridgeException(504, "outcome_unknown", "记账事务提交时出错（" + GlPostTx.FirstLine(ex.Message)
                + "），结果未知：请先用 gl/vouchers/load 核对再决定是否重试");
        }

        // 桥自己的拒绝原样；升级 MSDTC 失败、可重试的中止映射成 503；其余返回 null 交给调用方。
        static Exception Known(Exception ex)
        {
            if (ex is BridgeException)
            {
                return ex;
            }
            if (Find<TransactionManagerCommunicationException>(ex) != null)
            {
                return Escalated();
            }
            return Retryable(ex) ? Busy(ex) : null;
        }

        public static BridgeException Escalated()
        {
            return new BridgeException(503, "u8_unavailable", "记账事务升级为分布式事务，已回滚，未写入");
        }

        internal static bool Retryable(Exception ex)
        {
            if (Find<TimeoutException>(ex) != null || Find<TransactionException>(ex) != null || Aborted())
            {
                return true;
            }
            SqlException sql = Find<SqlException>(ex);
            if (sql == null)
            {
                return false;
            }
            foreach (SqlError error in sql.Errors)
            {
                if (Array.IndexOf(RetryNumbers, error.Number) >= 0)
                {
                    return true;
                }
            }
            return false;
        }

        static BridgeException Busy(Exception ex)
        {
            return new BridgeException(503, "u8_unavailable", "记账事务被中止（死锁、锁等待超时或执行超时："
                + GlPostTx.FirstLine(ex.Message) + "）" + RetryTail);
        }

        // 当前环境事务已中止（事务超时、服务器回滚）：之后的 SQL 报什么都按可重试。
        static bool Aborted()
        {
            try
            {
                Transaction current = Transaction.Current;
                return current != null && current.TransactionInformation.Status == TransactionStatus.Aborted;
            }
            catch (ObjectDisposedException)
            {
                return false;
            }
            catch (TransactionException)
            {
                return true;
            }
        }

        static T Find<T>(Exception ex) where T : Exception
        {
            for (Exception e = ex; e != null; e = e.InnerException)
            {
                T hit = e as T;
                if (hit != null)
                {
                    return hit;
                }
            }
            return null;
        }
    }
}
