using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using Microsoft.Win32;

namespace U8Co
{
    // 模板缓存的两种指纹。任何异常都向上抛，调用方不用缓存。
    // 数据库对象指纹：sys.dm_exec_describe_first_result_set(模板 SQL, NULL, 1) 的整张元数据表（列序、列名、类型、长度、
    // 精度、排序规则、可空、键列、隐藏列、来源表和来源列）拼成 XML 后在桥里算 SHA-256。直接描述模板 SQL 本身，
    // 视图改了定义、底表加减列、类型变化都会反映出来，不用解析 SQL 找对象名，也不用 CHECKSUM 这种 32 位弱校验。
    // 同一批次再取服务器、库名（含账套号和年度）、登录名、缺省架构，作为缓存键的一部分；有未提交事务时不用缓存。
    internal static class TplFingerprint
    {
        const string ProbeSql = "select @@SERVERNAME as s, db_name() as d, suser_sname() as u, schema_name() as sc, "
            + "convert(varchar(12), @@TRANCOUNT) as tc, convert(nvarchar(max), (select "
            + "column_ordinal as o, name as n, system_type_name as t, max_length as l, precision as p, scale as c, "
            + "collation_name as k, is_nullable as z, is_identity_column as i, is_part_of_unique_key as q, "
            + "is_updateable as w, is_computed_column as m, is_hidden as h, source_schema as ss, source_table as st, "
            + "source_column as sn, error_number as e "
            + "from sys.dm_exec_describe_first_result_set(?, NULL, 1) order by column_ordinal for xml raw('c'))) as f";

        // 返回 null：这次不用缓存（有事务、描述失败），但不算故障。
        public static TplProbe Probe(object conn, string sql)
        {
            if (conn == null || sql == null || sql.Length == 0 || sql.Length > 4000)
            {
                return null;
            }
            Dictionary<string, object> row = Rows.One(conn, ProbeSql, new object[] { sql });
            if (row == null || Cell(row, "tc") != "0")
            {
                return null;
            }
            string meta = Cell(row, "f");
            // FOR XML RAW 省略 NULL 属性：出现 e= 说明 SQL Server 描述失败。
            if (meta.Length == 0 || meta.IndexOf(" e=\"", StringComparison.Ordinal) >= 0)
            {
                return null;
            }
            TplProbe probe = new TplProbe();
            probe.Key = Cell(row, "s") + "\n" + Cell(row, "d") + "\n" + Cell(row, "u") + "\n" + Cell(row, "sc") + "\n" + sql;
            probe.SchemaFp = Crypto.Hex(Crypto.Sha256(Encoding.UTF8.GetBytes(meta)));
            probe.ComponentFp = TplComponent.Fingerprint();
            return probe;
        }

        static string Cell(Dictionary<string, object> row, string name)
        {
            object value;
            if (!row.TryGetValue(name, out value))
            {
                return "";
            }
            return Values.Text(value);
        }
    }

    // 组件指纹：ADODB.Recordset、MSXML2.DOMDocument 的进程内服务器 DLL 的 FileVersion + 修改时间 + 大小。
    // ProgID → InprocServer32 路径每个进程解析一次；文件状态最多 60 秒重查一次。
    internal static class TplComponent
    {
        static readonly string[] ProgIds = new string[] { "ADODB.Recordset", "MSXML2.DOMDocument" };
        const int RecheckSeconds = 60;

        static readonly object Gate = new object();
        static string[] _paths;
        static string _fp;
        static DateTime _checkedUtc = DateTime.MinValue;

        public static string Fingerprint()
        {
            lock (Gate)
            {
                DateTime now = DateTime.UtcNow;
                if (_fp != null && now >= _checkedUtc && now - _checkedUtc < TimeSpan.FromSeconds(RecheckSeconds))
                {
                    return _fp;
                }
                _fp = null;
                if (_paths == null)
                {
                    _paths = ResolveAll();
                }
                string fp = StatAll(_paths);
                _fp = fp;
                _checkedUtc = now;
                return fp;
            }
        }

        static string[] ResolveAll()
        {
            string[] paths = new string[ProgIds.Length];
            for (int i = 0; i < ProgIds.Length; i++)
            {
                paths[i] = ServerPath(ProgIds[i]);
            }
            return paths;
        }

        static string StatAll(string[] paths)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < paths.Length; i++)
            {
                FileInfo file = new FileInfo(paths[i]);
                if (!file.Exists)
                {
                    throw new InvalidOperationException("组件文件不存在");
                }
                string version = FileVersionInfo.GetVersionInfo(file.FullName).FileVersion;
                sb.Append(file.FullName).Append('|').Append(version ?? "").Append('|')
                    .Append(file.LastWriteTimeUtc.Ticks.ToString(CultureInfo.InvariantCulture)).Append('|')
                    .Append(file.Length.ToString(CultureInfo.InvariantCulture)).Append('\n');
            }
            return sb.ToString();
        }

        // 32 位进程读 HKCR 时系统自动走 WOW6432Node，与 CreateObject 看到的是同一份注册。
        static string ServerPath(string progId)
        {
            string clsid = ClsidOf(progId);
            if (clsid == null)
            {
                string cur = DefaultValue(progId + "\\CurVer");
                clsid = cur == null ? null : ClsidOf(cur);
            }
            if (clsid == null)
            {
                throw new InvalidOperationException("找不到 " + progId + " 的 CLSID");
            }
            string path = DefaultValue("CLSID\\" + clsid + "\\InprocServer32");
            if (path == null)
            {
                throw new InvalidOperationException("找不到 " + progId + " 的 InprocServer32");
            }
            path = Environment.ExpandEnvironmentVariables(path.Trim().Trim('"'));
            if (!Path.IsPathRooted(path))
            {
                throw new InvalidOperationException(progId + " 的 InprocServer32 不是绝对路径");
            }
            return path;
        }

        static string ClsidOf(string progId)
        {
            return DefaultValue(progId + "\\CLSID");
        }

        static string DefaultValue(string subKey)
        {
            using (RegistryKey key = Registry.ClassesRoot.OpenSubKey(subKey))
            {
                if (key == null)
                {
                    return null;
                }
                string value = key.GetValue("") as string;
                if (value == null || value.Trim().Length == 0)
                {
                    return null;
                }
                return value.Trim();
            }
        }
    }
}
