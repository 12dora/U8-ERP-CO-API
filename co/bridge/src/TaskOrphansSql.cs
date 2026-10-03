using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // TaskOrphans 的 SQL。只用 ? 参数；IN 列表的占位个数来自本地的任务号个数，不拼调用方文本。
    // 参数按 Unicode 字符串绑定（adVarWChar）。现场核对的列类型：UA_TaskLog.cTaskId nvarchar(20)、cStation nvarchar(255)、
    // cSub_Id nvarchar(2)、dInTime datetime，都是 nvarchar，参数与列同型，不会因隐式转换改成扫表。
    internal static class TaskOrphansSql
    {
        // 快照最多读这么多行；超过就不清理（快照不全，分不清新旧）。
        const int SnapCap = 2000;
        const int CandCap = 50;
        // ua_Task_Common 行多于这么多时看不全，一个都不清。
        const int OwnerCap = 500;
        // 只清 QM：插件只被证实会开 QM 登录。要加子系统先实测。
        static readonly string[] Subs = new string[] { "QM" };
        // UFSYSTEM..UA_TaskLog 的登录时间列（现场核对：dInTime；该表没有操作员和账套列）。
        const string LoginTime = "dInTime";

        const string NowSql = "SELECT CONVERT(varchar(23), GETDATE(), 121) AS t";
        const string SnapSql = "SELECT TOP (?) CONVERT(nvarchar(100), cTaskId) AS id"
            + " FROM UFSYSTEM..UA_TaskLog WHERE cStation=?";
        const string CandSql = "SELECT TOP (?) CONVERT(nvarchar(100), cTaskId) AS id FROM UFSYSTEM..UA_TaskLog"
            + " WHERE cStation=? AND " + LoginTime + ">=CONVERT(datetime, ?, 121)"
            + " AND " + LoginTime + "<=CONVERT(datetime, ?, 121) AND cSub_Id IN ";
        // ua_Task_Common 有行时：操作员、账套必须是本次请求的（UA_TaskLog 没有这两列）。在删除事务里按行锁住再读。
        const string OwnerSql = "SELECT CONVERT(nvarchar(100), cTaskId) AS id, CONVERT(nvarchar(100), cUser_Id) AS u,"
            + " CONVERT(nvarchar(100), cAcc_Id) AS a FROM UFSYSTEM..ua_Task_Common WITH (UPDLOCK, ROWLOCK)"
            + " WHERE cStation=? AND cTaskId IN ";
        // 按行锁住这些任务号的全部行（不按子系统过滤），不用 HOLDLOCK，免得范围锁挡住别人的 U8 登录。
        const string LockSql = "SELECT COUNT(*) AS n FROM UFSYSTEM..UA_TaskLog WITH (UPDLOCK, ROWLOCK)"
            + " WHERE cStation=? AND cTaskId IN ";
        const string SubCountSql = "SELECT COUNT(*) AS n FROM UFSYSTEM..UA_TaskLog WHERE cStation=? AND cSub_Id IN ";
        const string DelCommonSql = "DELETE FROM UFSYSTEM..ua_Task_Common WHERE cStation=? AND cTaskId IN ";
        const string DelLogSql = "DELETE FROM UFSYSTEM..UA_TaskLog WHERE cStation=? AND cSub_Id IN ";
        // 清理连接上：死锁时让自己当牺牲者，等锁最多 2 秒（覆盖 OpenFresh 的 10 秒）。
        const string QuietSql = "SET DEADLOCK_PRIORITY LOW; SET LOCK_TIMEOUT 2000";

        public static string SubText()
        {
            return string.Join(",", Subs);
        }

        public static string Now(object conn)
        {
            string now = Rows.Scalar(conn, NowSql, new object[0]);
            if (now == null || now.Trim().Length == 0)
            {
                throw new InvalidOperationException("取不到数据库时间");
            }
            return now.Trim();
        }

        // 本机现有任务号。行数超过上限返回 null；查询失败直接抛出（由调用方记 orphan_tasks_failed，不清理）。
        public static HashSet<string> TaskIds(object conn, string station)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, SnapSql, new object[] { SnapCap + 1, station }, SnapCap + 1);
            if (rows.Count > SnapCap) return null;
            HashSet<string> ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                ids.Add(Key(Cell(rows[i], "id")));
            }
            return ids;
        }

        // 窗口内新出现的本机 QM 任务号（原样返回，删除时按原值比）。行数到上限返回 null。操作员和账套在 Delete 里核对。
        public static List<string> Candidates(object conn, OrphanScan scan)
        {
            object[] args = Args(new object[] { CandCap + 1, scan.Station, scan.Since, scan.Until }, Subs, null);
            string sql = CandSql + In(Subs.Length);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, args, CandCap + 1);
            if (rows.Count > CandCap) return null;
            List<string> ids = new List<string>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                string id = Cell(rows[i], "id");
                if (id == null || Key(id).Length == 0 || scan.Before.Contains(Key(id))) continue;
                if (seen.Add(Key(id))) ids.Add(id);
            }
            return ids;
        }

        // 独立的新连接、一个短事务：按行锁住这些任务号，核对只有 QM 行、行数与候选数一致；
        // 再锁住读 ua_Task_Common，去掉属于别的操作员或账套的候选（Foreign 记个数）；
        // 最后删剩下的 ua_Task_Common、UA_TaskLog，删后必须一行不剩。核对不过时回滚，Skip 给出原因。
        public static OrphanDrop Delete(WorkContext ctx, OrphanScan scan, List<string> ids)
        {
            OrphanDrop drop = new OrphanDrop();
            object conn = ctx.OpenFresh();
            bool open = false;
            try
            {
                GlSql.Exec(conn, QuietSql, new object[0]);
                CoTrans.Begin(conn);
                open = true;
                drop.Skip = Check(conn, scan.Station, ids);
                if (drop.Skip != null) return drop;
                List<string> kept = Owned(conn, scan, ids, drop);
                if (drop.Skip != null || kept.Count == 0) return drop;
                Remove(conn, scan.Station, kept);
                CoTrans.Commit(conn);
                open = false;
                drop.Deleted = kept.Count;
                return drop;
            }
            finally
            {
                if (open) QuietRollback(conn);
                AdoXml.Close(conn);
            }
        }

        // ua_Task_Common 里该任务号的任何一行操作员或账套不是本次请求的，就去掉这个候选；没有行的保留。
        // 行数超过 OwnerCap 时看不全，一个都不删（owner_rows_too_many）。
        static List<string> Owned(object conn, OrphanScan scan, List<string> ids, OrphanDrop drop)
        {
            object[] args = Args(new object[] { scan.Station }, null, ids);
            List<Dictionary<string, object>> rows = Rows.Query(conn, OwnerSql + In(ids.Count), args, OwnerCap + 1);
            if (rows.Count > OwnerCap)
            {
                drop.Skip = "owner_rows_too_many";
                return new List<string>();
            }
            HashSet<string> foreign = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                bool mine = Same(Cell(rows[i], "u"), scan.Operator) && Same(Cell(rows[i], "a"), scan.Acc);
                if (!mine) foreign.Add(Key(Cell(rows[i], "id")));
            }
            List<string> kept = new List<string>();
            for (int i = 0; i < ids.Count; i++)
            {
                if (!foreign.Contains(Key(ids[i]))) kept.Add(ids[i]);
            }
            drop.Foreign = ids.Count - kept.Count;
            return kept;
        }

        static void Remove(object conn, string station, List<string> ids)
        {
            GlSql.Exec(conn, DelCommonSql + In(ids.Count), Args(new object[] { station }, null, ids));
            GlSql.Exec(conn, DelLogSql + In(Subs.Length) + " AND cTaskId IN " + In(ids.Count), Args(new object[] { station }, Subs, ids));
            if (Count(conn, SubCountSql, station, ids) != 0)
            {
                throw new InvalidOperationException("删除后 UA_TaskLog 仍有残留");
            }
        }

        // 锁住后：同一任务号还有非 QM 行（例如门户会话）→ shared_task；QM 行数不等于候选数 → count_mismatch。
        static string Check(object conn, string station, List<string> ids)
        {
            string text = Rows.Scalar(conn, LockSql + In(ids.Count), Args(new object[] { station }, null, ids));
            int all = ToInt(text);
            int qm = Count(conn, SubCountSql, station, ids);
            if (all != qm) return "shared_task";
            if (qm != ids.Count) return "count_mismatch";
            return null;
        }

        static int Count(object conn, string head, string station, List<string> ids)
        {
            string sql = head + In(Subs.Length) + " AND cTaskId IN " + In(ids.Count);
            return ToInt(Rows.Scalar(conn, sql, Args(new object[] { station }, Subs, ids)));
        }

        static int ToInt(string text)
        {
            int n;
            if (text == null || !int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                throw new InvalidOperationException("计数没有结果");
            }
            return n;
        }

        static bool Same(string a, string b)
        {
            return string.Equals(Key(a), Key(b), StringComparison.OrdinalIgnoreCase);
        }

        static void QuietRollback(object conn)
        {
            try
            {
                CoTrans.Rollback(conn);
            }
            catch (Exception)
            {
            }
        }

        static object[] Args(object[] head, string[] subs, List<string> ids)
        {
            List<object> all = new List<object>(head);
            if (subs != null)
            {
                for (int i = 0; i < subs.Length; i++) all.Add(subs[i]);
            }
            if (ids != null)
            {
                for (int i = 0; i < ids.Count; i++) all.Add(ids[i]);
            }
            return all.ToArray();
        }

        static string In(int count)
        {
            StringBuilder buf = new StringBuilder("(");
            for (int i = 0; i < count; i++)
            {
                if (i > 0) buf.Append(',');
                buf.Append('?');
            }
            buf.Append(')');
            return buf.ToString();
        }

        static string Cell(Dictionary<string, object> row, string name)
        {
            object value;
            if (row == null || !row.TryGetValue(name, out value)) return null;
            return value as string;
        }

        static string Key(string text)
        {
            return text == null ? "" : text.Trim();
        }
    }

    // TaskOrphansSql.Delete 的结果：删掉的个数、跳过原因（非 null 时已回滚）、因属于别人而去掉的候选数。
    internal sealed class OrphanDrop
    {
        public int Deleted;
        public string Skip;
        public int Foreign;
    }
}
