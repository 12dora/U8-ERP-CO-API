using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 从数据库读出一个 PermContext（功能、记录级数据、字段权限）。只用传入的连接（读线程是 ctx.Conn），全是只读 SQL，操作员只进参数。
    // 功能权限在 UFSYSTEM（UA_HoldAuth / UA_Role / UA_Account），数据权限在账套库（AA_*）。
    internal static class PermLoad
    {
        const int MaxCodes = 200000;
        const string RoleSql = "SELECT r.cGroup_Id FROM UFSYSTEM..UA_Role r WHERE r.cUser_Id=?";

        const string AcctYearSql = "SELECT TOP 1 CONVERT(varchar(10), a.iYear) AS y FROM UFSYSTEM..UA_Account a"
            + " WHERE a.cAcc_Id=?";

        // 功能 id：业务授权挂在建账年度（UA_Account.iYear）下，请求年度（账套库年度）的授权也认。
        // 登录日期由调用方给，不参与年度窗口。请求年度这一项让结果是旧 GlState.Permit 的超集。
        const string FuncSql = "SELECT DISTINCT h.cAuth_Id FROM UFSYSTEM..UA_HoldAuth h WHERE h.cAcc_Id=?"
            + " AND h.iYear IN (?, ?)"
            + " AND ((h.iIsUser=1 AND h.cUser_Id=?) OR (h.iIsUser=0 AND h.cUser_Id IN (" + RoleSql + ")))";

        // 账套主管：只看本账套不晚于请求年度、最近一个有 admin 行的年度（请求年度有 admin 行就只看请求年度），
        // 在那一年持有 admin / Admin 才算。某年收回的账套主管不会因为早年的行仍算主管。
        const string SupervisorSql = "SELECT CASE WHEN EXISTS (SELECT 1 FROM UFSYSTEM..UA_HoldAuth h WHERE h.cAcc_Id=?"
            + " AND h.cAuth_Id IN (N'admin', N'Admin') AND h.iYear=(SELECT MAX(m.iYear) FROM UFSYSTEM..UA_HoldAuth m"
            + " WHERE m.cAcc_Id=? AND m.cAuth_Id IN (N'admin', N'Admin') AND m.iYear<=?)"
            + " AND ((h.iIsUser=1 AND h.cUser_Id=?) OR (h.iIsUser=0 AND h.cUser_Id IN (" + RoleSql + "))))"
            + " THEN N'1' ELSE N'0' END AS s";

        const string RolesSql = "SELECT DISTINCT LTRIM(RTRIM(r.cGroup_Id)) AS g FROM UFSYSTEM..UA_Role r WHERE r.cUser_Id=?";

        // 总账选项「明细账查询权限控制到科目」。
        const string GlSubjSql = "SELECT TOP 1 i.cValue FROM AccInformation i WHERE i.cSysID=N'GL' AND i.cName=N'bQryCtlSubj'";

        const string SwitchSql = "SELECT DISTINCT b.cBusObId FROM AA_BusObject_base b"
            + " WHERE b.langid=N'zh-CN' AND b.iAuthType=0 AND b.bAuthControl=1";

        const string AdminSql = "SELECT DISTINCT h.CBusObId FROM AA_holdBusobject h WHERE h.iAuthType=0 AND h.iAdmin=1"
            + " AND ((h.isUserGroup=0 AND h.cUserId=?) OR (h.isUserGroup=1 AND h.cUserId IN (" + RoleSql + ")))";

        // 与 PermSql.HoldExists 的条件保持一致。
        const string CodeSql = "SELECT DISTINCT LTRIM(RTRIM(a.cACCode)) AS code, LTRIM(RTRIM(ISNULL(a.cClassCode, N''))) AS cls"
            + " FROM AA_HoldAuth a WHERE a.cBusObId=? AND ISNULL(a.cFuncId, N'') LIKE N'%R%'"
            + " AND NULLIF(LTRIM(RTRIM(a.cACCode)), N'') IS NOT NULL"
            + " AND ((a.isUserGroup=0 AND a.cUserId=?) OR (a.isUserGroup=1 AND a.cUserId IN (" + RoleSql + ")))";

        // 字段权限：拒绝行只在 iAuthType=1、开关打开的对象上生效；本人行与所属角色行一起取，合并在 MergeColumns 里做。
        // g：1 = 角色行；n：1 = 拒绝（cFuncID 含 N）。
        // 按二进制排序固定行序：同一字段在本人行、角色行里大小写不同时，存下的写法（先到者）每次相同，快照指纹不抖动。
        const string ColumnSql = "SELECT LTRIM(RTRIM(a.cKey)) AS k, LTRIM(RTRIM(a.cFld)) AS f,"
            + " CASE WHEN a.isUserGroup=1 THEN N'1' ELSE N'0' END AS g,"
            + " CASE WHEN ISNULL(a.cFuncID, N'') LIKE N'%N%' THEN N'1' ELSE N'0' END AS n"
            + " FROM AA_ColumnAuth a WHERE a.cKey IN (SELECT b.cBusObId FROM AA_BusObject_base b"
            + " WHERE b.langid=N'zh-CN' AND b.iAuthType=1 AND b.bAuthControl=1)"
            + " AND ((a.isUserGroup=0 AND a.cUserId=?) OR (a.isUserGroup=1 AND a.cUserId IN (" + RoleSql + ")))"
            + " ORDER BY a.isUserGroup, a.cKey COLLATE Latin1_General_BIN, a.cFld COLLATE Latin1_General_BIN,"
            + " a.cUserId COLLATE Latin1_General_BIN";

        // 字段权限行数上限：超过时不截断（截断会少遮字段），整个快照失败。
        internal const int MaxColumnRows = 20000;

        public static PermContext Load(object conn, string acc, int year, int dateYear, string op)
        {
            PermContext p = new PermContext();
            p.Acc = acc ?? "";
            p.Operator = op ?? "";
            p.Year = year;
            p.DateYear = dateYear;
            p.AcctYear = AcctYear(conn, p.Acc, year);
            LoadFuncs(conn, p);
            object[] sup = new object[] { p.Acc, p.Acc, p.Year, p.Operator, p.Operator };
            p.Supervisor = Rows.Scalar(conn, SupervisorSql, sup) == "1";
            if (!p.Supervisor)
            {
                LoadRoles(conn, p);
                p.GlSubjCtl = Values.Flag(Rows.Scalar(conn, GlSubjSql, null));
                LoadData(conn, p);
                LoadColumns(conn, p);
            }
            p.LoadedTicks = DateTime.UtcNow.Ticks;
            return p;
        }

        static int AcctYear(object conn, string acc, int fallback)
        {
            Dictionary<string, object> row = Rows.One(conn, AcctYearSql, new object[] { acc });
            int y;
            if (row != null && int.TryParse(Values.Text(First(row)).Trim(), NumberStyles.Integer,
                CultureInfo.InvariantCulture, out y) && y > 1900)
            {
                return y;
            }
            return fallback;
        }

        static void LoadFuncs(object conn, PermContext p)
        {
            object[] args = new object[] { p.Acc, p.Year, p.AcctYear, p.Operator, p.Operator };
            List<Dictionary<string, object>> rows = Rows.Query(conn, FuncSql, args, MaxCodes);
            for (int i = 0; i < rows.Count; i++)
            {
                string code = Values.Text(First(rows[i])).Trim();
                if (code.Length > 0)
                {
                    p.Funcs.Add(code);
                }
            }
        }

        static void LoadRoles(object conn, PermContext p)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, RolesSql, new object[] { p.Operator }, 1000);
            for (int i = 0; i < rows.Count; i++)
            {
                string role = Values.Text(First(rows[i])).Trim();
                if (role.Length > 0)
                {
                    p.Roles.Add(role);
                }
            }
        }

        // 开关每次建条目都重读。user（制单人）与 gzauth（工资）不用于读取，不取编码。
        static void LoadData(object conn, PermContext p)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, SwitchSql, null, 1000);
            for (int i = 0; i < rows.Count; i++)
            {
                string obj = PermObj.Normalize(Values.Text(First(rows[i])));
                if (obj.Length > 0)
                {
                    p.On.Add(obj);
                }
            }
            if (p.On.Count == 0)
            {
                return;
            }
            rows = Rows.Query(conn, AdminSql, new object[] { p.Operator, p.Operator }, 1000);
            for (int i = 0; i < rows.Count; i++)
            {
                p.DataAdmin.Add(PermObj.Normalize(Values.Text(First(rows[i]))));
            }
            foreach (string obj in p.On)
            {
                if (p.DataAdmin.Contains(obj) || PermObj.NotForRead(obj) || !p.Controls(obj))
                {
                    continue;
                }
                LoadCodes(conn, p, obj);
            }
        }

        // 人员的开关是 person，授权行常挂在 hr_hi_person 上：person 下没有行时改取 hr_hi_person。
        static void LoadCodes(object conn, PermContext p, string obj)
        {
            HashSet<string> set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string source = obj;
            ReadCodes(conn, p, obj, obj, set);
            if (set.Count == 0 && obj == PermObj.Person)
            {
                source = PermObj.HrPerson;
                ReadCodes(conn, p, obj, source, set);
            }
            p.Codes[obj] = set;
            p.SourceOf[obj] = source;
        }

        static void ReadCodes(object conn, PermContext p, string obj, string source, HashSet<string> set)
        {
            object[] args = new object[] { source, p.Operator, p.Operator };
            List<Dictionary<string, object>> rows = Rows.Query(conn, CodeSql, args, MaxCodes);
            for (int i = 0; i < rows.Count; i++)
            {
                string code = Values.Text(Col(rows[i], "code")).Trim();
                if (code.Length == 0)
                {
                    continue;
                }
                if (obj == PermObj.Item)
                {
                    set.Add(Values.Text(Col(rows[i], "cls")).Trim() + PermContext.PairSep + code);
                }
                else
                {
                    set.Add(code);
                }
            }
        }

        static void LoadColumns(object conn, PermContext p)
        {
            object[] args = new object[] { p.Operator, p.Operator };
            List<Dictionary<string, object>> rows = Rows.Query(conn, ColumnSql, args, MaxColumnRows + 1);
            if (rows.Count > MaxColumnRows)
            {
                throw new BridgeException(500, "internal", "字段权限行数超过上限");
            }
            MergeColumns(p, rows);
        }

        // U8 的合并规则：本人拒绝 ∪ 所属角色拒绝；本人在同一（对象、字段）上有不含 N 的行时，角色的拒绝不算；
        // 本人已拒绝时角色不能放开。角色上不含 N 的行不起作用。
        internal static void MergeColumns(PermContext p, List<Dictionary<string, object>> rows)
        {
            Dictionary<string, bool> user = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            List<string[]> role = new List<string[]>();
            for (int i = 0; i < rows.Count; i++)
            {
                SplitColumn(rows[i], user, role);
            }
            foreach (KeyValuePair<string, bool> u in user)
            {
                if (u.Value)
                {
                    int cut = u.Key.IndexOf(PermContext.PairSep);
                    AddColumn(p, u.Key.Substring(0, cut), u.Key.Substring(cut + 1));
                }
            }
            for (int i = 0; i < role.Count; i++)
            {
                bool deny;
                if (!user.TryGetValue(role[i][0] + PermContext.PairSep + role[i][1], out deny) || deny)
                {
                    AddColumn(p, role[i][0], role[i][1]);
                }
            }
        }

        // 一行分到本人（对象-字段 → 是否拒绝；同一对重复时拒绝优先）或角色拒绝。空对象、空字段跳过。
        static void SplitColumn(Dictionary<string, object> row, Dictionary<string, bool> user, List<string[]> role)
        {
            string key = Values.Text(Col(row, "k")).Trim();
            string fld = Values.Text(Col(row, "f")).Trim();
            if (key.Length == 0 || fld.Length == 0)
            {
                return;
            }
            bool deny = Values.Text(Col(row, "n")).Trim() == "1";
            if (Values.Text(Col(row, "g")).Trim() == "1")
            {
                if (deny)
                {
                    role.Add(new string[] { key, fld });
                }
                return;
            }
            string pair = key + PermContext.PairSep + fld;
            bool had;
            user[pair] = deny || (user.TryGetValue(pair, out had) && had);
        }

        static void AddColumn(PermContext p, string key, string fld)
        {
            HashSet<string> set;
            if (!p.Columns.TryGetValue(key, out set))
            {
                set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                p.Columns[key] = set;
            }
            set.Add(fld);
        }

        static object First(Dictionary<string, object> row)
        {
            foreach (object value in row.Values)
            {
                return value;
            }
            return null;
        }

        static object Col(Dictionary<string, object> row, string name)
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
    }
}
