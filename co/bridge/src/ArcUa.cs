using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 只读档案 operator（U8 操作员）、role（角色）：纯 SQL，只用 ctx.Conn。表名列名都是常量，调用方的文本只进 ? 参数。
    // 数据在系统库，账套库连接上用三段名 UFSYSTEM..UA_*（同 PermLoad）：UA_User 操作员、UA_Group 角色、UA_Role 角色成员、
    // UA_HoldAuth 功能授权。口令、口令日期、邮箱手机、系统用户名等列一律不读。
    // 范围：只列本账套有授权的操作员（本人或所属角色在 UA_HoldAuth 里有本账套、请求年度或建账年度的任一行，年度窗口同 PermLoad），
    // 以及本账套有授权的角色；系统管理员（UA_User.iAdmin=1，即 admin）不列。不在范围内的编码 get 回 404。
    // 操作员关联的人员在账套库 UserHrPersonContro（cUser_Id → cPsn_Num，U8 视图 V_AA_UserHRPersonContro 同此关联），
    // 与登录对象 userToken 里的 cEmployeeId 是同一个人员编码。
    // 未覆盖：UA_User.nState 非 0 的取值含义（U8 自己的过程按 ISNULL(nState,0)=0 取正常操作员，这里非 0 一律当停用）；
    // UA_Group.cGroup_Id 的长度（UA_Role.cGroup_Id 是 nvarchar(20)）；iUserType 非 0 的操作员（U8 视图 v_UserRole 不列）照常列出。
    // 两张表都没有 rowversion：列表不支持 changed_since，watermark 为 null。数据权限：只按功能权限，不按记录过滤。
    // 权限：只认请求年度的账套主管（PermContext.Supervisor，按不晚于请求年度、最近一个有 admin 行的年度判断），
    // 在 Get / List 里显式查，不靠 PermRegistry 的 A("admin")：功能 id 按请求年度和建账年度两年取，某年收回的主管仍会命中。
    // PermRegistry 仍登记 admin 规则，供 PermGate 和 PermSelfTest。
    internal static class ArcUa
    {
        internal const string Operator = "operator";
        internal const string Role = "role";
        // UA_User.cUser_Id nvarchar(20)。
        internal const int CodeMax = 20;

        // ? 的顺序：账套号、请求年度、建账年度。
        const string Window = "a.cAcc_Id=? AND a.iYear IN (?, ?)";
        internal const string UserAuthed = " AND EXISTS (SELECT 1 FROM UFSYSTEM..UA_HoldAuth a WHERE " + Window
            + " AND ((a.iIsUser=1 AND a.cUser_Id=u.cUser_Id) OR (a.iIsUser=0 AND a.cUser_Id IN"
            + " (SELECT r0.cGroup_Id FROM UFSYSTEM..UA_Role r0 WHERE r0.cUser_Id=u.cUser_Id))))";
        internal const string RoleAuthed = " AND EXISTS (SELECT 1 FROM UFSYSTEM..UA_HoldAuth a WHERE " + Window
            + " AND a.iIsUser=0 AND a.cUser_Id=g.cGroup_Id)";
        const string OperatorCols = "RTRIM(u.cUser_Id) AS code, u.cUser_Name AS name, u.cDept AS dept,"
            + " CONVERT(varchar(6), ISNULL(u.nState, 0)) AS state, RTRIM(p.cPsn_Num) AS person_code, hp.cPsn_Name AS person_name"
            + " FROM UFSYSTEM..UA_User u"
            // 账套库与 UFSYSTEM 的排序规则可能不同，跨库比较按账套库的排序规则。
            + " OUTER APPLY (SELECT TOP 1 x.cPsn_Num FROM UserHrPersonContro x"
            + " WHERE x.cUser_Id=u.cUser_Id COLLATE DATABASE_DEFAULT ORDER BY x.cPsn_Num) p"
            + " LEFT JOIN hr_hi_person hp ON hp.cPsn_Num=p.cPsn_Num"
            + " WHERE ISNULL(u.iAdmin, 0)=0";
        const string RoleCols = "RTRIM(g.cGroup_Id) AS code, g.cGroup_Name AS name FROM UFSYSTEM..UA_Group g WHERE 1=1";
        // 一页操作员的角色（只取本账套有授权的角色），一页角色的成员（系统管理员除外）。IN 列表只有 ?。
        const string RolesOfSql = "SELECT RTRIM(g.cUser_Id) AS owner, RTRIM(g.cGroup_Id) AS code FROM UFSYSTEM..UA_Role g"
            + " WHERE 1=1" + RoleAuthed + " AND g.cUser_Id IN (";
        const string MembersSql = "SELECT RTRIM(r.cGroup_Id) AS owner, RTRIM(r.cUser_Id) AS code FROM UFSYSTEM..UA_Role r"
            + " JOIN UFSYSTEM..UA_User u ON u.cUser_Id=r.cUser_Id WHERE ISNULL(u.iAdmin, 0)=0 AND r.cGroup_Id IN (";

        internal static bool Is(ArcKind kind)
        {
            return kind != null && (kind.Name == Operator || kind.Name == Role);
        }

        public static ApiResult Get(WorkContext ctx, ArcReq req)
        {
            RequireSupervisor(ctx);
            UaSpec spec = UaSpec.Of(req.Kind);
            List<object> args = new List<object>();
            StringBuilder sql = Select(ctx, spec, args, 1);
            sql.Append(" AND ").Append(spec.Alias).Append(".").Append(spec.Key).Append("=?");
            args.Add(req.Code);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), 1);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            PermHook.Archive(ctx, req.Code);
            Dictionary<string, object> body = ArcRead.Head(req);
            body["fields"] = Items(ctx, spec, rows, 1)[0];
            return ApiResult.Ok(body);
        }

        public static ApiResult List(WorkContext ctx, ArcReq req)
        {
            RequireSupervisor(ctx);
            UaSpec spec = UaSpec.Of(req.Kind);
            string col = spec.Alias + "." + spec.Key;
            List<object> args = new List<object>();
            StringBuilder sql = Select(ctx, spec, args, req.Limit + 1);
            ArcRead.AddFilter(sql, args, " AND " + col + " > ?", req.After);
            ArcRead.AddFilter(sql, args, " AND " + col + " LIKE ? ESCAPE '\\'", ArcRead.Like(req.Prefix, false));
            ArcRead.AddFilter(sql, args, " AND " + spec.Alias + "." + spec.NameCol + " LIKE ? ESCAPE '\\'",
                ArcRead.Like(req.NameLike, true));
            PermHook.Where(sql, args, ctx, spec.Alias);
            sql.Append(" ORDER BY ").Append(col);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), req.Limit + 1);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["archive"] = req.Kind.Name;
            body["items"] = Items(ctx, spec, rows, req.Limit);
            body["next"] = rows.Count > req.Limit ? ArcRead.Cell(rows[req.Limit - 1], "code") : null;
            body["watermark"] = null;
            return ApiResult.Ok(body);
        }

        static void RequireSupervisor(WorkContext ctx)
        {
            if (!PermCheck.Of(ctx).Supervisor)
            {
                throw new BridgeException(403, "no_permission", "只有账套主管能读取操作员、角色");
            }
        }

        // SELECT TOP (?) … 加本账套授权条件；? 的顺序：TOP、账套号、两个年度。
        static StringBuilder Select(WorkContext ctx, UaSpec spec, List<object> args, int top)
        {
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ").Append(spec.Cols).Append(spec.Authed);
            args.Add(top);
            AddWindow(ctx, args);
            return sql;
        }

        static void AddWindow(WorkContext ctx, List<object> args)
        {
            PermContext p = PermCheck.Of(ctx);
            args.Add(p.Acc);
            args.Add(p.Year);
            args.Add(p.AcctYear);
        }

        static List<object> Items(WorkContext ctx, UaSpec spec, List<Dictionary<string, object>> rows, int limit)
        {
            List<Dictionary<string, object>> page = rows.GetRange(0, Math.Min(rows.Count, limit));
            Dictionary<string, List<object>> links = Links(ctx, spec, page);
            List<object> items = new List<object>();
            for (int i = 0; i < page.Count; i++)
            {
                Dictionary<string, object> item = spec.IsOperator ? OperatorItem(page[i]) : RoleItem(page[i]);
                List<object> list;
                string code = ArcRead.Cell(page[i], "code") ?? "";
                item[spec.LinkKey] = links.TryGetValue(code, out list) ? list : new List<object>();
                items.Add(item);
            }
            return items;
        }

        // 操作员 → 角色编码，角色 → 成员操作员编码；键按编码不分大小写（库的排序规则不分大小写）。
        static Dictionary<string, List<object>> Links(WorkContext ctx, UaSpec spec, List<Dictionary<string, object>> page)
        {
            Dictionary<string, List<object>> map = new Dictionary<string, List<object>>(StringComparer.OrdinalIgnoreCase);
            if (page.Count == 0)
            {
                return map;
            }
            List<object> args = new List<object>();
            StringBuilder sql = new StringBuilder(spec.IsOperator ? RolesOfSql : MembersSql);
            if (spec.IsOperator)
            {
                AddWindow(ctx, args);
            }
            for (int i = 0; i < page.Count; i++)
            {
                sql.Append(i == 0 ? "?" : ",?");
                args.Add(ArcRead.Cell(page[i], "code") ?? "");
            }
            sql.Append(") ORDER BY owner, code");
            foreach (Dictionary<string, object> row in Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), page.Count * 500))
            {
                string owner = ArcRead.Cell(row, "owner") ?? "";
                if (!map.ContainsKey(owner))
                {
                    map[owner] = new List<object>();
                }
                map[owner].Add(ArcRead.Cell(row, "code"));
            }
            return map;
        }

        static Dictionary<string, object> OperatorItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = ArcRead.Cell(row, "code");
            item["name"] = ArcRead.Cell(row, "name");
            item["dept"] = Blank(ArcRead.Cell(row, "dept"));
            // nState：0 正常，非 0 当停用（具体取值未经实测）。
            int state = 0;
            string text = ArcRead.Cell(row, "state");
            bool known = text != null && int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out state);
            item["state"] = known ? (object)state : null;
            item["disabled"] = known && state != 0;
            item["person_code"] = Blank(ArcRead.Cell(row, "person_code"));
            item["person_name"] = Blank(ArcRead.Cell(row, "person_name"));
            return item;
        }

        static Dictionary<string, object> RoleItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = ArcRead.Cell(row, "code");
            item["name"] = ArcRead.Cell(row, "name");
            return item;
        }

        static string Blank(string text)
        {
            return text == null || text.Trim().Length == 0 ? null : text;
        }

        // 两类档案的 SQL 片段：别名、主键列、名称列、列清单、本账套授权条件，以及成员列表的键名。
        sealed class UaSpec
        {
            public bool IsOperator;
            public string Alias;
            public string Key;
            public string NameCol;
            public string Cols;
            public string Authed;
            public string LinkKey;

            internal static UaSpec Of(ArcKind kind)
            {
                UaSpec s = new UaSpec();
                s.IsOperator = kind.Name == Operator;
                s.Alias = s.IsOperator ? "u" : "g";
                s.Key = s.IsOperator ? "cUser_Id" : "cGroup_Id";
                s.NameCol = s.IsOperator ? "cUser_Name" : "cGroup_Name";
                s.Cols = s.IsOperator ? OperatorCols : RoleCols;
                s.Authed = s.IsOperator ? UserAuthed : RoleAuthed;
                s.LinkKey = s.IsOperator ? "roles" : "members";
                return s;
            }
        }
    }
}
