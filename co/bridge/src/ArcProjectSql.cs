using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 项目档案写入的 SQL。表名只由 ArcProject.Table 从数据库里校验过的大类拼出，调用方的文本只进参数。
    // 调用方负责事务（ArcProjectWrite）；带锁的读都用 UPDLOCK, HOLDLOCK，锁到提交；删除时项目行用 XLOCK（见 Count）。
    internal static class ArcProjectSql
    {
        // 与 ArcProject 的可用大类同一条件，另要求分类表 fitemss<大类>class 存在。
        const string ClassSql = "SELECT f.citem_class AS cls FROM fitem f WITH (UPDLOCK, HOLDLOCK)"
            + " WHERE f.citem_class=? AND f.ctable = N'fitemss' + f.citem_class"
            + " AND OBJECT_ID(N'dbo.' + f.ctable, N'U') IS NOT NULL"
            + " AND OBJECT_ID(N'dbo.' + f.ctable + N'class', N'U') IS NOT NULL";
        // 项目结构（fitemstructure）里除标准栏目外的栏目或子表行（iSubItem<>0）：如现金流量项目的方向、项目管理的完工日期和子表。
        // 这类大类的项目 U8 还要写别的值或别的表，桥不写（review 4b P2-1）。
        const string StructSql = "SELECT TOP 1 cfield_name AS col FROM fitemstructure WHERE citem_class=?"
            + " AND (ISNULL(iSubItem, 0) <> 0 OR cfield_name NOT IN"
            + " (N'I_id', N'citemcode', N'citemname', N'bclose', N'citemccode', N'iotherused'))";
        const string ExtraMsg = "该项目大类有额外栏目或子表，请在 U8 客户端维护";
        // 兜底：除编码、名称、所属分类外，不允许为空、没有缺省值的列（自增、计算列、rowversion 除外）。
        const string ExtraSql = "SELECT TOP 1 c.name AS col FROM sys.columns c WHERE c.object_id = OBJECT_ID(?, N'U')"
            + " AND c.is_nullable = 0 AND c.is_identity = 0 AND c.is_computed = 0 AND c.default_object_id = 0"
            + " AND c.system_type_id <> 189 AND c.name NOT IN (N'citemcode', N'citemname', N'citemccode')";
        const string RowCols = "SELECT citemcode AS citemcode, citemname AS citemname, bclose AS bclose, citemccode AS citemccode FROM ";

        // 锁住大类（fitem 行）并返回项目表名；大类不存在或不可用 404。
        internal static string LockClass(object conn, string code)
        {
            string cls = ArcProject.Split(code, "code")[0];
            string stored = Rows.Scalar(conn, ClassSql, new object[] { cls });
            if (stored == null || !ArcProject.IsClass(stored) || string.Equals(stored, "ch", StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(404, "not_found", "项目大类不存在：" + cls);
            }
            return ArcProject.Table(stored);
        }

        // 新增、修改都查：表名是 LockClass 返回的 fitemss<大类>。
        internal static void RequireNoExtra(object conn, string table)
        {
            string cls = table.Substring("fitemss".Length);
            string col = Rows.Scalar(conn, StructSql, new object[] { cls });
            if (col == null)
            {
                col = Rows.Scalar(conn, ExtraSql, new object[] { "dbo." + table });
            }
            if (col != null)
            {
                throw new BridgeException(409, "state_mismatch", ExtraMsg + "（" + col + "）");
            }
        }

        // 所属分类必须存在且是末级（fitemss<大类>class.bItemCend=1）。
        internal static void RequireLeaf(object conn, string table, string classCode)
        {
            string sql = "SELECT cItemCcode AS c FROM " + table + "class WITH (UPDLOCK, HOLDLOCK) WHERE cItemCcode=? AND bItemCend=1";
            if (Rows.One(conn, sql, new object[] { classCode }) == null)
            {
                throw ArcReq.Bad("所属分类 " + classCode + " 不存在或不是末级分类");
            }
        }

        // 带锁读一行；编码比较按库的排序规则（不分大小写）。
        internal static Dictionary<string, object> Lock(object conn, string table, string item)
        {
            return Rows.One(conn, RowCols + table + " WITH (UPDLOCK, HOLDLOCK) WHERE citemcode=?", new object[] { item });
        }

        internal static Dictionary<string, object> Read(object conn, string table, string item)
        {
            return Rows.One(conn, RowCols + table + " WHERE citemcode=?", new object[] { item });
        }

        // 删除用：带锁数出该编码的行（编码不分大小写，理应恰好一行），删除后在同一事务里再数一次（应为 0）。
        // 带锁时用 XLOCK, ROWLOCK, HOLDLOCK：从查引用到提交，别的连接按加锁的已提交读读不到、也改不了这一行（剩余窗口见 ArcProjectDel）。
        internal static int Count(object conn, string table, string item, bool locked)
        {
            string hint = locked ? " WITH (XLOCK, ROWLOCK, HOLDLOCK)" : "";
            string sql = "SELECT COUNT(*) AS n FROM " + table + hint + " WHERE citemcode=?";
            return int.Parse(Rows.Scalar(conn, sql, new object[] { item }) ?? "0", System.Globalization.CultureInfo.InvariantCulture);
        }

        internal static void Delete(object conn, string table, string item)
        {
            GlSql.Exec(conn, "DELETE FROM " + table + " WHERE citemcode=?", new object[] { item });
        }

        internal static void Insert(object conn, string table, string item, ArcBag fields)
        {
            string sql = "INSERT INTO " + table + " (citemcode, citemname, bclose, citemccode) VALUES (?, ?, ?, ?)";
            object[] args = new object[] { item, fields.Get("name"), Bit(fields.Get("bclose")), fields.Get("citemccode") };
            GlSql.Exec(conn, sql, args);
        }

        // 只改调用方给的列；列名来自固定标签表（ArcKind.SqlMap）。
        internal static void Update(object conn, string table, string item, ArcReq req)
        {
            StringBuilder sql = new StringBuilder("UPDATE ").Append(table).Append(" SET ");
            List<object> args = new List<object>();
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string column = req.Map.Column(tags[i]);
                if (i > 0)
                {
                    sql.Append(", ");
                }
                sql.Append(column).Append("=?");
                string value = req.Fields.Get(tags[i]);
                args.Add(column == "bclose" ? (object)Bit(value) : value);
            }
            sql.Append(" WHERE citemcode=?");
            args.Add(item);
            GlSql.Exec(conn, sql.ToString(), args.ToArray());
        }

        static int Bit(string value)
        {
            return value == "1" ? 1 : 0;
        }
    }
}
