using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 许可采样的只读 SQL。连接串来自最近一次成功登录（LicenseState），不为采样专门登录 U8。
    // 连接和命令都只等 5 秒；两条 SQL 都没有调用方输入，也就没有参数。
    internal static class LicenseSql
    {
        const int TimeoutSeconds = 5;
        const int MaxRows = 500;

        const string CountSql = "SELECT cSub_Id, COUNT(DISTINCT cStation) FROM UFSYSTEM..UA_TaskLog WITH (NOLOCK)"
            + " GROUP BY cSub_Id";

        // 每个工作站一行，读许可总数（LicenseTotals）时从中挑本机那一行。
        const string SerialSql = "SELECT cStation, MIN(cStationSerial) FROM UFSYSTEM..UA_TaskLog WITH (NOLOCK)"
            + " WHERE cStationSerial LIKE N'%@%' GROUP BY cStation";

        public static object Open(string connectionString)
        {
            object conn = ComUtil.Create("ADODB.Connection");
            if (conn == null)
            {
                throw new InvalidOperationException("ADODB 未注册");
            }
            try
            {
                ComUtil.Set(conn, "ConnectionTimeout", TimeoutSeconds);
                ComUtil.Set(conn, "CommandTimeout", TimeoutSeconds);
                ComUtil.Call(conn, "Open", new object[] { connectionString });
                return conn;
            }
            catch
            {
                ComUtil.Final(conn);
                throw;
            }
        }

        // 子系统号 → UA_TaskLog 里登记的不同工作站数。只收两位大写字母的子系统号。
        public static Dictionary<string, int> StationCounts(object conn)
        {
            Dictionary<string, int> used = new Dictionary<string, int>(StringComparer.Ordinal);
            List<string[]> rows = Pairs(conn, CountSql);
            for (int i = 0; i < rows.Count; i++)
            {
                string sub = rows[i][0].Trim().ToUpperInvariant();
                int count;
                if (ConfigRules.SubCode(sub)
                    && int.TryParse(rows[i][1], NumberStyles.Integer, CultureInfo.InvariantCulture, out count))
                {
                    used[sub] = count;
                }
            }
            return used;
        }

        // 本机（cStation = 计算机名）的 cStationSerial，没有就返回 null。
        public static string StationSerial(object conn, string station)
        {
            List<string[]> rows = Pairs(conn, SerialSql);
            for (int i = 0; i < rows.Count; i++)
            {
                if (string.Equals(rows[i][0].Trim(), station, StringComparison.OrdinalIgnoreCase))
                {
                    return rows[i][1].Trim();
                }
            }
            return null;
        }

        // 只读前两列，值一律转成文本；NULL 当空串。
        static List<string[]> Pairs(object conn, string sql)
        {
            List<string[]> rows = new List<string[]>();
            object rs = ComUtil.Call(conn, "Execute", new object[] { sql });
            object fields = null;
            try
            {
                fields = ComUtil.Get(rs, "Fields");
                while (!Values.Flag(ComUtil.Get(rs, "EOF")) && rows.Count < MaxRows)
                {
                    rows.Add(new string[] { Cell(fields, 0), Cell(fields, 1) });
                    ComUtil.Call(rs, "MoveNext", new object[0]);
                }
                return rows;
            }
            finally
            {
                ComUtil.ReleaseOne(fields);
                CloseRs(rs);
            }
        }

        static string Cell(object fields, int index)
        {
            object field = null;
            try
            {
                field = ComUtil.Call(fields, "Item", new object[] { index });
                object value = ComUtil.Get(field, "Value");
                if (value == null || value is DBNull)
                {
                    return "";
                }
                IFormattable fmt = value as IFormattable;
                return fmt != null ? fmt.ToString(null, CultureInfo.InvariantCulture) : Convert.ToString(value);
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
