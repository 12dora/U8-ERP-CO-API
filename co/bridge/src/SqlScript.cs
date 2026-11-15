using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace U8Co
{
    // 存货核算脚本的参数，经 #ia_args 交给脚本。KeepDate 是记账日期（月末那一天，yyyy-MM-dd）；
    // Accounter 是登录操作员的 U8 用户名（U8 在 cbAccounter / cAccounter 里存名字）；OnUncosted 为 refuse 或 skip。
    internal sealed class IaArgs
    {
        public int Year;
        public int Month;
        public string KeepDate;
        public string Accounter;
        public string OnUncosted;
    }

    // Counts：最后一个只有 k、n 两列的结果集（诊断计数），HasCounts 表示读到过这样的结果集。
    // Rows：第一个其他形状的结果集。
    internal sealed class ScriptResult
    {
        public Dictionary<string, string> Counts = new Dictionary<string, string>();
        public bool HasCounts;
        public List<Dictionary<string, object>> Rows;

        // 脚本正常跑完一定以 k、n 计数收尾；缺了说明批没有执行完，按内部错误处理（调用方回滚）。
        public void Require(string key)
        {
            if (!HasCounts || (key != null && !Counts.ContainsKey(key)))
            {
                throw new BridgeException(500, "internal", "存货核算脚本没有返回结果计数 " + (key ?? "") + "，已回滚");
            }
        }
    }

    // 脚本用 THROW 50000–50099 拒绝（期初记账脚本用 50101–50199）。Message 是脚本里的诊断（50000 是 U8 过程返回的原文），只进审计，
    // 不要原样回给调用方；由调用方按 Number 换成中文 409。Rows 是出错前最近一个含 cInvCode 列的结果集（50061 的存货清单），没有就为空。
    internal sealed class ScriptRefusal : Exception
    {
        public int Number;
        public List<Dictionary<string, object>> Rows;

        public ScriptRefusal(int number, string message, List<Dictionary<string, object>> rows)
            : base(message ?? "")
        {
            Number = number;
            Rows = rows ?? new List<Dictionary<string, object>>();
        }
    }

    // 内嵌的 SQL 脚本（co/bridge/sql/**，build.ps1 用 /resource 嵌入，逻辑名即相对路径，如 sql/ia/post.sql）。
    // 每个脚本整批执行（不分 GO），在调用方给的连接上跑（请求事务、预演回滚都在调用方）。调用方的值只经 #ia_args
    // （带参数的一句写入），脚本文本不拼任何值。过程会关掉 NOCOUNT，先回「影响行数」这种已关闭的结果：
    // 逐个 NextRecordset 读完全部结果，批才在服务器上执行完，错误也在读到那一处时才抛出。
    internal static class SqlScript
    {
        internal static readonly string[] Names = new string[]
        {
            "sql/ia/post_tables.sql",
            "sql/ia/post.sql",
            "sql/ia/unpost.sql",
            "sql/ia/period_end.sql",
            "sql/ia/period_end_cancel.sql",
            "sql/ia/close.sql",
            "sql/ia/unclose.sql",
            "sql/ia/qc_keep.sql",
            "sql/ia/qc_recover.sql",
            // 应收 / 应付汇兑损益（ArapExGain：参数表 #exg_args 由调用方先建，走 RunPrepared）。
            "sql/arap/exgain_create.sql",
            "sql/arap/exgain_persist.sql",
            "sql/arap/exgain_cancel.sql",
            "sql/arap/exgain_cancel_end.sql",
            // 计提坏账准备（ArapBadProvision：参数表 #badp_args 由调用方先建，走 RunPrepared）。
            "sql/arap/bad_provision.sql",
            // 坏账发生 9G、坏账收回 9H（ArapBadOccur / ArapBadRecover：参数表 #bad_args 由调用方先建，走 RunPrepared）。
            "sql/arap/bad_occur.sql",
            "sql/arap/bad_recover.sql",
            // 取消坏账处理 9F / 9G / 9H（ArapProcCancelBad：参数表 #badc_args 由调用方先建，走 RunPrepared）。
            "sql/arap/bad_cancel.sql"
        };

        internal const int RefuseLow = 50000;
        internal const int RefuseHigh = 50099;
        // 存货核算期初记账 / 取消期初记账（qc_keep.sql、qc_recover.sql）的拒绝编号：中文原文，调用方（OpeningIa）按编号转 409。
        internal const int QcRefuseLow = 50101;
        internal const int QcRefuseHigh = 50199;
        const int MaxRows = 1000;
        // 结果集个数的保险上限（运行时间由 CommandTimeout 约束）；到了上限批还没读完就报错，不能当作成功提交。
        const int MaxSets = 10000000;
        // ADO / SQLOLEDB 的命令超时（DB_E_ABORTLIMITREACHED）。
        const int TimeoutHr = unchecked((int)0x80040E31);
        const string DetailColumn = "cInvCode";

        internal const string ArgsTable = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#ia_args') IS NOT NULL DROP TABLE #ia_args;"
            + " CREATE TABLE #ia_args (y smallint, m tinyint, keep_date nvarchar(10), accounter nvarchar(20), on_uncosted nvarchar(10));"
            + " SET NOCOUNT OFF;";

        internal const string ArgsFill = "INSERT INTO #ia_args (y, m, keep_date, accounter, on_uncosted) VALUES (?, ?, ?, ?, ?)";

        static readonly object Gate = new object();
        static readonly Dictionary<string, string> Cache = new Dictionary<string, string>(StringComparer.Ordinal);

        // 读一个内嵌脚本（缓存）。没有这个资源是构建问题：500。
        public static string Text(string name)
        {
            lock (Gate)
            {
                string text;
                if (name != null && Cache.TryGetValue(name, out text))
                {
                    return text;
                }
                text = Load(name);
                Cache[name] = text;
                return text;
            }
        }

        static string Load(string name)
        {
            Stream stream = name == null ? null : typeof(SqlScript).Assembly.GetManifestResourceStream(name);
            if (stream == null)
            {
                throw new BridgeException(500, "internal", "缺少内嵌脚本 " + (name ?? ""));
            }
            using (StreamReader reader = new StreamReader(stream, new UTF8Encoding(false), true))
            {
                string text = reader.ReadToEnd();
                if (text.Trim().Length == 0)
                {
                    throw new BridgeException(500, "internal", "内嵌脚本为空 " + name);
                }
                return text;
            }
        }

        // 同下，另要求最后的计数里有 requiredKey（为 null 时只要求有计数结果集）。
        public static ScriptResult Run(object conn, string[] names, IaArgs args, int timeoutSeconds, string requiredKey)
        {
            ScriptResult result = Run(conn, names, args, timeoutSeconds);
            result.Require(requiredKey);
            return result;
        }

        // 建 #ia_args 并写入参数，再按顺序逐个整批执行脚本。50000–50099、50101–50199 → ScriptRefusal，超时 → 503 ia_timeout，
        // 其余 SQL 错误 → 500。
        public static ScriptResult Run(object conn, string[] names, IaArgs args, int timeoutSeconds)
        {
            if (conn == null)
            {
                throw new BridgeException(500, "internal", "数据库连接为空");
            }
            if (names == null || names.Length == 0 || args == null)
            {
                throw new BridgeException(500, "internal", "存货核算脚本参数缺失");
            }
            // 先取全部文本：缺资源在动库之前报错。
            string[] texts = new string[names.Length];
            for (int i = 0; i < names.Length; i++)
            {
                texts[i] = Text(names[i]);
            }
            ScriptState state = new ScriptState();
            WriteArgs(conn, args, state);
            for (int i = 0; i < names.Length; i++)
            {
                RunOne(conn, names[i], texts[i], timeoutSeconds, state);
            }
            if (state.Result.Rows == null)
            {
                state.Result.Rows = new List<Dictionary<string, object>>();
            }
            return state.Result;
        }

        // 同 Run，但不建 #ia_args：参数表由调用方先在本连接上建好、写好（如汇兑损益的 #exg_args，ArapExGainSql.Prepare）。
        // 最后的计数里必须有 requiredKey（为 null 时只要求有计数结果集），缺了 500。
        public static ScriptResult RunPrepared(object conn, string[] names, int timeoutSeconds, string requiredKey)
        {
            if (conn == null || names == null || names.Length == 0)
            {
                throw new BridgeException(500, "internal", "脚本参数缺失");
            }
            string[] texts = new string[names.Length];
            for (int i = 0; i < names.Length; i++)
            {
                texts[i] = Text(names[i]);
            }
            ScriptState state = new ScriptState();
            for (int i = 0; i < names.Length; i++)
            {
                RunOne(conn, names[i], texts[i], timeoutSeconds, state);
            }
            if (state.Result.Rows == null)
            {
                state.Result.Rows = new List<Dictionary<string, object>>();
            }
            state.Result.Require(requiredKey);
            return state.Result;
        }

        static void WriteArgs(object conn, IaArgs args, ScriptState state)
        {
            try
            {
                UnwriteoffSql.Run(conn, ArgsTable);
                GlSql.Exec(conn, ArgsFill, new object[] { args.Year, args.Month, args.KeepDate, args.Accounter, args.OnUncosted });
            }
            catch (COMException ex)
            {
                throw Fail(conn, "#ia_args", ex, state);
            }
        }

        static void RunOne(object conn, string name, string text, int timeoutSeconds, ScriptState state)
        {
            object cmd = ComUtil.Create("ADODB.Command");
            if (cmd == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            object rs = null;
            try
            {
                ComUtil.Set(cmd, "ActiveConnection", conn);
                ComUtil.Set(cmd, "CommandText", text);
                ComUtil.Set(cmd, "CommandTimeout", timeoutSeconds);
                rs = ComUtil.Call(cmd, "Execute", new object[0]);
                for (int i = 0; rs != null; i++)
                {
                    if (i >= MaxSets)
                    {
                        Cancel(cmd);
                        throw new BridgeException(500, "internal", "存货核算脚本 " + name + " 结果集超过上限，已回滚");
                    }
                    Take(rs, state);
                    object next = ComUtil.Call(rs, "NextRecordset", new object[0]);
                    ComUtil.ReleaseOne(rs);
                    rs = next;
                }
            }
            catch (Exception ex)
            {
                // 先读错误集合再取消：取消会清掉 Errors。取消让服务器停下这一批，回滚前连接是空闲的。
                // 不是 COM 错误（读结果集时的转换、结果集超限等）同样先取消，再原样抛出。
                COMException com = ex as COMException;
                Exception failure = com == null ? null : Fail(conn, name, com, state);
                Cancel(cmd);
                if (failure == null)
                {
                    throw;
                }
                throw failure;
            }
            finally
            {
                Close(rs);
                ComUtil.Final(cmd);
            }
        }

        // 已关闭的结果（影响行数）跳过；k、n 两列的是诊断计数，取最后一个；其他形状第一个进 Rows；
        // 含 cInvCode 列的最近一个留给拒绝（50061 列出的存货）。
        static void Take(object rs, ScriptState state)
        {
            if (Convert.ToInt32(ComUtil.Get(rs, "State"), CultureInfo.InvariantCulture) == 0)
            {
                return;
            }
            List<string> columns;
            List<Dictionary<string, object>> rows = Read(rs, out columns);
            if (IsCounts(columns))
            {
                state.Result.Counts = ToCounts(rows);
                state.Result.HasCounts = true;
                return;
            }
            if (state.Result.Rows == null)
            {
                state.Result.Rows = rows;
            }
            if (HasColumn(columns, DetailColumn))
            {
                state.Last = rows;
            }
        }

        internal static bool HasColumn(List<string> columns, string name)
        {
            foreach (string column in columns)
            {
                if (string.Equals(column, name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        internal static bool IsCounts(List<string> columns)
        {
            return columns != null && columns.Count == 2
                && string.Equals(columns[0], "k", StringComparison.OrdinalIgnoreCase)
                && string.Equals(columns[1], "n", StringComparison.OrdinalIgnoreCase);
        }

        static Dictionary<string, string> ToCounts(List<Dictionary<string, object>> rows)
        {
            Dictionary<string, string> counts = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (Dictionary<string, object> row in rows)
            {
                string key = Cell(row, "k");
                if (key.Length > 0)
                {
                    counts[key] = Cell(row, "n");
                }
            }
            return counts;
        }

        static string Cell(Dictionary<string, object> row, string name)
        {
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return Values.Text(pair.Value).Trim();
                }
            }
            return "";
        }

        static List<Dictionary<string, object>> Read(object rs, out List<string> columns)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            object fields = null;
            try
            {
                fields = ComUtil.Get(rs, "Fields");
                int count = Convert.ToInt32(ComUtil.Get(fields, "Count"), CultureInfo.InvariantCulture);
                columns = Columns(fields, count);
                while (rows.Count < MaxRows && !Values.Flag(ComUtil.Get(rs, "EOF")))
                {
                    rows.Add(ReadRow(fields, columns));
                    ComUtil.Call(rs, "MoveNext", new object[0]);
                }
                return rows;
            }
            finally
            {
                ComUtil.ReleaseOne(fields);
            }
        }

        static List<string> Columns(object fields, int count)
        {
            List<string> columns = new List<string>();
            for (int i = 0; i < count; i++)
            {
                object field = ComUtil.Call(fields, "Item", new object[] { i });
                try
                {
                    columns.Add(Values.Text(ComUtil.Get(field, "Name")));
                }
                finally
                {
                    ComUtil.ReleaseOne(field);
                }
            }
            return columns;
        }

        // 单元格一律转成字符串（Rows 的约定），空值在行里缺席。
        static Dictionary<string, object> ReadRow(object fields, List<string> columns)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            for (int i = 0; i < columns.Count; i++)
            {
                object field = ComUtil.Call(fields, "Item", new object[] { i });
                try
                {
                    object value = ComUtil.Get(field, "Value");
                    if (value == null || value is DBNull || Rows.Hidden(columns[i]))
                    {
                        continue;
                    }
                    IFormattable fmt = value as IFormattable;
                    row[columns[i]] = fmt != null ? fmt.ToString(null, CultureInfo.InvariantCulture) : Convert.ToString(value);
                }
                finally
                {
                    ComUtil.ReleaseOne(field);
                }
            }
            return row;
        }

        static Exception Fail(object conn, string name, COMException ex, ScriptState state)
        {
            int number;
            string text;
            FirstError(conn, out number, out text);
            if (IsRefusal(number))
            {
                return new ScriptRefusal(number, text, state.Last);
            }
            if (ex.ErrorCode == TimeoutHr)
            {
                return new BridgeException(503, "ia_timeout", "存货核算处理超时，已回滚；可稍后重试，或调大 iaCommandSeconds");
            }
            string detail = text.Length > 0 ? text : ex.Message;
            return new BridgeException(500, "internal", "存货核算脚本 " + name + " 出错：" + GlPostTx.FirstLine(detail));
        }

        internal static bool IsRefusal(int number)
        {
            return (number >= RefuseLow && number <= RefuseHigh) || (number >= QcRefuseLow && number <= QcRefuseHigh);
        }

        // ADO 的 Errors 里挑一条：有拒绝编号（IsRefusal）就用它，否则第一条带 SQL 错误号的。读不到时号为 0、原文为空。
        // SQLState 以 01 开头的是提示和警告（严重级别 ≤ 10，如 PRINT、低级别 RAISERROR），不算错误。
        static void FirstError(object conn, out int number, out string text)
        {
            number = 0;
            text = "";
            object errors = null;
            try
            {
                errors = ComUtil.Get(conn, "Errors");
                int count = Convert.ToInt32(ComUtil.Get(errors, "Count"), CultureInfo.InvariantCulture);
                for (int i = 0; i < count && !IsRefusal(number); i++)
                {
                    Pick(errors, i, ref number, ref text);
                }
            }
            catch (COMException)
            {
                // 读不到错误集合时按普通失败处理。
            }
            finally
            {
                ComUtil.ReleaseOne(errors);
            }
        }

        static void Pick(object errors, int index, ref int number, ref string text)
        {
            object item = ComUtil.Call(errors, "Item", new object[] { index });
            try
            {
                string sqlState = Values.Text(ComUtil.Get(item, "SQLState"));
                if (sqlState.StartsWith("01", StringComparison.Ordinal))
                {
                    return;
                }
                int native = Convert.ToInt32(ComUtil.Get(item, "NativeError"), CultureInfo.InvariantCulture);
                if (native != 0 && (number == 0 || IsRefusal(native)))
                {
                    number = native;
                    text = Values.Text(ComUtil.Get(item, "Description"));
                }
            }
            finally
            {
                ComUtil.ReleaseOne(item);
            }
        }

        // 只为让服务器停下这一批；同步执行时 ADO 可能不认，失败不影响后面的回滚。
        static void Cancel(object cmd)
        {
            try
            {
                ComUtil.Call(cmd, "Cancel", new object[0]);
            }
            catch (Exception)
            {
            }
        }

        static void Close(object rs)
        {
            if (rs == null)
            {
                return;
            }
            try
            {
                if (Convert.ToInt32(ComUtil.Get(rs, "State"), CultureInfo.InvariantCulture) != 0)
                {
                    ComUtil.Call(rs, "Close", new object[0]);
                }
            }
            catch (Exception)
            {
            }
            ComUtil.Final(rs);
        }

        sealed class ScriptState
        {
            public ScriptResult Result = new ScriptResult();
            public List<Dictionary<string, object>> Last;
        }
    }
}
