using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // archives/resolve 的分档查询：每档一条 SELECT TOP (limit+1)，按编码排序。? 按在 SQL 里出现的顺序进 args：
    // TOP、停用标志（include_disabled 时在选择列里）、From、Where、本档条件、停用过滤、数据权限（PermSql，放在最后）。
    // 解析一项时用到的全部上下文：查询表达式、请求选项、登录日期、权限快照和该档案的读取规则。
    internal sealed class ResolveRun
    {
        public ResolveSrc Src;
        public ArcResolveReq Req;
        public string Date;
        public PermContext Perm;
        public PermRule Rule;
    }

    internal static class ArcResolveSql
    {
        internal const int TierCode = 0;
        internal const int TierName = 1;
        internal const int TierAbbr = 2;
        internal const int TierMnem = 3;
        internal const int TierContains = 4;
        internal static readonly string[] TierNames = new string[] { "code", "name", "abbr", "mnemonic", "contains" };

        // 这类档案有没有这一档（简称、助记码只有部分档案有）。
        internal static bool Has(ResolveSrc s, int tier)
        {
            if (tier == TierAbbr)
            {
                return s.Abbr != null;
            }
            if (tier == TierMnem)
            {
                return s.Mnem != null || s.AddCode != null;
            }
            return true;
        }

        internal static string Build(ResolveRun run, int tier, string q, List<object> args)
        {
            ResolveSrc s = run.Src;
            ArcResolveReq req = run.Req;
            string date = run.Date;
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ");
            args.Add(req.Limit + 1);
            sql.Append(s.Code).Append(" AS code, ").Append(s.Name).Append(" AS name");
            Col(sql, s.Class, "class_code");
            Col(sql, s.Abbr, "abbr");
            Col(sql, s.Spec, "spec");
            Col(sql, s.Unit, "unit");
            Col(sql, s.Mnem, "mnem");
            if (req.IncludeDisabled && s.HasDisabled)
            {
                sql.Append(", CASE WHEN ").Append(s.DisabledSql(args, date)).Append(" THEN '1' ELSE '0' END AS disabled");
            }
            sql.Append(" FROM ").Append(s.From);
            args.AddRange(s.FromArgs);
            sql.Append(" WHERE 1=1").Append(s.Where);
            args.AddRange(s.WhereArgs);
            sql.Append(" AND ").Append(Cond(s, tier, q, args));
            if (!req.IncludeDisabled && s.HasDisabled)
            {
                sql.Append(" AND (CASE WHEN ").Append(s.DisabledSql(args, date)).Append(" THEN 1 ELSE 0 END)=0");
            }
            // 记录级数据权限（AA_HoldAuth）：与 archives/list 同一段条件（PermHook.Where → PermSql.AppendRule）。
            PermSql.AppendRule(sql, args, run.Perm, run.Rule, s.Alias);
            // 按第一列（编码）排序，不依赖列别名。
            sql.Append(" ORDER BY 1");
            return sql.ToString();
        }

        static void Col(StringBuilder sql, string expr, string alias)
        {
            if (expr != null)
            {
                sql.Append(", ").Append(expr).Append(" AS ").Append(alias);
            }
        }

        static string Cond(ResolveSrc s, int tier, string q, List<object> args)
        {
            switch (tier)
            {
                case TierCode:
                    return CodeCond(s, q, args);
                case TierName:
                    return Eq(s.Name, q, args);
                case TierAbbr:
                    return Eq(s.Abbr, q, args);
                case TierMnem:
                    return MnemCond(s, q, args);
                default:
                    return ContainsCond(s, q, args);
            }
        }

        static string CodeCond(ResolveSrc s, string q, List<object> args)
        {
            if (s.BareCode == null)
            {
                return Eq(s.Code, q, args);
            }
            return "(" + Eq(s.Code, q, args) + " OR " + Eq(s.BareCode, q, args) + ")";
        }

        static string Eq(string expr, string q, List<object> args)
        {
            args.Add(q);
            return expr + "=?";
        }

        // 助记码不分大小写；存货代码与助记码同一档。
        static string MnemCond(ResolveSrc s, string q, List<object> args)
        {
            List<string> parts = new List<string>();
            if (s.Mnem != null)
            {
                parts.Add("UPPER(" + s.Mnem + ")=UPPER(?)");
                args.Add(q);
            }
            if (s.AddCode != null)
            {
                parts.Add("UPPER(" + s.AddCode + ")=UPPER(?)");
                args.Add(q);
            }
            return "(" + string.Join(" OR ", parts.ToArray()) + ")";
        }

        // 名称包含；客户、供应商另比简称，存货另比「名称 + 规格型号」。LIKE 转义用 ArcRead.Like。
        static string ContainsCond(ResolveSrc s, string q, List<object> args)
        {
            string like = ArcRead.Like(q, true);
            List<string> exprs = new List<string>();
            exprs.Add(s.Name);
            if (s.Abbr != null)
            {
                exprs.Add(s.Abbr);
            }
            exprs.AddRange(s.Joined);
            StringBuilder sb = new StringBuilder("(");
            for (int i = 0; i < exprs.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append(" OR ");
                }
                sb.Append(exprs[i]).Append(" LIKE ? ESCAPE '\\'");
                args.Add(like);
            }
            return sb.Append(")").ToString();
        }
    }
}
