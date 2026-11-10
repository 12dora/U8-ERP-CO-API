using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 销售订单、发货单的扩展自定义项（<表>_extradefine，表头 chdefine*、表体 cbdefine*）并进 vouchers/load 和预演 docs：
    // GetVoucherData / 主表 SELECT 不一定带这些列。列名转小写，已有同名键不覆盖；账套没有扩展表时什么也不加。
    // 表名、列名只来自下表并加方括号，id 走参数。
    internal static class SaleExtraRead
    {
        // {类型, 表头扩展表, 表头主键, 表体表, 表体扩展表, 表体主键, 表体外键}
        static readonly string[][] Tables = new string[][] {
            new string[] { "sale_order", "SO_SOMain_extradefine", "ID", "SO_SODetails", "SO_SODetails_extradefine", "iSOsID", "ID" },
            new string[] { "dispatch", "DispatchList_extradefine", "DLID", "DispatchLists", "DispatchLists_extradefine", "iDLsID", "DLID" }
        };
        const string ExistsSql = "SELECT CASE WHEN OBJECT_ID(N'dbo.{0}', N'U') IS NULL THEN '0' ELSE '1' END AS ok";
        const string ColumnsSql = "SELECT LOWER(COLUMN_NAME) AS c FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=?";

        internal static string[] Find(VoucherKind kind)
        {
            for (int i = 0; kind != null && i < Tables.Length; i++)
            {
                if (Tables[i][0] == kind.Name)
                {
                    return Tables[i];
                }
            }
            return null;
        }

        // lines 的元素是 Dictionary<string, object>（load 的行或预演的行）。
        internal static void Merge(object conn, VoucherKind kind, int id, Dictionary<string, object> head, IList lines)
        {
            string[] t = Find(kind);
            if (t == null)
            {
                return;
            }
            if (head != null && Exists(conn, t[1]))
            {
                string sql = "SELECT * FROM " + Q(t[1]) + " WHERE " + Q(t[2]) + "=?";
                MergeRow(head, Rows.One(conn, sql, new object[] { id }), t[2]);
            }
            if (lines != null && lines.Count > 0 && Exists(conn, t[4]))
            {
                MergeLines(conn, t, id, lines);
            }
        }

        // vouchers/load：约定字段里的主表列（SalePins.BaseColumns）组件 DOM 可能不带（模板没有、桥在保存后补写的列），
        // 按库里实有的列另查，只并进组件已返回的表头和表体行（按表体主键），不加行；已有非空值不动。
        internal static void MergeBase(object conn, VoucherKind kind, int id, Dictionary<string, object> head, IList lines)
        {
            string[] t = Find(kind);
            if (t == null)
            {
                return;
            }
            string headSql = BaseSql(conn, SalePins.BaseColumns(kind.Name, true), t[2], t[2]);
            if (head != null && headSql != null)
            {
                MergeRow(head, Rows.One(conn, headSql, new object[] { id }), t[2]);
            }
            string lineSql = BaseSql(conn, SalePins.BaseColumns(kind.Name, false), t[5], t[6]);
            if (lines != null && lines.Count > 0 && lineSql != null)
            {
                MergeByLine(lines, Rows.Query(conn, lineSql, new object[] { id }, 5000), t[5]);
            }
        }

        // SELECT [主键], [列] AS [请求字段] … FROM [表] WHERE [外键]=?；表里一列都没有时 null。纯拼接，列名只来自 SalePins.Spec。
        static string BaseSql(object conn, List<string[]> cols, string key, string fk)
        {
            if (cols.Count == 0)
            {
                return null;
            }
            HashSet<string> have = Columns(conn, cols[0][0]);
            List<string> parts = new List<string>();
            for (int i = 0; i < cols.Count; i++)
            {
                if (have.Contains(cols[i][1].ToLowerInvariant()))
                {
                    parts.Add(Q(cols[i][1]) + " AS " + Q(cols[i][2]));
                }
            }
            if (parts.Count == 0)
            {
                return null;
            }
            return "SELECT " + Q(key) + ", " + string.Join(", ", parts.ToArray()) + " FROM " + Q(cols[0][0])
                + " WHERE " + Q(fk) + "=?";
        }

        static HashSet<string> Columns(object conn, string table)
        {
            HashSet<string> set = new HashSet<string>(StringComparer.Ordinal);
            List<Dictionary<string, object>> rows = Rows.Query(conn, ColumnsSql, new object[] { table }, 1000);
            for (int i = 0; i < rows.Count; i++)
            {
                set.Add(CoRows.Col(rows[i], "c"));
            }
            return set;
        }

        static void MergeLines(object conn, string[] t, int id, IList lines)
        {
            string sql = "SELECT x.* FROM " + Q(t[4]) + " x WHERE x." + Q(t[5]) + " IN (SELECT b." + Q(t[5])
                + " FROM " + Q(t[3]) + " b WHERE b." + Q(t[6]) + "=?)";
            MergeByLine(lines, Rows.Query(conn, sql, new object[] { id }, 5000), t[5]);
        }

        // 按表体主键把 rows 并进组件返回的 lines；lines 里没有的行不加。
        static void MergeByLine(IList lines, List<Dictionary<string, object>> rows, string key)
        {
            Dictionary<int, Dictionary<string, object>> byLine = new Dictionary<int, Dictionary<string, object>>();
            for (int i = 0; i < rows.Count; i++)
            {
                int line = CoRows.AsId(Value(rows[i], key));
                if (line > 0)
                {
                    byLine[line] = rows[i];
                }
            }
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                Dictionary<string, object> extra;
                if (line != null && byLine.TryGetValue(CoRows.AsId(Value(line, key)), out extra))
                {
                    MergeRow(line, extra, key);
                }
            }
        }

        // 纯函数（--selftest 用）：extra 的列（主键除外）以小写名并进 target；target 已有非空值（键不分大小写）的不动，
        // 已有空值的键换成扩展表的值（键名照 target 原样）。
        internal static void MergeRow(Dictionary<string, object> target, Dictionary<string, object> extra, string keyColumn)
        {
            if (target == null || extra == null)
            {
                return;
            }
            foreach (KeyValuePair<string, object> pair in extra)
            {
                if (string.Equals(pair.Key, keyColumn, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                string have = KeyIn(target, pair.Key);
                if (have == null)
                {
                    target[pair.Key.ToLowerInvariant()] = pair.Value;
                }
                else if (Convert.ToString(target[have]).Trim().Length == 0)
                {
                    target[have] = pair.Value;
                }
            }
        }

        static string KeyIn(Dictionary<string, object> row, string name)
        {
            foreach (string key in row.Keys)
            {
                if (string.Equals(key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return key;
                }
            }
            return null;
        }

        static object Value(Dictionary<string, object> row, string name)
        {
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pair.Value;
                }
            }
            return null;
        }

        static bool Exists(object conn, string table)
        {
            return Rows.Scalar(conn, string.Format(ExistsSql, table), new object[0]) == "1";
        }

        static string Q(string name)
        {
            return "[" + name + "]";
        }
    }
}
