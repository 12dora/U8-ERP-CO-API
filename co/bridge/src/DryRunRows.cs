using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 预演回读的 ADO 查询：一个整数参数（单据主键），按列类型给出 JSON 值（DryRunValue）。
    // 与 Rows.Query 同样的打开方式（客户端游标、只读、超时 45 秒），但保留数值和日期的类型。
    internal static class DryRunRows
    {
        const int AdInteger = 3;
        const int AdParamInput = 1;
        const int AdUseClient = 3;
        const int AdOpenStatic = 3;
        const int AdLockReadOnly = 1;

        public static List<Dictionary<string, object>> Read(object conn, string sql, int id, int maxRows)
        {
            object cmd = null;
            object rs = null;
            try
            {
                cmd = Command(conn, sql, id);
                rs = Open(cmd);
                return ReadAll(rs, maxRows);
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
        }

        public static int Count(object conn, string sql, int id)
        {
            object cmd = null;
            object rs = null;
            try
            {
                cmd = Command(conn, sql, id);
                rs = Open(cmd);
                if (Values.Flag(ComUtil.Get(rs, "EOF")))
                {
                    return 0;
                }
                object fields = ComUtil.Get(rs, "Fields");
                try
                {
                    return Convert.ToInt32(FieldValue(fields, 0), CultureInfo.InvariantCulture);
                }
                finally
                {
                    ComUtil.ReleaseOne(fields);
                }
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
        }

        static object Command(object conn, string sql, int id)
        {
            if (conn == null)
            {
                throw new BridgeException(500, "internal", "数据库连接为空");
            }
            object cmd = ComUtil.Create("ADODB.Command");
            if (cmd == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            object param = null;
            object parameters = null;
            try
            {
                ComUtil.Set(cmd, "ActiveConnection", conn);
                ComUtil.Set(cmd, "CommandText", sql);
                ComUtil.Set(cmd, "CommandTimeout", 45);
                param = ComUtil.Call(cmd, "CreateParameter", new object[] { "p0", AdInteger, AdParamInput, 4, id });
                parameters = ComUtil.Get(cmd, "Parameters");
                ComUtil.Call(parameters, "Append", new object[] { param });
                return cmd;
            }
            catch
            {
                ComUtil.Final(cmd);
                throw;
            }
            finally
            {
                ComUtil.ReleaseOne(parameters);
                ComUtil.ReleaseOne(param);
            }
        }

        static object Open(object cmd)
        {
            object rs = ComUtil.Create("ADODB.Recordset");
            if (rs == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            try
            {
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { cmd, Type.Missing, AdOpenStatic, AdLockReadOnly, -1 });
                return rs;
            }
            catch
            {
                ComUtil.Final(rs);
                throw;
            }
        }

        static List<Dictionary<string, object>> ReadAll(object rs, int maxRows)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            if (Values.Flag(ComUtil.Get(rs, "EOF")))
            {
                return rows;
            }
            object fields = null;
            try
            {
                fields = ComUtil.Get(rs, "Fields");
                int count = Convert.ToInt32(ComUtil.Get(fields, "Count"), CultureInfo.InvariantCulture);
                while (!Values.Flag(ComUtil.Get(rs, "EOF")) && rows.Count < maxRows)
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
                object field = null;
                try
                {
                    field = ComUtil.Call(fields, "Item", new object[] { i });
                    string name = Values.Text(ComUtil.Get(field, "Name"));
                    int adoType = Convert.ToInt32(ComUtil.Get(field, "Type"), CultureInfo.InvariantCulture);
                    object value;
                    if (DryRunValue.Keep(name, adoType) && DryRunValue.TryJson(ComUtil.Get(field, "Value"), out value))
                    {
                        row[name.ToLowerInvariant()] = value;
                    }
                }
                finally
                {
                    ComUtil.ReleaseOne(field);
                }
            }
            return row;
        }

        static object FieldValue(object fields, int index)
        {
            object field = ComUtil.Call(fields, "Item", new object[] { index });
            try
            {
                return ComUtil.Get(field, "Value");
            }
            finally
            {
                ComUtil.ReleaseOne(field);
            }
        }

        static void CloseRs(object rs)
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
    }
}
