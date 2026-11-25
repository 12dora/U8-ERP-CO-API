using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace U8Co
{
    internal static class ProbeSql
    {
        const int NoTransaction = unchecked((int)0x8004D00E);
        const string LastText =
            "select s.session_id as spid, convert(varchar(30), c.last_read, 121) as last_read, "
            + "t.text as sqltext from sys.dm_exec_sessions s "
            + "join sys.dm_exec_connections c on c.session_id = s.session_id "
            + "cross apply sys.dm_exec_sql_text(c.most_recent_sql_handle) t "
            + "where s.host_process_id = ? and s.session_id <> @@SPID order by c.last_read";

        public static void Query(ProbeRun run, Dictionary<string, object> step)
        {
            string sql = StepIo.Req(step, "query");
            object[] args = ProbeCall.Scalars(run, StepIo.List(step, "params"));
            ProbeAdo.CommandSeconds = StepIo.Has(step, "timeout_s") ? Math.Max(1, Math.Min(3600, Nums.AsInt(step["timeout_s"]))) : 45;
            List<Dictionary<string, object>> rows = ProbeAdo.Query(run.NeedConn(), sql, args, Max(step, 50));
            EmitRows(run, step, rows);
        }

        public static void Last(ProbeRun run, Dictionary<string, object> step)
        {
            run.NeedConn();
            if (run.ConnText == null || run.ConnText.Length == 0)
            {
                throw new BridgeException(500, "internal", "没有连接串");
            }
            object conn = null;
            try
            {
                conn = ProbeAdo.Open(run.ConnText);
                object[] args = new object[] { Process.GetCurrentProcess().Id };
                List<Dictionary<string, object>> rows = ProbeAdo.Query(conn, LastText, args, 30);
                EmitRows(run, step, rows);
            }
            finally
            {
                ProbeAdo.Close(conn);
            }
        }

        public static void Trans(ProbeRun run, Dictionary<string, object> step)
        {
            object conn = run.NeedConn();
            string op = StepIo.Op(step);
            if (op == "trans.begin")
            {
                ComUtil.Call(conn, "BeginTrans", new object[0]);
            }
            else if (op == "trans.commit")
            {
                CommitSeen(conn);
            }
            else if (op == "trans.rollback")
            {
                Rollback(conn);
            }
            else
            {
                throw new BridgeException(400, "bad_request", "不认识的事务操作");
            }
            Line line = ProbeOut.Begin(run, step, true);
            line.Num("trancount", ProbeAdo.QueryInt(conn, "SELECT @@TRANCOUNT"));
            ProbeOut.Emit(line);
        }

        static void EmitRows(ProbeRun run, Dictionary<string, object> step, List<Dictionary<string, object>> rows)
        {
            Line line = ProbeOut.Begin(run, step, true);
            line.Num("count", rows.Count);
            line.Raw("rows", ProbeOut.Rows(rows));
            ProbeOut.Emit(line);
        }

        static int Max(Dictionary<string, object> step, int fallback)
        {
            if (!StepIo.Present(step, "max") || step["max"] == null)
            {
                return fallback;
            }
            int max = Nums.AsInt(step["max"]);
            if (max < 1 || max > 200)
            {
                throw new BridgeException(400, "bad_request", "max 无效");
            }
            return max;
        }

        static void CommitSeen(object conn)
        {
            try
            {
                ComUtil.Call(conn, "CommitTrans", new object[0]);
            }
            catch (Exception ex)
            {
                if (!IsNoTx(ex))
                {
                    throw;
                }
            }
            RequireClear(conn);
        }

        static void Rollback(object conn)
        {
            try
            {
                ComUtil.Call(conn, "RollbackTrans", new object[0]);
            }
            catch (Exception ex)
            {
                if (IsNoTx(ex))
                {
                    return;
                }
                throw;
            }
        }

        static void RequireClear(object conn)
        {
            int left = ProbeAdo.QueryInt(conn, "SELECT @@TRANCOUNT");
            if (left != 0)
            {
                throw new BridgeException(500, "internal", "提交后事务还在");
            }
        }

        static bool IsNoTx(Exception ex)
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
    }
}
