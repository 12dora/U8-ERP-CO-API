using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 表名和列名只能来自 VoucherKind。调用方的文本只进 ADO 参数。
    internal static class Rows
    {
        const int AdInteger = 3;
        const int AdVarWChar = 202;
        const int AdParamInput = 1;
        const int AdUseClient = 3;
        const int AdOpenStatic = 3;
        const int AdLockReadOnly = 1;

        public static List<Dictionary<string, object>> Query(object conn, string sql, object[] args, int maxRows)
        {
            if (conn == null)
            {
                throw new BridgeException(500, "internal", "数据库连接为空");
            }
            if (maxRows < 1)
            {
                return new List<Dictionary<string, object>>();
            }
            object cmd = null;
            object rs = null;
            try
            {
                rs = OpenRs(conn, sql, args, out cmd);
                return ReadRs(rs, maxRows);
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
        }

        public static Dictionary<string, object> One(object conn, string sql, object[] args)
        {
            List<Dictionary<string, object>> rows = Query(conn, sql, args, 1);
            if (rows.Count == 0)
            {
                return null;
            }
            return rows[0];
        }

        public static string Scalar(object conn, string sql, object[] args)
        {
            Dictionary<string, object> row = One(conn, sql, args);
            if (row == null)
            {
                return null;
            }
            foreach (object value in row.Values)
            {
                return value as string;
            }
            return null;
        }

        public static string Ufts(object conn, string table, string idColumn, int id)
        {
            RequireIdent(table);
            RequireIdent(idColumn);
            string sql = "SELECT CONVERT(CHAR, CONVERT(MONEY, UFTS), 2) FROM " + table + " WHERE " + idColumn + "=?";
            string raw = Scalar(conn, sql, new object[] { id });
            if (raw == null)
            {
                return null;
            }
            return raw.TrimEnd();
        }

        public static object NewDom()
        {
            return RowsDom.NewDom();
        }

        public static void UseXPath(object dom)
        {
            RowsDom.UseXPath(dom);
        }

        public static List<Dictionary<string, object>> FromDom(object dom, int maxRows)
        {
            return RowsDom.FromDom(dom, maxRows);
        }

        static void RequireIdent(string name)
        {
            if (name == null || name.Length == 0 || name.Length > 64)
            {
                throw new BridgeException(500, "internal", "标识符无效");
            }
            for (int i = 0; i < name.Length; i++)
            {
                if (!IdentChar(name[i]))
                {
                    throw new BridgeException(500, "internal", "标识符无效");
                }
            }
        }

        static bool IdentChar(char c)
        {
            return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                || (c >= '0' && c <= '9') || c == '_';
        }

        static object OpenRs(object conn, string sql, object[] args, out object cmd)
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
                ComUtil.Set(cmd, "CommandTimeout", 45);
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
            int adoType = AdVarWChar;
            int size = 4000;
            object bound = value;
            if (value is int)
            {
                adoType = AdInteger;
                size = 4;
            }
            else if (value == null)
            {
                bound = DBNull.Value;
            }
            else if (!(value is string))
            {
                throw new BridgeException(500, "internal", "参数类型不支持");
            }
            AppendParam(cmd, "p" + index.ToString(CultureInfo.InvariantCulture), adoType, size, bound);
        }

        static void AppendParam(object cmd, string name, int adoType, int size, object value)
        {
            object param = ComUtil.Call(cmd, "CreateParameter", new object[] { name, adoType, AdParamInput, size, value });
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
                string name;
                string text = Cell(fields, i, out name);
                if (text == null || Hidden(name))
                {
                    continue;
                }
                row[name] = text;
            }
            return row;
        }

        static string Cell(object fields, int index, out string name)
        {
            object field = null;
            try
            {
                field = ComUtil.Call(fields, "Item", new object[] { index });
                name = Values.Text(ComUtil.Get(field, "Name"));
                return FormatCell(ComUtil.Get(field, "Value"));
            }
            finally
            {
                ComUtil.ReleaseOne(field);
            }
        }

        static string FormatCell(object value)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            if (value is DateTime)
            {
                DateTime dt = (DateTime)value;
                string pattern = dt.TimeOfDay.Ticks == 0 ? "yyyy-MM-dd" : "yyyy-MM-dd HH:mm:ss";
                return dt.ToString(pattern, CultureInfo.InvariantCulture);
            }
            if (value is bool)
            {
                return ((bool)value) ? "1" : "0";
            }
            // SELECT * 里的 timestamp/rowversion（含 UFTS）是 byte[]，ToString 会变成 System.Byte[]。
            byte[] raw = value as byte[];
            if (raw != null)
            {
                return BitConverter.ToString(raw).Replace("-", "");
            }
            IFormattable fmt = value as IFormattable;
            if (fmt != null)
            {
                return fmt.ToString(null, CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value);
        }

        internal static bool Hidden(string name)
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

        static bool Eof(object rs)
        {
            return Values.Flag(ComUtil.Get(rs, "EOF"));
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
