using System;
using System.Collections.Generic;

namespace U8Co
{
    // 分管部门闸门和 Customer_Auth 同步：新部门须存在、末级、未停用；改 cCusDepart 时按 U8 客户卡片
    // 维护 Privilege_Type=0 的部门维度行。autoId 是 IDENTITY，插入不写该列。
    internal static class ArcPersonAuth
    {
        const string DeptSql = "SELECT LTRIM(RTRIM(ISNULL(cDepCode,''))) AS code,"
            + " CONVERT(varchar(1), ISNULL(bDepEnd, 0)) AS leaf,"
            + " CONVERT(varchar(10), dDepEndDate, 23) AS ended"
            + " FROM Department WHERE cDepCode=?";
        const string LockSql = "SELECT LTRIM(RTRIM(ISNULL(Privilege_ID,''))) AS priv"
            + " FROM Customer_Auth WITH (UPDLOCK, ROWLOCK)"
            + " WHERE Account_ID=? AND Privilege_Type=0";
        const string UpdateSql = "UPDATE Customer_Auth SET Privilege_ID=?"
            + " WHERE Account_ID=? AND Privilege_Type=0";
        const string InsertSql = "INSERT INTO Customer_Auth (Account_ID, Privilege_ID, Privilege_Type)"
            + " VALUES (?, ?, 0)";
        // 回读核对全部部门维度行（重复行时 UPDATE 会改每一行，不能只看第一行）。
        const string CheckSql = "SELECT COUNT(*) AS n, SUM(CASE WHEN LTRIM(RTRIM(ISNULL(Privilege_ID,'')))=? THEN 1 ELSE 0 END) AS ok"
            + " FROM Customer_Auth WHERE Account_ID=? AND Privilege_Type=0";

        internal static string NoteMissing(string dept)
        {
            return "分管部门不存在：" + dept;
        }

        internal static string NoteNotLeaf(string dept)
        {
            return "分管部门不是末级：" + dept;
        }

        internal static string NoteEnded(string dept)
        {
            return "分管部门已停用：" + dept;
        }

        // 空串表示可用。调用方只在 FollowDept 为真（新部门非空）时调用。
        internal static string RejectNote(object conn, string dept, string date)
        {
            Dictionary<string, object> row = Rows.One(conn, DeptSql, new object[] { dept });
            if (row == null)
            {
                return NoteMissing(dept);
            }
            if (Cell(row, "leaf") != "1")
            {
                return NoteNotLeaf(dept);
            }
            string ended = Cell(row, "ended");
            if (date != null && date.Length > 0 && ended.Length > 0
                && string.CompareOrdinal(ended, date) <= 0)
            {
                return NoteEnded(dept);
            }
            return "";
        }

        internal static void Apply(object conn, string code, string dept)
        {
            Dictionary<string, object> row = Rows.One(conn, LockSql, new object[] { code });
            if (row == null)
            {
                GlSql.Exec(conn, InsertSql, new object[] { code, dept });
                return;
            }
            GlSql.Exec(conn, UpdateSql, new object[] { dept, code });
        }

        internal static bool Matches(object conn, string code, string dept)
        {
            Dictionary<string, object> row = Rows.One(conn, CheckSql, new object[] { (dept ?? "").Trim(), code });
            string n = row == null ? "0" : Cell(row, "n");
            return n != "0" && n == Cell(row, "ok");
        }

        static string Cell(Dictionary<string, object> row, string name)
        {
            return (GlSql.Col(row, name) ?? "").Trim();
        }

        static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }
    }
}
