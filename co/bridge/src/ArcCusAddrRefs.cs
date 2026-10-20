using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一张可能引用收货地址编码的表。名字全部来自 INFORMATION_SCHEMA，CusCol 为 null 表示该表没有客户列。
    internal sealed class ArcCusAddrRefTable
    {
        public string Schema;
        public string Table;
        public string AddrCol;
        public string CusCol;
    }

    // 改收货地址编码前的引用检查：库里任何有 cAddCode 列的表（CusDeliverAdd 自己除外）只要还有行用着旧编码就拒绝。
    // U8 单据把收货地址编码存成文本列，没有外键，所以运行时按元数据找表；表名列名只取自 INFORMATION_SCHEMA 且加方括号，值走参数。
    internal static class ArcCusAddrRefs
    {
        const string OwnTable = "CusDeliverAdd";
        const int CatalogMax = 20000;
        const int ShowMax = 20;
        const string CatalogSql = "SELECT c.TABLE_SCHEMA AS sch, c.TABLE_NAME AS tbl,"
            + " c.COLUMN_NAME AS col, c.DATA_TYPE AS typ"
            + " FROM INFORMATION_SCHEMA.COLUMNS c JOIN INFORMATION_SCHEMA.TABLES t"
            + " ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME"
            + " WHERE t.TABLE_TYPE='BASE TABLE' AND LOWER(c.COLUMN_NAME) IN ('caddcode','ccuscode')"
            + " ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION";

        // 在调用方的事务和连接上查，返回仍引用 address 的表名（去重、按元数据顺序）。
        public static List<string> Hits(object conn, string customer, string address)
        {
            List<Dictionary<string, object>> catalog = Rows.Query(conn, CatalogSql, new object[0], CatalogMax);
            if (catalog.Count >= CatalogMax)
            {
                throw new BridgeException(500, "internal", "收货地址引用检查的元数据过多，无法确认");
            }
            List<string> hits = new List<string>();
            foreach (ArcCusAddrRefTable t in Targets(catalog))
            {
                object[] args = t.CusCol == null ? new object[] { address } : new object[] { address, customer };
                if (Rows.One(conn, Sql(t), args) != null)
                {
                    hits.Add(t.Table);
                }
            }
            return hits;
        }

        public static string Message(List<string> tables)
        {
            List<string> shown = tables.Count > ShowMax ? tables.GetRange(0, ShowMax) : tables;
            string more = tables.Count > ShowMax ? " 等 " + tables.Count + " 张表" : "";
            return "收货地址已被单据引用，不能改编码：" + string.Join("、", shown.ToArray()) + more;
        }

        // 元数据行（sch, tbl, col, typ）按表归并：要有文本类型的 cAddCode 列；客户列只在它是文本列时才用来过滤。
        internal static List<ArcCusAddrRefTable> Targets(List<Dictionary<string, object>> catalog)
        {
            List<ArcCusAddrRefTable> list = new List<ArcCusAddrRefTable>();
            Dictionary<string, ArcCusAddrRefTable> byName = new Dictionary<string, ArcCusAddrRefTable>();
            foreach (Dictionary<string, object> row in catalog)
            {
                string sch = GlSql.Col(row, "sch");
                string tbl = GlSql.Col(row, "tbl");
                string col = GlSql.Col(row, "col");
                if (!IsText(GlSql.Col(row, "typ")) || string.Equals(tbl, OwnTable, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                string key = sch + "." + tbl;
                ArcCusAddrRefTable t;
                if (!byName.TryGetValue(key, out t))
                {
                    t = new ArcCusAddrRefTable();
                    t.Schema = sch;
                    t.Table = tbl;
                    byName[key] = t;
                    list.Add(t);
                }
                bool isAddr = string.Equals(col, "caddcode", StringComparison.OrdinalIgnoreCase);
                if (isAddr && t.AddrCol == null)
                {
                    t.AddrCol = col;
                }
                else if (!isAddr && t.CusCol == null)
                {
                    t.CusCol = col;
                }
            }
            list.RemoveAll(delegate(ArcCusAddrRefTable x) { return x.AddrCol == null; });
            return list;
        }

        // SELECT TOP 1 1 FROM [sch].[tbl] WHERE [addr]=? [AND [cus]=?]；名字含 ] 的不可能是正常表，直接拒绝。
        internal static string Sql(ArcCusAddrRefTable t)
        {
            string sql = "SELECT TOP 1 1 FROM " + Quote(t.Schema) + "." + Quote(t.Table) + " WHERE " + Quote(t.AddrCol) + "=?";
            if (t.CusCol != null)
            {
                sql += " AND " + Quote(t.CusCol) + "=?";
            }
            return sql;
        }

        internal static string Quote(string name)
        {
            if (string.IsNullOrEmpty(name) || name.IndexOf(']') >= 0)
            {
                throw new BridgeException(500, "internal", "引用检查遇到无法安全引用的表名或列名");
            }
            return "[" + name + "]";
        }

        static bool IsText(string type)
        {
            string t = (type ?? "").ToLowerInvariant();
            return t == "nvarchar" || t == "varchar" || t == "nchar" || t == "char";
        }
    }
}
