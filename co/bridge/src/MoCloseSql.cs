using System;
using System.Collections.Generic;

namespace U8Co
{
    // 生产订单关闭 / 打开的 SQL、存储过程调用、错误映射和响应。SQL 文本固定，值只走参数。
    internal static partial class MoClose
    {
        const string Cols = "select convert(varchar(12), d.MoDId) as MoDId, o.MoCode,"
            + " convert(varchar(10), d.Status) as Status, convert(varchar(10), isnull(d.IsWFControlled,0)) as wf,"
            + " d.InvCode, d.MDeptCode, d.WhCode, d.CloseUser, convert(varchar(10), d.CloseDate, 23) as CloseDate,"
            + " d.RelsUser, convert(varchar(10), d.RelsDate, 23) as RelsDate from mom_order o";
        const string Tail = " on d.MoId=o.MoId where o.MoId=? order by d.SortSeq, d.MoDId";
        const string LockSql = Cols + " join mom_orderdetail d with (UPDLOCK, HOLDLOCK)" + Tail;
        const string RowsSql = Cols + " join mom_orderdetail d" + Tail;
        // 建表不带参数（直接批处理），留在会话里；带参数的语句走 sp_executesql，能看到外层的临时表。
        const string CreateSql = "IF OBJECT_ID('tempdb..#tmp_procmodid') IS NOT NULL DROP TABLE #tmp_procmodid;"
            + " CREATE TABLE #tmp_procmodid (MoDId int, Ufts varchar(30), ProcDate datetime, ErrFlag int, Errno int)";
        const string DropSql = "IF OBJECT_ID('tempdb..#tmp_procmodid') IS NOT NULL DROP TABLE #tmp_procmodid";
        // Ufts 按 U8 的写法 convert(char, convert(money, Ufts), 2)，不去前导空格（存储过程按原样比较）。
        const string InsertSql = "INSERT INTO #tmp_procmodid (MoDId, Ufts, ProcDate, ErrFlag, Errno)"
            + " SELECT d.MoDId, CONVERT(char, CONVERT(money, d.Ufts), 2), CAST(CONVERT(date, ?, 23) AS datetime), 1, 1"
            + " FROM mom_orderdetail d WHERE d.MoId=? AND d.MoDId=?";
        // 权限串全传 <ALL>：功能权限和数据权限由桥自己判断。关闭的最后一个参数 @v_closeflag 传 0（U8 缺省是 1），
        // 有在制品（sfc_moroutingdetail 的结存数量非 0）的行不关，Errno=102。
        const string CloseSql = "SET NOCOUNT ON; DECLARE @d datetime; SET @d = CAST(CONVERT(date, ?, 23) AS datetime);"
            + " EXEC Usp_MO_Close @d, ?, N'<ALL>', N'', N'<ALL>', N'<ALL>', N'<ALL>', 0";
        const string OpenSql = "SET NOCOUNT ON; DECLARE @d datetime; SET @d = CAST(CONVERT(date, ?, 23) AS datetime);"
            + " EXEC Usp_MO_UnClose @d, ?, N'<ALL>', N'', N'<ALL>', N'<ALL>', N'<ALL>'";
        const string ResultSql = "SELECT convert(varchar(12), MoDId) as MoDId, convert(varchar(10), ErrFlag) as ErrFlag,"
            + " convert(varchar(10), Errno) as Errno FROM #tmp_procmodid";

        static void CallProc(WorkContext ctx, MoCloseAsk ask)
        {
            Plain(ctx.Conn, CreateSql);
            foreach (int lineId in ask.Chosen.Keys)
            {
                GlSql.Exec(ctx.Conn, InsertSql, new object[] { ask.Date, ask.Id, lineId });
            }
            GlSql.Exec(ctx.Conn, ask.Closing ? CloseSql : OpenSql, new object[] { ask.Date, ask.User });
            List<Dictionary<string, object>> flags = Rows.Query(ctx.Conn, ResultSql, new object[0], 5000);
            if (flags.Count != ask.Chosen.Count)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有处理全部明细");
            }
            RaiseErrors(ctx, flags, ask.Closing);
        }

