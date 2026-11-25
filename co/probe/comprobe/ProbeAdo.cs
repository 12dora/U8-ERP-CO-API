using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static class ProbeAdo
    {
        const int AdInteger = 3;
        const int AdDouble = 5;
        const int AdBoolean = 11;
        const int AdBigInt = 20;
        const int AdVarWChar = 202;
        const int AdParamInput = 1;
        const int AdUseClient = 3;
        const int AdOpenStatic = 3;
        const int AdLockReadOnly = 1;

        // 单步 SQL 的超时（秒）。sql 步骤可用 timeout_s 改（1–3600），缺省 45。
        internal static int CommandSeconds = 45;

        public static object Open(string connectionString)
        {
            object conn = ComUtil.Create("ADODB.Connection");
            if (conn == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            try
            {
                ComUtil.Set(conn, "CommandTimeout", 45);
                ComUtil.Call(conn, "Open", new object[] { connectionString });
                return conn;
            }
            catch
            {
                ComUtil.Final(conn);
                throw;
            }
        }

        public static void Close(object conn)
        {
            if (conn == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(conn, "Close", new object[0]);
            }
            catch (Exception)
            {
            }
            ComUtil.Final(conn);
        }

        public static List<Dictionary<string, object>> Query(object conn, string sql, object[] args, int maxRows)
        {
            if (conn == null)
            {
                throw new BridgeException(400, "bad_request", "尚未登录");
            }
            object cmd = null;
            object rs = null;
            try
            {
                rs = SkipClosed(OpenRs(conn, sql, args, out cmd));
                if (rs == null)
                {
                    return new List<Dictionary<string, object>>();
                }
                return ReadRs(rs, maxRows);
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
        }

        // 批里的过程会把 NOCOUNT 打开，先返回「影响行数」这种已关闭的结果；跳到第一个有列的结果集，
        // 一个都没有就返回 null。读完各结果，批才会在服务器上执行完。
        static object SkipClosed(object rs)
        {
            for (int i = 0; i < 10000 && rs != null; i++)
            {
                if (Convert.ToInt32(ComUtil.Get(rs, "State"), CultureInfo.InvariantCulture) != 0)
                {
                    return rs;
                }
                object next = ComUtil.Call(rs, "NextRecordset", new object[0]);
                ComUtil.ReleaseOne(rs);
                rs = next;
            }
            return rs;
        }

        public static int QueryInt(object conn, string sql)
        {
            object rs = null;
            object fields = null;
            object field = null;
            try
            {
                rs = ComUtil.Call(conn, "Execute", new object[] { sql });
                fields = ComUtil.Get(rs, "Fields");
                field = ComUtil.Call(fields, "Item", new object[] { 0 });
                return Convert.ToInt32(ComUtil.Get(field, "Value"));
            }
            finally
            {
                ComUtil.ReleaseOne(field);
                ComUtil.ReleaseOne(fields);
                CloseRs(rs);
            }
        }

        public static object OpenRs(object conn, string sql, object[] args, out object cmd)
        {
            cmd = ComUtil.Create("ADODB.Command");
            if (cmd == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            object rs = null;
            try
            {
                ComUtil.Set(cmd, "ActiveConnection", conn);
                ComUtil.Set(cmd, "CommandText", sql);
                ComUtil.Set(cmd, "CommandTimeout", CommandSeconds);
                AddParams(cmd, args);
                rs = ComUtil.Create("ADODB.Recordset");
                if (rs == null)
                {
                    throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
                }
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { cmd, Type.Missing, AdOpenStatic, AdLockReadOnly, -1 });
                return rs;
            }
            catch
            {
                ComUtil.Final(rs);
                ComUtil.Final(cmd);
                cmd = null;
                throw;
            }
        }

        public static bool Eof(object rs)
        {
            if (rs == null)
            {
                return true;
            }
            return Values.Flag(ComUtil.Get(rs, "EOF"));
        }

        public static void CloseRs(object rs)
        {
            if (rs == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(rs, "Close", new object[0]);
            }
            catch (Exception)
            {
            }
            ComUtil.Final(rs);
        }

        static void AddParams(object cmd, object[] args)
        {
            if (args == null)
            {
                return;
            }
            for (int i = 0; i < args.Length; i++)
            {
                AddOne(cmd, i, args[i]);
            }
        }

        static void AddOne(object cmd, int index, object value)
        {
            int adoType;
            int size;
            object bound;
            Kind(value, out adoType, out size, out bound);
            string name = "p" + index.ToString(CultureInfo.InvariantCulture);
            object param = ComUtil.Call(cmd, "CreateParameter", new object[] { name, adoType, AdParamInput, size, bound });
            object parameters = null;
            try
            {
                parameters = ComUtil.Get(cmd, "Parameters");
                ComUtil.Call(parameters, "Append", new object[] { param });
            }
            finally
            {
                ComUtil.ReleaseOne(parameters);
                ComUtil.ReleaseOne(param);
            }
        }

        static void Kind(object value, out int adoType, out int size, out object bound)
        {
            bound = value;
            adoType = AdVarWChar;
            size = 4000;
            if (value == null)
            {
                bound = DBNull.Value;
                return;
            }
            if (value is bool)
            {
                adoType = AdBoolean;
                size = 1;
                return;
            }
            if (value is int)
            {
                adoType = AdInteger;
                size = 4;
                return;
            }
            if (value is long)
            {
                adoType = AdBigInt;
                size = 8;
                return;
            }
            if (value is string)
            {
                return;
            }
            if (value is double || value is float || value is decimal)
            {
                adoType = AdDouble;
                size = 8;
                bound = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                return;
            }
            throw new BridgeException(400, "bad_request", "参数类型不支持");
        }

        static List<Dictionary<string, object>> ReadRs(object rs, int maxRows)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            if (Eof(rs))
            {
                return rows;
            }
            object fields = null;
            try
            {
                fields = ComUtil.Get(rs, "Fields");
                int count = Convert.ToInt32(ComUtil.Get(fields, "Count"));
                while (!Eof(rs) && rows.Count < maxRows)
                {
                    rows.Add(ReadRow(fields, count));
                    ComUtil.Call(rs, "MoveNext", new object[0]);
                }
                return rows;
            }
            finally
            {
                ComUtil.ReleaseOne(fields);
            }
        }

        static Dictionary<string, object> ReadRow(object fields, int count)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            for (int i = 0; i < count; i++)
            {
                PutCell(row, fields, i);
            }
            return row;
        }

        static void PutCell(Dictionary<string, object> row, object fields, int index)
        {
            object field = null;
            try
            {
                field = ComUtil.Call(fields, "Item", new object[] { index });
                string name = Values.Text(ComUtil.Get(field, "Name"));
                if (Hidden(name))
                {
                    return;
                }
                row[name] = Cell(ComUtil.Get(field, "Value"));
            }
            finally
            {
                ComUtil.ReleaseOne(field);
            }
        }

        static object Cell(object value)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            if (value is DateTime)
            {
                return ((DateTime)value).ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            byte[] raw = value as byte[];
            if (raw != null)
            {
                return BitConverter.ToString(raw).Replace("-", "");
            }
            return Nums.Invariant(value);
        }

        static bool Hidden(string name)
        {
            if (name == null)
            {
                return true;
            }
            string lower = name.ToLowerInvariant();
            if (lower.IndexOf("password", StringComparison.Ordinal) >= 0)
            {
                return true;
            }
            return lower.IndexOf("pwd", StringComparison.Ordinal) >= 0;
        }
    }
}
