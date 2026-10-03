using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 总账写库只走这里：参数化的 UPDATE / DELETE，调用方负责事务。表名列名都是本类调用方的常量。
    internal static class GlSql
    {
        const int AdInteger = 3;
        const int AdVarWChar = 202;
        const int AdParamInput = 1;

        public static void Exec(object conn, string sql, object[] args)
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
            object rs = null;
            try
            {
                ComUtil.Set(cmd, "ActiveConnection", conn);
                ComUtil.Set(cmd, "CommandText", sql);
                ComUtil.Set(cmd, "CommandTimeout", 45);
                for (int i = 0; i < args.Length; i++)
                {
                    AddOne(cmd, i, args[i]);
                }
                rs = ComUtil.Call(cmd, "Execute", new object[0]);
            }
            finally
            {
                ComUtil.Final(rs);
                ComUtil.Final(cmd);
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

        // 读出来的单元格都是字符串（Rows 的约定）。空值在行里缺席。
        public static string Col(Dictionary<string, object> row, string name)
        {
            object value;
            if (row == null || !row.TryGetValue(name, out value) || value == null)
            {
                return "";
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture).Trim();
        }

        public static bool Bit(Dictionary<string, object> row, string name)
        {
            string text = Col(row, name);
            return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        public static int Int(Dictionary<string, object> row, string name)
        {
            int value;
            int.TryParse(Col(row, name), NumberStyles.Integer, CultureInfo.InvariantCulture, out value);
            return value;
        }

        public static decimal Money(Dictionary<string, object> row, string name)
        {
            decimal value;
            decimal.TryParse(Col(row, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value);
            return decimal.Round(value, 2, MidpointRounding.AwayFromZero);
        }

        public static object Num(Dictionary<string, object> row, string name)
        {
            string text = Col(row, name);
            double value;
            if (text.Length == 0 || !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                return null;
            }
            return value;
        }

        // 期间、类别、凭证号四个参数，顺序与各 SQL 的 WHERE 一致。
        public static object[] KeyArgs(GlKey key)
        {
            return new object[] { key.Year, key.Period, key.Sign, key.No };
        }

        public static object[] With(object[] head, object[] tail)
        {
            object[] all = new object[head.Length + tail.Length];
            head.CopyTo(all, 0);
            tail.CopyTo(all, head.Length);
            return all;
        }
    }
}