        // ErrFlag 默认 1，处理成功置 0。Errno：101 本月已做成本计算，102 还有在制品（只在关闭且 @v_closeflag=0 时），
        // 0 不在存储过程的权限串内（打开时也表示预留量超过现存量），1 未处理（Ufts 不符等）。任一行失败都整笔回滚。
        static void RaiseErrors(WorkContext ctx, List<Dictionary<string, object>> flags, bool closing)
        {
            string what = closing ? "关闭" : "打开";
            bool cost = false;
            bool wip = false;
            bool denied = false;
            string other = null;
            for (int i = 0; i < flags.Count; i++)
            {
                if (CoRows.Col(flags[i], "ErrFlag") == "0")
                {
                    continue;
                }
                string no = CoRows.Col(flags[i], "Errno");
                CoRows.Note(ctx.Item, "MoClose " + CoRows.Col(flags[i], "MoDId") + " Errno=" + no);
                cost = cost || no == "101";
                wip = wip || no == "102";
                denied = denied || no == "0";
                other = other ?? no;
            }
            if (other == null)
            {
                return;
            }
            throw ErrorOf(what, cost, wip, denied, other);
        }

        static BridgeException ErrorOf(string what, bool cost, bool wip, bool denied, string no)
        {
            if (cost)
            {
                return new BridgeException(409, "u8_rejected", "本月成本已计算，不能" + what);
            }
            if (wip)
            {
                return new BridgeException(409, "u8_rejected", "生产订单还有在制，不能关闭");
            }
            if (denied && what == "关闭")
            {
                return new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
            if (denied)
            {
                // Usp_MO_UnClose 把占用的现存量加回后，若预留量超过现存量也记 Errno=0，与无权限同号。
                return new BridgeException(409, "u8_rejected", "预留量超过现存量或没有权限，不能打开");
            }
            if (no == "1")
            {
                return new BridgeException(409, "u8_rejected", "生产订单已被修改，请重试");
            }
            return new BridgeException(409, "u8_rejected", "U8 未能" + what + "生产订单（错误号 " + no + "）");
        }

        static void DropTemp(object conn)
        {
            Plain(conn, DropSql);
        }

        static void DropQuietly(object conn)
        {
            try
            {
                Plain(conn, DropSql);
            }
            catch (Exception)
            {
            }
        }

        static void Plain(object conn, string sql)
        {
            object rs = null;
            try
            {
                rs = ComUtil.Call(conn, "Execute", new object[] { sql });
            }
            finally
            {
                ComUtil.Final(rs);
            }
        }

        // 与销售订单 / 采购订单的关闭响应同形（closed、closed_by、closed_at、lines），另带 code、changed 和 state。
        // closed 表示全部行都已关闭；closed_by / closed_at 取第一条已关闭行，没有已关闭行时为空。
        static ApiResult Body(VoucherKind kind, int id, string action, int changed, List<Dictionary<string, object>> rows)
        {
            List<Dictionary<string, object>> lines = new List<Dictionary<string, object>>();
            Dictionary<string, object> first = null;
            int closedCount = 0;
            int released = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                string status = StatusOf(rows[i]);
                bool shut = status == "4";
                if (shut)
                {
                    closedCount++;
                    first = first ?? rows[i];
                }
                released += Audited(rows[i], status) ? 1 : 0;
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["line_id"] = CoRows.AsId(CoRows.Col(rows[i], "MoDId"));
                item["closed"] = shut;
                lines.Add(item);
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = rows.Count > 0 ? CoRows.Col(rows[0], "MoCode") : "";
            body["action"] = action;
            body["closed"] = rows.Count > 0 && closedCount == rows.Count;
            body["closed_by"] = first == null ? "" : CoRows.Col(first, "CloseUser");
            body["closed_at"] = first == null ? "" : CoRows.Col(first, "CloseDate");
            body["changed"] = changed;
            body["lines"] = lines;
            body["state"] = State(rows, released, closedCount);
            return ApiResult.Ok(body);
        }

        // 同 vouchers/load（SqlRead.MoState）：Status 3、4 都算已审核，且审核人非空。
        static bool Audited(Dictionary<string, object> row, string status)
        {
            return (status == "3" || status == "4") && CoRows.Col(row, "RelsUser").Length > 0;
        }

        static Dictionary<string, object> State(List<Dictionary<string, object>> rows, int released, int closedCount)
        {
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = rows.Count > 0 && released == rows.Count;
            state["closed"] = rows.Count > 0 && closedCount == rows.Count;
            state["verifier"] = rows.Count > 0 ? CoRows.Col(rows[0], "RelsUser") : "";
            state["verified_at"] = rows.Count > 0 ? CoRows.Col(rows[0], "RelsDate") : "";
            return state;
        }
    }
}
