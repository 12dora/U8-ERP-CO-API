using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // archives/resolve 查一类档案用到的表达式。表名列名只来自 ArcKind 和这里的常量（列名按 U8 表结构），
    // 调用方的文本只进 ? 参数。From 带别名 Alias；FromArgs / WhereArgs 按在 SQL 里出现的顺序排。
    internal sealed class ResolveSrc
    {
        public string Alias = "h";
        public string From;
        public readonly List<object> FromArgs = new List<object>();
        public string Where = "";
        public readonly List<object> WhereArgs = new List<object>();
        public string Code;
        // 项目：编码也可以只写项目编码（不带大类），第一档同时比这一列。
        public string BareCode;
        public string Name;
        public string Class;
        public string Abbr;
        public string Mnem;
        public string AddCode;
        public string Spec;
        public string Unit;
        // 停用：EndDate 是日期列（不晚于登录日期即停用），Closed 是直接的条件；两者至多一个。
        public string EndDate;
        public string Closed;
        // 「包含」一档另外比的表达式（存货：名称 + 规格型号）。
        public string[] Joined = new string[0];
        // 项目一个大类都没有：不查库，全部 none。
        public bool Empty;

        public bool HasDisabled
        {
            get { return EndDate != null || Closed != null; }
        }

        // 停用条件；日期比较按 yyyy-MM-dd 字符串，不受 DATEFORMAT 影响，NULL 视为未停用。
        public string DisabledSql(List<object> args, string date)
        {
            if (EndDate != null)
            {
                args.Add(date);
                return "CONVERT(varchar(10), " + EndDate + ", 23) <= ?";
            }
            return Closed;
        }
    }

    internal static class ArcResolveSrc
    {
        public static ResolveSrc Of(WorkContext ctx, ArcKind kind, PermContext p)
        {
            if (ArcKindRo.IsProject(kind))
            {
                return Project(ctx);
            }
            if (ArcUa.Is(kind))
            {
                return Ua(kind, p);
            }
            ResolveSrc s = Plain(kind);
            Extras(s, kind.Name);
            // 科目：只列登录年度（会计年度）的末级科目，同 archives/list 的年度条件。
            if (kind.YearCol != null)
            {
                s.Where = " AND h.bend=1 AND h." + kind.YearCol + "=?";
                s.WhereArgs.Add(GlState.LoginYear(ctx));
                s.Closed = "h.bclose=1";
            }
            return s;
        }

        static ResolveSrc Plain(ArcKind kind)
        {
            ResolveSrc s = new ResolveSrc();
            s.From = kind.Table + " h";
            s.Code = "h." + kind.Key;
            s.Name = "h." + kind.NameCol;
            s.Class = kind.ClassCol == null ? null : "h." + kind.ClassCol;
            return s;
        }

        // 简称、助记码、存货代码、规格、主计量单位、停用日期（列名按 U8 表结构）。
        static void Extras(ResolveSrc s, string name)
        {
            switch (name)
            {
                case "customer":
                    Partner(s, "h.cCusAbbName", "h.cCusMnemCode");
                    return;
                case "vendor":
                    Partner(s, "h.cVenAbbName", "h.cVenMnemCode");
                    return;
                case "inventory":
                    s.Mnem = "h.cInvMnemCode";
                    s.AddCode = "h.cInvAddCode";
                    s.Spec = "h.cInvStd";
                    s.Unit = "h.cComUnitCode";
                    s.EndDate = "h.dEDate";
                    s.Joined = new string[]
                    {
                        "(h.cInvName + ISNULL(h.cInvStd, N''))", "(h.cInvName + N' ' + ISNULL(h.cInvStd, N''))"
                    };
                    return;
                case "department":
                    s.EndDate = "h.dDepEndDate";
                    return;
                case "warehouse":
                    s.EndDate = "h.dWhEndDate";
                    return;
            }
        }

        static void Partner(ResolveSrc s, string abbr, string mnem)
        {
            s.Abbr = abbr;
            s.Mnem = mnem;
            s.EndDate = "h.dEndDate";
        }

        // 项目：全部大类 UNION ALL（同 ArcProject.List），编码 "<大类>:<编码>"；bclose=1 视为停用。
        static ResolveSrc Project(WorkContext ctx)
        {
            ResolveSrc s = new ResolveSrc();
            List<string> classes = Classes(ctx);
            s.Empty = classes.Count == 0;
            StringBuilder from = new StringBuilder("(");
            for (int i = 0; i < classes.Count; i++)
            {
                if (i > 0)
                {
                    from.Append(" UNION ALL ");
                }
                from.Append("SELECT CAST(? AS nvarchar(2)) AS cls, citemcode, citemname, citemccode, bclose FROM ");
                from.Append(ArcProject.Table(classes[i]));
                s.FromArgs.Add(classes[i]);
            }
            s.From = from.Append(") h").ToString();
            s.Code = "(h.cls + ':' + h.citemcode)";
            s.BareCode = "h.citemcode";
            s.Name = "h.citemname";
            s.Class = "h.citemccode";
            s.Closed = "h.bclose=1";
            return s;
        }

        // 大类超过 100 个时 ArcProject 的 400 带 field project_class，resolve 没有这个字段：换成不带 field 的 400。
        static List<string> Classes(WorkContext ctx)
        {
            try
            {
                return ArcProject.Classes(ctx.Conn, null);
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 400)
                {
                    throw;
                }
                throw new BridgeException(400, "bad_request", "项目大类超过 100 个，无法按名称解析项目", null,
                    "请用 archives/list 按 project_class 逐个大类查");
            }
        }

        // 操作员、角色（系统库）：范围同 ArcUa（本账套有授权、不含系统管理员）；操作员 nState 非 0 视为停用。
        static ResolveSrc Ua(ArcKind kind, PermContext p)
        {
            ResolveSrc s = new ResolveSrc();
            bool op = kind.Name == ArcUa.Operator;
            s.Alias = op ? "u" : "g";
            s.From = op ? "UFSYSTEM..UA_User u" : "UFSYSTEM..UA_Group g";
            s.Where = op ? " AND ISNULL(u.iAdmin, 0)=0" + ArcUa.UserAuthed : ArcUa.RoleAuthed;
            s.WhereArgs.Add(p.Acc);
            s.WhereArgs.Add(p.Year);
            s.WhereArgs.Add(p.AcctYear);
            s.Code = op ? "RTRIM(u.cUser_Id)" : "RTRIM(g.cGroup_Id)";
            s.Name = op ? "u.cUser_Name" : "g.cGroup_Name";
            s.Closed = op ? "ISNULL(u.nState, 0)<>0" : null;
            return s;
        }
    }
}
