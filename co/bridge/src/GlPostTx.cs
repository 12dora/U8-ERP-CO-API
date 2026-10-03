using System;
using System.Collections.Generic;
using System.Data;
using System.Globalization;
using System.Transactions;

namespace U8Co
{
    // 记账事务：一个 System.Transactions.TransactionScope，ReadCommitted，超时 5 分钟。
    // 实测 U8 记账加入外层环境事务时不要求隔离级别一致、也不另起计时器，
    // 所以外层不必用 Serializable；独立运行时 U8 自己的作用域是 Serializable、缺省 60 秒，外层超时取 5 分钟（≥ 60 秒，
    // 且在 machine.config 缺省的 MaximumTimeout 10 分钟之内），比命令超时（GlPostSql 120 秒、U8 DAL 缺省 30 秒）都长。
    // 并发由锁保证：第一条语句给 GL_mpostcond1 加表级 U 锁（UPDLOCK, HOLDLOCK, TABLOCK）直到提交，U8 客户端的汇总
    // （GL_P_JZA 先删 GL_mpostcond1）等我们提交。本年度留在 GL_mpostcond1 里、还没记账的旧汇总范围（有人汇总后没记，
    // 分不清是放弃了还是正要记）同 U8 自己的汇总一样被覆盖；记完后在同一事务里清掉本年度的范围，别人之后再点「记账」
    // 也记不到我们这批凭证（U8 的科目总账汇总不查 ibook，重复记账会重复累加发生额）。快照读 GL_accvouch 也带
    // UPDLOCK, HOLDLOCK（GlPostSnap）。U8 的 DAL 每条语句新开 SqlConnection(ConnString4Dn) 再关，桥自己的读写（GlPostSql）
    // 用同一个串、也是依次开关，全程复用一条已登记的连接，不升级 MSDTC；Complete 之前核对事务仍是本地事务，不是就抛、回滚、503。
    // 顺序：锁 GL_mpostcond1 → VouchPostGather（汇总记账范围）→ 核对 GL_mpostcond1 本年度的范围与请求完全一致
    // → 快照 U8 回写 ibook 时会误碰的其他凭证行 → VouchPostAll → 把误碰的行改回 → 核对 → 清本年度范围 → 核对本地事务 → Complete。
    // Complete 之前任何异常都回滚，什么都不留（错误归类见 GlPostErr）；提交时被中止 503，结果未知 504。
    internal static class GlPostTx
    {
        static readonly TimeSpan ScopeTimeout = TimeSpan.FromMinutes(5);
        const string MineSql = "SELECT iperiod, isignseq, ino_id FROM GL_mpostcond1 WHERE iyear=@y";
        const string LockSql = "SELECT COUNT(*) n FROM GL_mpostcond1 WITH (UPDLOCK, HOLDLOCK, TABLOCK)";
        const string ClearSql = "DELETE FROM GL_mpostcond1 WHERE iyear=@y";

        public static void Run(GlPostNet net, string conn, GlPostReq req)
        {
            TransactionOptions options = new TransactionOptions();
            options.IsolationLevel = System.Transactions.IsolationLevel.ReadCommitted;
            options.Timeout = ScopeTimeout;
            bool done = false;
            try
            {
                using (TransactionScope scope = new TransactionScope(TransactionScopeOption.Required, options))
                {
                    Work(net, conn, req);
                    Local();
                    scope.Complete();
                    done = true;
                }
            }
            catch (Exception ex)
            {
                throw done ? GlPostErr.Commit(ex) : GlPostErr.Before(ex);
            }
        }

