using System;
using System.Collections.Generic;
using System.Data.Common;
using System.Data.SqlClient;
using System.Globalization;

namespace U8Co
{
    // 记账事务里桥自己的读写（SqlClient）。每次开一条连接、用完就关，同一时刻不开两条：在 TransactionScope 里依次打开的
    // 同串连接复用池里已登记到事务的那一条，事务保持轻量，不升级 MSDTC。SQL 只用参数，表名列名是常量。
    internal static class GlPostSql
    {
        const int TimeoutSeconds = 120;

        // 登录对象的 UfDbName（经 AdoCreds，OLE DB 格式）换成 SqlClient 连接串：去掉 Provider 和 SqlClient 不认的键，
        // 按 SqlConnectionStringBuilder 规范化。U8 的 InternalConnectionString 也用它规范化，U8 的 DAL 和桥拿到的是同一个串、
        // 同一个连接池。要用 U8 自己的库账号（服务账户的 Windows 登录不够：记账会跨库读 UFSystem）。串里有口令，不记日志。
        public static string Text(string oledb)
        {
            DbConnectionStringBuilder source = new DbConnectionStringBuilder();
            try
            {
                source.ConnectionString = oledb ?? "";
            }
            catch (ArgumentException)
            {
                throw new BridgeException(500, "internal", "UfDbName 无法解析");
            }
            SqlConnectionStringBuilder sql = new SqlConnectionStringBuilder();
            foreach (string key in source.Keys)
            {
                if (string.Equals(key, "provider", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                Copy(sql, key, source[key]);
            }
            sql.Pooling = true;
            sql.Enlist = true;
            if (sql.DataSource.Length == 0 || sql.InitialCatalog.Length == 0)
            {
                throw new BridgeException(500, "internal", "UfDbName 缺少服务器或数据库");
            }
            return AsU8(sql.ConnectionString);
        }

        // 照 U8 的 InternalConnectionString：用这个串初始化 SqlConnectionStringBuilder 再设 MinPoolSize = 1，取 ConnectionString。
        // 桥交给 U8 的就是这个结果，U8 再做一遍得到同一个串（池按串区分，差一个键就会另开连接、升级 MSDTC）。
        static string AsU8(string text)
        {
            SqlConnectionStringBuilder u8 = new SqlConnectionStringBuilder(text);
            u8.MinPoolSize = 1;
            return u8.ConnectionString;
        }

        // OLE DB 专有的键（Use Procedure for Prepare、Auto Translate 等）SqlClient 不认，跳过。
        static void Copy(SqlConnectionStringBuilder sql, string key, object value)
        {
            try
            {
                sql[key] = value;
            }
            catch (ArgumentException)
            {
            }
            catch (KeyNotFoundException)
            {
            }
            catch (FormatException)
            {
            }
            catch (InvalidCastException)
            {
            }
        }

        // 失败按 GlPostErr.Own 归类：死锁、锁超时、执行超时、事务已中止 503，其余 500（桥自己的 SQL，不算 U8 拒绝）。
        public static List<object[]> Rows(string conn, string sql, SqlParameter[] args)
        {
            try
            {
                return Read(conn, sql, args);
            }
            catch (Exception ex)
            {
                throw GlPostErr.Own(ex);
            }
        }

        public static int Exec(string conn, string sql, SqlParameter[] args)
        {
            try
            {
                return Write(conn, sql, args);
            }
            catch (Exception ex)
            {
                throw GlPostErr.Own(ex);
            }
        }

        static List<object[]> Read(string conn, string sql, SqlParameter[] args)
        {
            List<object[]> rows = new List<object[]>();
            using (SqlConnection c = new SqlConnection(conn))
            {
                c.Open();
                using (SqlCommand cmd = Command(c, sql, args))
                {
                    using (SqlDataReader reader = cmd.ExecuteReader())
                    {
                        while (reader.Read())
                        {
                            object[] values = new object[reader.FieldCount];
                            reader.GetValues(values);
                            rows.Add(values);
                        }
                    }
                }
            }
            return rows;
        }

        static int Write(string conn, string sql, SqlParameter[] args)
        {
            using (SqlConnection c = new SqlConnection(conn))
            {
                c.Open();
                using (SqlCommand cmd = Command(c, sql, args))
                {
                    return cmd.ExecuteNonQuery();
                }
            }
        }

        static SqlCommand Command(SqlConnection c, string sql, SqlParameter[] args)
        {
            SqlCommand cmd = new SqlCommand(sql, c);
            cmd.CommandTimeout = TimeoutSeconds;
            if (args != null)
            {
                cmd.Parameters.AddRange(args);
            }
            return cmd;
        }

        // 用属性赋值，避免 new SqlParameter(name, 0) 落到 SqlDbType 的重载上。null 写 DBNull。
        public static SqlParameter P(string name, object value)
        {
            SqlParameter p = new SqlParameter();
            p.ParameterName = name;
            p.Value = value ?? DBNull.Value;
            return p;
        }

        public static int Int(object value)
        {
            if (value == null || value is DBNull)
            {
                return 0;
            }
            return Convert.ToInt32(value, CultureInfo.InvariantCulture);
        }

        // ibook 是 tinyint，可空。
        public static int? Book(object value)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            return Convert.ToInt32(value, CultureInfo.InvariantCulture);
        }

        public static string Str(object value, bool keepNull)
        {
            if (value == null || value is DBNull)
            {
                return keepNull ? null : "";
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }
    }
}