        static void Work(GlPostNet net, string conn, GlPostReq req)
        {
            GlPostSql.Rows(conn, LockSql, null);
            DataSet gathered;
            try
            {
                gathered = net.Gather(req);
            }
            catch (Exception ex)
            {
                throw GlPostErr.U8(ex);
            }
            Gathered(conn, req, gathered);
            List<GlPostRow> snap = GlPostSnap.Take(conn, req);
            try
            {
                net.PostAll(req);
            }
            catch (Exception ex)
            {
                throw GlPostErr.U8(ex);
            }
            GlPostSnap.Restore(conn, snap, req.Poster);
            GlPostSnap.Verify(conn, req, snap);
            GlPostSql.Exec(conn, ClearSql, new[] { GlPostSql.P("@y", req.Year) });
        }

        // 轻量事务升级成 MSDTC 分布式事务说明有第二条连接登记进来了（不该发生）：不提交，回滚后 503。
        static void Local()
        {
            Transaction current = Transaction.Current;
            if (current == null)
            {
                throw new BridgeException(500, "internal", "记账时没有环境事务，已回滚，未写入");
            }
            if (current.TransactionInformation.DistributedIdentifier != Guid.Empty)
            {
                throw GlPostErr.Escalated();
            }
        }

        // GL_mpostcond1 里本年度的行必须正好是请求的凭证（GL_P_JZA 会静默跳过不合格的凭证）。
        static void Gathered(string conn, GlPostReq req, DataSet gathered)
        {
            HashSet<string> got = new HashSet<string>(StringComparer.Ordinal);
            int extra = 0;
            foreach (object[] row in GlPostSql.Rows(conn, MineSql, new[] { GlPostSql.P("@y", req.Year) }))
            {
                int period = GlPostSql.Int(row[0]);
                int seq = GlPostSql.Int(row[1]);
                int no = GlPostSql.Int(row[2]);
                if (req.Has(req.Year, period, seq, no))
                {
                    got.Add(GlPostReq.Pair(seq, no));
                }
                else
                {
                    extra++;
                }
            }
            List<string> missing = new List<string>();
            foreach (GlPostItem item in req.Items)
            {
                if (!got.Contains(GlPostReq.Pair(item.Seq, item.No)))
                {
                    missing.Add(item.Sign + "-" + item.No.ToString(CultureInfo.InvariantCulture) + Reason(gathered, item));
                }
            }
            if (missing.Count > 0)
            {
                throw GlState.Refuse("U8 没有把这些凭证汇总进记账范围，未记账：" + string.Join("；", missing.ToArray()));
            }
            if (extra > 0)
            {
                throw GlState.Refuse("U8 汇总的记账范围多出 " + extra.ToString(CultureInfo.InvariantCulture) + " 张请求之外的凭证，未记账");
            }
        }

        // VouchPostGather 返回的 Table3 是范围内未记账的凭证，照 UCPostError 给原因。
        static string Reason(DataSet gathered, GlPostItem item)
        {
            DataTable table = gathered == null ? null : gathered.Tables["Table3"];
            if (table == null)
            {
                return "";
            }
            foreach (DataRow row in table.Rows)
            {
                if (Cell(row, "csign").Trim() != item.Sign || Cell(row, "ino_id").Trim() != item.No.ToString(CultureInfo.InvariantCulture))
                {
                    continue;
                }
                if (Cell(row, "iflag").Trim() == "2")
                {
                    return "（错误凭证）";
                }
                if (Cell(row, "ccheck").Trim().Length == 0)
                {
                    return "（未审核凭证）";
                }
                return Cell(row, "cMaster").Trim().Length == 0 ? "（主管会计未签字）" : "（出纳未签字）";
            }
            return "";
        }

        static string Cell(DataRow row, string name)
        {
            return row.Table.Columns.Contains(name) ? GlPostSql.Str(row[name], false) : "";
        }

        // U8 和 SQL Server 的消息只取第一行，去掉 .NET 堆栈。
        internal static string FirstLine(string message)
        {
            string text = (message ?? "").Trim();
            int cut = text.IndexOfAny(new char[] { '\r', '\n' });
            if (cut >= 0)
            {
                text = text.Substring(0, cut).Trim();
            }
            return text.Length > 300 ? text.Substring(0, 300) : text;
        }
    }
}
